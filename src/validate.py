"""
Stage 3: Validate
Never trust extracted marks blindly - cross-check the arithmetic that should
always hold on a grade card, and flag anything that doesn't add up so a
human reviews that one row instead of it silently being wrong in the Excel.
"""


def _to_num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def validate_student(record: dict, schema=None) -> list:
    notes = []

    if record.get("low_confidence"):
        notes.append("GPT flagged low confidence on this page")

    courses = record.get("courses", [])
    credit_sum = 0.0
    credit_points_sum = 0.0
    any_credit_missing = False

    for c in courses:
        code = c.get("course_code", "?")
        if c.get("low_confidence"):
            notes.append(f"{code}: low confidence")

        # component marks should sum to the printed total (allow "+1" grace-mark suffixes)
        comp_sum = 0.0
        comp_ok = True
        for comp in c.get("components", []):
            raw = str(comp.get("marks_obtained") or "").replace(" ", "")
            base = raw.split("+")[0].split("-")[0] if raw not in ("", "AB", "None") else None
            n = _to_num(base) if base else None
            if n is None:
                comp_ok = False
            else:
                comp_sum += n
        total_obtained = _to_num(c.get("total_marks_obtained"))
        if comp_ok and total_obtained is not None and abs(comp_sum - total_obtained) > 0.5:
            notes.append(f"{code}: components sum to {comp_sum} but Total shows {total_obtained}")

        credit = _to_num(c.get("credit"))
        cp = _to_num(c.get("credit_points"))
        if credit is None:
            any_credit_missing = True
        else:
            credit_sum += credit
        if cp is not None:
            credit_points_sum += cp

    gt_credit = _to_num(record.get("grand_total_credit"))
    if not any_credit_missing and gt_credit is not None and abs(credit_sum - gt_credit) > 0.01:
        notes.append(f"course credits sum to {credit_sum} but Grand Total credit shows {gt_credit}")

    gt_cp = _to_num(record.get("grand_total_credit_points"))
    if gt_cp is not None and abs(credit_points_sum - gt_cp) > 0.5:
        notes.append(f"credit points sum to {credit_points_sum:.3f} but Grand Total shows {gt_cp}")

    if not record.get("registration_no"):
        notes.append("missing registration number")
    if not record.get("roll_no"):
        notes.append("missing roll number")

    return notes
