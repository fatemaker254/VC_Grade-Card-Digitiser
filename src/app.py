"""
Local web front end for the marksheet pipeline.

Run with:
    python app.py
Then open http://127.0.0.1:5000 in a browser, drag in the master PDF, and
watch it process. Finished Excel files are written to OUTPUT_DIR on disk
(same as running main.py directly).

After extraction, anything the pipeline couldn't fully verify on its own -
two-pass disagreements it couldn't auto-resolve, marks that exceed full
marks, an SGPA that doesn't match credit points / credits, a Credit Points
value that doesn't match this college's own learned grade scale - goes to
a review queue. The review screen shows the flagged field next to the
actual scanned page, pre-filled with the pipeline's best guess, so a human
only has to confirm or correct - not read the whole page and retype
everything. Excel files are only written once every flagged item has been
looked at.
"""
import os
import subprocess
import sys
import threading
import uuid
from collections import defaultdict
from pathlib import Path

from flask import Flask, jsonify, render_template, request, send_file

BASE_DIR = Path(__file__).parent
sys.path.insert(0, str(BASE_DIR / "src"))

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from openai import OpenAI
from pdf_split import split_pdf_to_images
from gpt_extract import extract_marksheet
from reconcile import reconcile
from validate import validate_student, normalize_fail_credit_points
from grade_scale import learn_grade_scale, apply_grade_scale_check
from config import load_subject_map, save_subject_map, semester_folder_name, subject_for_student
from build_excel import write_subject_workbook

UPLOAD_DIR = BASE_DIR / "input"
OUTPUT_DIR = BASE_DIR / "output"
CACHE_DIR = BASE_DIR / "cache"
SUBJECT_MAP_PATH = BASE_DIR / "subject_map.json"
UPLOAD_DIR.mkdir(exist_ok=True)
OUTPUT_DIR.mkdir(exist_ok=True)

app = Flask(__name__)
# job_id -> {status, percent, log, records, review_items, files, error, ...}
JOBS = {}


def _log(job_id, line):
    JOBS[job_id]["log"].append(line)
    print(line)


def _num_or_str(v):
    return v


def run_pipeline(job_id: str, pdf_path: Path, single_pass: bool, dpi: int, verify_dpi: int):
    job = JOBS[job_id]
    try:
        client = OpenAI()
        pages_dir = CACHE_DIR / "pages"
        gpt_cache_dir = CACHE_DIR / "gpt_json"
        pages_hires_dir = CACHE_DIR / "pages_hires"
        gpt_cache_verify_dir = CACHE_DIR / "gpt_json_verify"

        _log(job_id, "Splitting PDF into page images ...")
        image_paths = split_pdf_to_images(str(pdf_path), str(pages_dir), dpi=dpi)
        hires_paths = [] if single_pass else split_pdf_to_images(str(pdf_path), str(pages_hires_dir), dpi=verify_dpi)
        job["total_pages"] = len(image_paths)
        _log(job_id, f"{len(image_paths)} page(s) found")

        records, failed = [], []
        for i, img_path in enumerate(image_paths, start=1):
            try:
                pass1 = extract_marksheet(img_path, client, cache_dir=str(gpt_cache_dir))
                if single_pass:
                    final, recon_items = pass1, []
                    final["_review_notes"] = []
                else:
                    pass2 = extract_marksheet(hires_paths[i - 1], client, cache_dir=str(gpt_cache_verify_dir))
                    final, notes, recon_items = reconcile(pass1, pass2)
                    final["_review_notes"] = notes
                final["_recon_items"] = recon_items
                final["_display_image"] = hires_paths[i - 1] if not single_pass else img_path
                records.append(final)
                flag = f" - {len(recon_items)} field(s) to review" if recon_items else ""
                _log(job_id, f"[{i}/{len(image_paths)}] {final.get('student_name', '?')} "
                              f"({final.get('registration_no', '?')}){flag}")
            except Exception as e:
                failed.append((img_path, str(e)))
                _log(job_id, f"[{i}/{len(image_paths)}] FAILED: {e}")
            job["percent"] = int(i / len(image_paths) * 80)

        _log(job_id, "Cross-checking arithmetic and this college's grade scale ...")
        grade_scale = learn_grade_scale(records)
        job["grade_scale"] = grade_scale
        if grade_scale:
            _log(job_id, "Learned grade scale: " +
                 ", ".join(f"{g}={p:g}" for g, p in sorted(grade_scale.items())))

        review_items = []
        for idx, rec in enumerate(records):
            fix_notes = normalize_fail_credit_points(rec)
            for n in fix_notes:
                _log(job_id, f"  auto-corrected: {n}")
            v_notes, v_items = validate_student(rec)
            g_notes, g_items = apply_grade_scale_check(rec, grade_scale)
            all_items = list(rec.get("_recon_items", [])) + v_items + g_items
            for item in all_items:
                review_items.append({
                    "id": uuid.uuid4().hex,
                    "record_index": idx,
                    "student_name": rec.get("student_name", ""),
                    "registration_no": rec.get("registration_no", ""),
                    **item,
                    "resolved": False,
                })
        job["percent"] = 90
        job["records"] = records
        job["review_items"] = review_items
        job["failed"] = len(failed)

        if review_items:
            _log(job_id, f"{len(review_items)} field(s) across {len(records)} page(s) need a quick human check.")
            job["status"] = "needs_review"
            job["percent"] = 95
        else:
            _log(job_id, "Nothing flagged - writing Excel files directly.")
            finalize_job(job_id)
    except Exception as e:
        job["status"] = "error"
        job["error"] = str(e)
        _log(job_id, f"Pipeline error: {e}")


def finalize_job(job_id: str):
    """Groups records into semester/subject workbooks and writes them to disk."""
    job = JOBS[job_id]
    records = job["records"]

    subject_map = load_subject_map(str(SUBJECT_MAP_PATH))
    groups = defaultdict(list)
    for rec in records:
        semester = semester_folder_name(rec.get("semester_roman", ""), rec.get("exam_title", ""))
        subject, _ = subject_for_student(rec, subject_map)
        groups[(semester, subject)].append(rec)
    save_subject_map(str(SUBJECT_MAP_PATH), subject_map)

    written = []
    for (semester, subject), group_records in groups.items():
        out_path = OUTPUT_DIR / semester / f"{subject}.xlsx"
        write_subject_workbook(group_records, subject, str(out_path))
        written.append(str(out_path.relative_to(OUTPUT_DIR)))
        _log(job_id, f"Wrote {out_path.relative_to(OUTPUT_DIR)} ({len(group_records)} student(s))")

    job["files"] = written
    job["percent"] = 100
    job["status"] = "done"
    _log(job_id, "Done.")


def _apply_correction(record: dict, field: str, new_value, course_code: str):
    """Writes a human-confirmed/corrected value back into the record at the
    right nested location, given the field key used by validate.py /
    reconcile.py / grade_scale.py."""
    if course_code == "Grand Total":
        record[field] = new_value
        return

    course = next((c for c in record.get("courses", []) if c.get("course_code") == course_code), None)
    if course is None:
        return

    if field.startswith("component:"):
        _, comp_name, subfield = field.split(":", 2)
        comp = next((c for c in course.get("components", []) if c.get("name") == comp_name), None)
        if comp is not None:
            comp[subfield] = new_value
    else:
        course[field] = new_value


@app.route("/")
def index():
    return render_template("index.html", output_dir=str(OUTPUT_DIR))


@app.route("/process", methods=["POST"])
def process():
    f = request.files.get("pdf")
    if not f or not f.filename.lower().endswith(".pdf"):
        return jsonify({"error": "Please upload a PDF file."}), 400

    single_pass = request.form.get("single_pass") == "true"
    dest = UPLOAD_DIR / f"{uuid.uuid4().hex[:8]}_{f.filename}"
    f.save(dest)

    job_id = uuid.uuid4().hex
    JOBS[job_id] = {"status": "running", "percent": 0, "log": [], "files": [],
                     "total_pages": 0, "records": [], "review_items": []}
    threading.Thread(target=run_pipeline, args=(job_id, dest, single_pass, 220, 350), daemon=True).start()
    return jsonify({"job_id": job_id})


@app.route("/status/<job_id>")
def status(job_id):
    job = JOBS.get(job_id)
    if not job:
        return jsonify({"error": "unknown job"}), 404
    pending = sum(1 for it in job.get("review_items", []) if not it["resolved"])
    return jsonify({k: v for k, v in job.items() if k != "records"} | {"pending_review": pending})


@app.route("/review/<job_id>")
def review_page(job_id):
    if job_id not in JOBS:
        return "Unknown job", 404
    return render_template("review.html", job_id=job_id)


@app.route("/review/<job_id>/items")
def review_items(job_id):
    job = JOBS.get(job_id)
    if not job:
        return jsonify({"error": "unknown job"}), 404
    items = [it for it in job.get("review_items", []) if not it["resolved"]]
    for it in items:
        it["image_url"] = f"/review/{job_id}/image/{it['record_index']}"
    return jsonify({"items": items, "total": len(job.get("review_items", [])),
                     "remaining": len(items)})


@app.route("/review/<job_id>/image/<int:record_index>")
def review_image(job_id, record_index):
    job = JOBS.get(job_id)
    if not job or record_index >= len(job["records"]):
        return "Not found", 404
    path = job["records"][record_index].get("_display_image")
    if not path or not Path(path).exists():
        return "Not found", 404
    return send_file(path)


@app.route("/review/<job_id>/submit", methods=["POST"])
def review_submit(job_id):
    job = JOBS.get(job_id)
    if not job:
        return jsonify({"error": "unknown job"}), 404

    data = request.get_json()
    item_id, new_value = data.get("id"), data.get("value")
    item = next((it for it in job["review_items"] if it["id"] == item_id), None)
    if item is None:
        return jsonify({"error": "unknown item"}), 404

    record = job["records"][item["record_index"]]
    _apply_correction(record, item["field"], new_value, item["course_code"])
    item["resolved"] = True
    item["current_value"] = new_value

    remaining = sum(1 for it in job["review_items"] if not it["resolved"])
    return jsonify({"ok": True, "remaining": remaining})


@app.route("/review/<job_id>/finish", methods=["POST"])
def review_finish(job_id):
    job = JOBS.get(job_id)
    if not job:
        return jsonify({"error": "unknown job"}), 404
    remaining = sum(1 for it in job["review_items"] if not it["resolved"])
    if remaining > 0:
        return jsonify({"error": f"{remaining} item(s) still unresolved"}), 400
    finalize_job(job_id)
    return jsonify({"ok": True})


@app.route("/open-output", methods=["POST"])
def open_output():
    """Opens the output folder in the OS file explorer, cross-platform."""
    path = str(OUTPUT_DIR)
    try:
        if sys.platform.startswith("win"):
            os.startfile(path)  # noqa
        elif sys.platform == "darwin":
            subprocess.run(["open", path])
        else:
            subprocess.run(["xdg-open", path])
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


if __name__ == "__main__":
    app.run(debug=False, port=5000)
