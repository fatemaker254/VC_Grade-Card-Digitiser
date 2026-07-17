"""
Learns this college's actual grade -> grade-point mapping from the batch
itself (e.g. B+ = 7.5, C+ = 6.5 - these vary by institution/scheme), then
flags any row where Credit Points doesn't match (grade_point x credit).
Needs a decent-sized batch to learn from (a handful of pages won't have
enough repeats of each grade to be reliable) - if a grade doesn't have
enough samples yet, that grade is skipped rather than guessed at.
"""
from collections import defaultdict


def _to_num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def learn_grade_scale(records: list, min_samples: int = 3) -> dict:
    """Returns {grade: grade_point} learned from (credit_points / credit) per
    course across the whole batch, using the most common value per grade."""
    samples = defaultdict(list)
    for rec in records:
        for c in rec.get("courses", []):
            grade = (c.get("grade") or "").strip().upper()
            credit = _to_num(c.get("credit"))
            cp = _to_num(c.get("credit_points"))
            if grade and credit and cp is not None and credit > 0:
                samples[grade].append(round(cp / credit, 2))

    scale = {}
    for grade, vals in samples.items():
        if len(vals) < min_samples:
            continue
        # most common rounded value wins (robust to occasional OCR noise)
        counts = defaultdict(int)
        for v in vals:
            counts[v] += 1
        scale[grade] = max(counts, key=counts.get)
    return scale


def apply_grade_scale_check(record: dict, scale: dict) -> tuple:
    """Returns (notes, items) for courses whose Credit Points don't match
    this college's learned grade scale."""
    notes, items = [], []
    for c in record.get("courses", []):
        grade = (c.get("grade") or "").strip().upper()
        credit = _to_num(c.get("credit"))
        cp = _to_num(c.get("credit_points"))
        expected_point = scale.get(grade)
        if expected_point is None or credit is None or cp is None:
            continue
        expected_cp = round(expected_point * credit, 3)
        if abs(expected_cp - cp) > 0.05:
            code = c.get("course_code", "?")
            reason = (f"Credit Points {cp:g} doesn't match this college's grade scale "
                      f"({grade} = {expected_point:g}/credit -> expected {expected_cp:g})")
            notes.append(f"{code}: {reason}")
            items.append({"course_code": code, "field": "credit_points", "label": "Credit Points",
                           "current_value": c.get("credit_points"), "reason": reason})
    return notes, items
