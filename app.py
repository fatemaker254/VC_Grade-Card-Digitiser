"""
Local web front end for the marksheet pipeline.

Run with:
    python app.py
Then open http://127.0.0.1:5000 in a browser, drag in the master PDF, and
watch it process. Finished Excel files are written to OUTPUT_DIR on disk
(same as running main.py directly) - this just adds a friendlier way to
kick that off and watch progress, for Vivekananda College staff who'd
rather not use the command line.

This file intentionally does NOT modify main.py or anything in src/ - it
calls the same pipeline modules directly so both the CLI and the web UI
stay in sync automatically.
"""
import os
import subprocess
import sys
import threading
import time
import uuid
from collections import defaultdict
from pathlib import Path

from flask import Flask, jsonify, render_template, request, send_from_directory

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
from config import load_subject_map, save_subject_map, semester_folder_name, subject_for_student
from build_excel import write_subject_workbook

UPLOAD_DIR = BASE_DIR / "input"
OUTPUT_DIR = BASE_DIR / "output"
CACHE_DIR = BASE_DIR / "cache"
SUBJECT_MAP_PATH = BASE_DIR / "subject_map.json"
UPLOAD_DIR.mkdir(exist_ok=True)
OUTPUT_DIR.mkdir(exist_ok=True)

app = Flask(__name__)
JOBS = {}  # job_id -> {status, percent, log: [...], files: [...], error}


def _log(job, line):
    JOBS[job]["log"].append(line)
    print(line)


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
                    final, final["_review_notes"] = pass1, []
                else:
                    pass2 = extract_marksheet(hires_paths[i - 1], client, cache_dir=str(gpt_cache_verify_dir))
                    final, notes = reconcile(pass1, pass2)
                    final["_review_notes"] = notes
                records.append(final)
                flag = f" - {len(final['_review_notes'])} field(s) to review" if final.get("_review_notes") else ""
                _log(job_id, f"[{i}/{len(image_paths)}] {final.get('student_name', '?')} "
                              f"({final.get('registration_no', '?')}){flag}")
            except Exception as e:
                failed.append((img_path, str(e)))
                _log(job_id, f"[{i}/{len(image_paths)}] FAILED: {e}")
            job["percent"] = int(i / len(image_paths) * 90)

        _log(job_id, "Grouping students by semester and subject ...")
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
        job["failed"] = len(failed)
        job["percent"] = 100
        job["status"] = "done"
        _log(job_id, "Done.")
    except Exception as e:
        job["status"] = "error"
        job["error"] = str(e)
        _log(job_id, f"Pipeline error: {e}")


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
    JOBS[job_id] = {"status": "running", "percent": 0, "log": [], "files": [], "total_pages": 0}
    threading.Thread(target=run_pipeline, args=(job_id, dest, single_pass, 220, 350), daemon=True).start()
    return jsonify({"job_id": job_id})


@app.route("/status/<job_id>")
def status(job_id):
    job = JOBS.get(job_id)
    if not job:
        return jsonify({"error": "unknown job"}), 404
    return jsonify(job)


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
