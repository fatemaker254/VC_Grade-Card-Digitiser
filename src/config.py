"""
Grouping helpers: which folder (semester) and which Excel file (subject)
a student record belongs in.

Grouping is done by course-code PREFIX, not by GPT's free-text guess of the
subject name - the prefix is printed consistently on every grade card in a
department (BCMM, BNGA, ...), so it's a reliable, stable grouping key even
if GPT phrases things slightly differently page to page.
"""
import json
import re
from pathlib import Path

ROMAN_MAP = {"I": 1, "V": 5, "X": 10, "L": 50, "C": 100, "D": 500, "M": 1000}

DEFAULT_SUBJECT_MAP = {
    "BCMM": "Biochemistry",
    "BNGA": "Bengali",
    "MZOO": "Zoology",
    "HISG": "History",
    "STSD": "Statistics",
    "ALEN": "English",
}


def roman_to_int(s: str) -> int:
    s = s.upper().strip()
    total = 0
    prev = 0
    for ch in reversed(s):
        val = ROMAN_MAP.get(ch, 0)
        total += -val if val < prev else val
        prev = max(prev, val)
    return total or 0


def semester_folder_name(semester_roman: str, exam_title: str = "") -> str:
    """Turn 'III' (or the exam title as a fallback) into 'Semester_3'."""
    roman = (semester_roman or "").strip()
    if not roman:
        m = re.search(r"Semester\s*-?\s*([IVXLC]+)", exam_title, re.IGNORECASE)
        roman = m.group(1) if m else ""
    n = roman_to_int(roman) if roman else 0
    return f"Semester_{n}" if n else "Semester_Unknown"


def load_subject_map(path: str) -> dict:
    p = Path(path)
    if p.exists():
        return json.loads(p.read_text())
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(DEFAULT_SUBJECT_MAP, indent=2))
    return dict(DEFAULT_SUBJECT_MAP)


def save_subject_map(path: str, mapping: dict):
    Path(path).write_text(json.dumps(mapping, indent=2))


def course_prefix(course_code: str) -> str:
    """'BCMM-DSCC-3' -> 'BCMM'"""
    return course_code.split("-")[0].strip().upper() if course_code else ""


def anchor_course(courses: list) -> dict:
    """The course that defines the student's Honours/Major subject -
    identified by course_type containing 'Core Course' (covers both
    'Core Course' and 'Discipline Specific Core Course')."""
    for c in courses:
        if "core course" in (c.get("course_type") or "").lower():
            return c
    return courses[0] if courses else {}


def subject_for_student(record: dict, subject_map: dict) -> tuple:
    """Returns (subject_display_name, course_prefix). Updates subject_map
    in place with a placeholder if the prefix hasn't been seen before, so
    the user can fill in the real department name afterwards."""
    anchor = anchor_course(record.get("courses", []))
    prefix = course_prefix(anchor.get("course_code", ""))
    if not prefix:
        return "Unknown_Subject", prefix
    if prefix not in subject_map:
        subject_map[prefix] = prefix  # placeholder; user should rename later
    return subject_map[prefix], prefix
