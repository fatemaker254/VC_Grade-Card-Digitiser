"""
Run the whole pipeline: master PDF -> per-page images -> GPT-4o extraction
(x2, independently, for cross-checking) -> reconciliation -> validation ->
Semester_X/Subject.xlsx files.

USAGE:
    export OPENAI_API_KEY=sk-...
    python main.py --pdf input/all_marksheets.pdf --output output

Every page is read by GPT-4o TWICE: once from the normal-resolution render,
once from a higher-zoom re-render of the same page. Because these are
genuinely different images, a digit misread in one pass is often read
correctly in the other. Where both passes agree, the value is trusted as-is.
Where they disagree, the pipeline checks which candidate keeps that course's
arithmetic consistent (components summing to the printed Total) and uses
that one automatically; only what's still ambiguous after that lands in the
Excel's "Needs Review" column. No manual steps needed to run this - review
is only ever needed afterwards, on flagged rows.

This roughly doubles the API cost per page versus a single pass - worth it
for exam records, but pass --single-pass if you want the cheaper 1x mode.

First run splits the PDF into images (cached in cache/pages/ and
cache/pages_hires/) and calls GPT once per page per pass (cached in
cache/gpt_json/ and cache/gpt_json_verify/). Re-running after a crash or
interruption skips anything already cached, so you never pay twice for the
same page.
"""
import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

try:
    from dotenv import load_dotenv
    load_dotenv()  # reads .env in the current folder and puts values into os.environ
except ImportError:
    pass  # dotenv not installed - fine as long as OPENAI_API_KEY is set in the shell

from openai import OpenAI
from pdf_split import split_pdf_to_images
from gpt_extract import extract_marksheet
from reconcile import reconcile
from config import load_subject_map, save_subject_map, semester_folder_name, subject_for_student
from build_excel import write_subject_workbook


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pdf", required=True, help="Path to the master PDF containing all grade card pages")
    ap.add_argument("--output", default="output", help="Root folder for Semester_X/Subject.xlsx files")
    ap.add_argument("--cache", default="cache", help="Folder for cached page images + GPT JSON (safe to reuse across runs)")
    ap.add_argument("--subject-map", default="subject_map.json", help="course-code-prefix -> subject-name mapping, auto-created on first run")
    ap.add_argument("--dpi", type=int, default=220)
    ap.add_argument("--verify-dpi", type=int, default=350, help="Resolution for the second, cross-checking pass")
    ap.add_argument("--single-pass", action="store_true", help="Skip the second verification pass (cheaper, less accurate)")
    args = ap.parse_args()

    client = OpenAI()  # reads OPENAI_API_KEY from the environment

    pages_dir = Path(args.cache) / "pages"
    gpt_cache_dir = Path(args.cache) / "gpt_json"
    pages_hires_dir = Path(args.cache) / "pages_hires"
    gpt_cache_verify_dir = Path(args.cache) / "gpt_json_verify"

    print(f"[1/4] Splitting {args.pdf} into page images ...")
    image_paths = split_pdf_to_images(args.pdf, str(pages_dir), dpi=args.dpi)
    print(f"      {len(image_paths)} pages found")
    if not args.single_pass:
        hires_paths = split_pdf_to_images(args.pdf, str(pages_hires_dir), dpi=args.verify_dpi)

    print("[2/4] Extracting each page with GPT-4o (cached results are skipped) ...")
    records = []
    failed = []
    for i, img_path in enumerate(image_paths, start=1):
        try:
            pass1 = extract_marksheet(img_path, client, cache_dir=str(gpt_cache_dir))
            if args.single_pass:
                final = pass1
                final["_review_notes"] = []
            else:
                pass2 = extract_marksheet(hires_paths[i - 1], client, cache_dir=str(gpt_cache_verify_dir))
                final, notes = reconcile(pass1, pass2)
                final["_review_notes"] = notes
            records.append(final)
            flag = f" [{len(final['_review_notes'])} field(s) to review]" if final.get("_review_notes") else ""
            print(f"      [{i}/{len(image_paths)}] {Path(img_path).name}: "
                  f"{final.get('student_name', '?')} ({final.get('registration_no', '?')}){flag}")
        except Exception as e:
            failed.append((img_path, str(e)))
            print(f"      [{i}/{len(image_paths)}] {Path(img_path).name}: FAILED - {e}")

    print("[3/4] Grouping students into Semester / Subject buckets ...")
    subject_map = load_subject_map(args.subject_map)
    groups = defaultdict(list)  # (semester, subject) -> [records]
    for rec in records:
        semester = semester_folder_name(rec.get("semester_roman", ""), rec.get("exam_title", ""))
        subject, prefix = subject_for_student(rec, subject_map)
        groups[(semester, subject)].append(rec)
    save_subject_map(args.subject_map, subject_map)  # persist any new placeholder prefixes

    print("[4/4] Writing Excel files ...")
    for (semester, subject), group_records in groups.items():
        out_path = Path(args.output) / semester / f"{subject}.xlsx"
        write_subject_workbook(group_records, subject, str(out_path))
        print(f"      {out_path}  ({len(group_records)} students)")

    print("\nDone.")
    print(f"  Students processed : {len(records)}")
    print(f"  Failed pages        : {len(failed)}")
    if failed:
        fail_log = Path(args.output) / "_failed_pages.json"
        fail_log.write_text(json.dumps(failed, indent=2))
        print(f"  See {fail_log} for details - re-run the same command to retry just these once fixed.")
    unknown_prefixes = [p for p, name in subject_map.items() if p == name]
    if unknown_prefixes:
        print(f"\n  NOTE: these course-code prefixes have no friendly subject name yet: {unknown_prefixes}")
        print(f"  Edit {args.subject_map} to rename them (e.g. \"HISG\": \"History\") and re-run "
              f"(cached GPT results make this instant - no extra API cost).")


if __name__ == "__main__":
    main()
