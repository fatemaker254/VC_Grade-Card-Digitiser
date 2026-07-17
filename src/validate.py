"""
Stage 3: Validate
Never trust extracted marks blindly - cross-check the arithmetic that should
always hold on a grade card, and flag anything that doesn't add up so a
human reviews that one row instead of it silently being wrong in the Excel.

validate_student() returns (notes, items):
  notes - human-readable strings, used in the Excel "Needs Review" column
  items - structured dicts, one per flagged field, used to drive the
          web review queue (course_code, field, label, current_value, reason)

normalize_fail_credit_points() is a separate, CONFIDENT auto-correction (not
a review item): whichever course a student failed always prints a blank
Credit Points for that course, and the final SGPA is blank ("N.A.") for the
whole page - every other, passed course keeps its normal Credit Points. This
is a fixed rule of how these grade cards are printed, not a guess, so it's
applied automatically rather than sent to a human to confirm.

NOTE: the grade-scale check and the credit-sum / SGPA-formula checks that
used to live here have been removed - real-batch testing showed credits and
grade points are read reliably (so cross-checking them was just noise),
while marks are the field that actually needs the human-review safety net.
"""

FAIL_STATUSES = {"F", "F(TH)", "F(PR)", "F(TU)", "ECDB1", "ECDB2"}


def _to_num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _base_num(raw):
    """'19 +1' -> 19.0 ; 'AB' -> None ; '' -> None"""
    raw = str(raw or "").replace(" ", "")
    if raw in ("", "AB", "None", "-"):
        return None
    base = raw.split("+")[0].split("-")[0]
    return _to_num(base)


def _is_fail_status(status) -> bool:
    return (status or "").strip().upper() in FAIL_STATUSES


SUPPRESSED_ON_FAIL = {
    ("Grand Total", "sgpa"),
    ("Grand Total", "grand_total_marks_obtained"),
    ("Grand Total", "grand_total_credit_points"),
}
SUPPRESSED_LABELS = ("SGPA disagreement", "Grand Total marks obtained disagreement",
                     "Grand Total credit points disagreement")


def normalize_fail_credit_points(record: dict) -> list:
    """Forces Credit Points blank for any course whose Status is a Fail, and
    forces SGPA / Grand Total marks obtained / Grand Total credit points
    blank if any course on the page failed - that's how these grade cards
    are actually printed. Also strips any leftover two-pass disagreement
    flags on those now-blanked fields (a mismatch about what number used to
    be there no longer matters once we know for certain it's blank).
    Returns a list of plain-text notes describing what was auto-corrected,
    for the log only - these are certain, not sent to the review queue."""
    notes = []
    any_fail = False

    for c in record.get("courses", []):
        if _is_fail_status(c.get("status")):
            any_fail = True
            if c.get("credit_points") not in (None, "", "-"):
                notes.append(f"{c.get('course_code', '?')}: status is {c.get('status')} (failed) - "
                              f"cleared Credit Points ({c.get('credit_points')!r} -> blank)")
                c["credit_points"] = None

    if any_fail:
        if record.get("sgpa") not in (None, "", "N.A.", "NA"):
            notes.append(f"Semester has a failed course - cleared SGPA ({record.get('sgpa')!r} -> N.A.)")
            record["sgpa"] = "N.A."
        if record.get("grand_total_marks_obtained") not in (None, "", "-"):
            notes.append("Semester has a failed course - cleared Grand Total marks obtained "
                          f"({record.get('grand_total_marks_obtained')!r} -> blank)")
            record["grand_total_marks_obtained"] = None
        if record.get("grand_total_credit_points") not in (None, "", "-"):
            notes.append("Semester has a failed course - cleared Grand Total credit points "
                          f"({record.get('grand_total_credit_points')!r} -> blank)")
            record["grand_total_credit_points"] = None

        record["_recon_items"] = [
            it for it in record.get("_recon_items", [])
            if (it.get("course_code"), it.get("field")) not in SUPPRESSED_ON_FAIL
        ]
        record["_review_notes"] = [
            n for n in record.get("_review_notes", [])
            if not (n.startswith("Grand Total: ") and any(lbl in n for lbl in SUPPRESSED_LABELS))
        ]

    return notes


def validate_student(record: dict, schema=None) -> tuple:
    notes = []
    items = []

    def flag(course_code, field, label, current_value, reason):
        notes.append(f"{course_code}: {reason}")
        items.append({"course_code": course_code, "field": field, "label": label,
                       "current_value": current_value, "reason": reason})

    if record.get("low_confidence"):
        notes.append("GPT flagged low confidence on this page")

    courses = record.get("courses", [])
    any_course_total_missing = False

    for c in courses:
        code = c.get("course_code", "?")
        if c.get("low_confidence"):
            notes.append(f"{code}: low confidence")

        # --- component marks should sum to the printed total ---
        comp_sum = 0.0
        comp_ok = True
        for comp in c.get("components", []):
            n = _base_num(comp.get("marks_obtained"))
            if n is None:
                comp_ok = False
            else:
                comp_sum += n

            # --- bounds check: marks obtained can never exceed full marks ---
            full = _to_num(comp.get("full_marks"))
            if n is not None and full is not None and n > full + 0.01:
                flag(code, f"component:{comp.get('name')}:marks_obtained",
                     f"{comp.get('name')} marks obtained",
                     comp.get("marks_obtained"),
                     f"{comp.get('name')} marks obtained ({n:g}) exceeds full marks ({full:g})")

        total_obtained = _to_num(c.get("total_marks_obtained"))
        total_full = _to_num(c.get("total_full_marks"))

        if comp_ok and total_obtained is not None and abs(comp_sum - total_obtained) > 0.5:
            flag(code, "total_marks_obtained", "Total marks obtained",
                 c.get("total_marks_obtained"),
                 f"components sum to {comp_sum:g} but Total shows {total_obtained:g}")

        # --- a Total should never be present when a component behind it is
        #     missing/illegible - a real number here despite missing inputs
        #     usually means something was misread rather than genuinely blank ---
        if not comp_ok and total_obtained is not None:
            flag(code, "total_marks_obtained", "Total marks obtained",
                 c.get("total_marks_obtained"),
                 f"one or more component marks are missing/unreadable, so this Total "
                 f"({total_obtained:g}) can't be trusted")

        if total_obtained is None:
            any_course_total_missing = True

        # --- bounds check on the total itself ---
        if total_obtained is not None and total_full is not None and total_obtained > total_full + 0.01:
            flag(code, "total_marks_obtained", "Total marks obtained",
                 c.get("total_marks_obtained"),
                 f"Total marks obtained ({total_obtained:g}) exceeds Total full marks ({total_full:g})")

    # --- same cascade at the Grand Total level: if any course's own total is
    #     missing, a Grand Total that's still fully filled in is suspect ---
    grand_obtained = _to_num(record.get("grand_total_marks_obtained"))
    if any_course_total_missing and grand_obtained is not None:
        flag("Grand Total", "grand_total_marks_obtained", "Grand Total marks obtained",
             record.get("grand_total_marks_obtained"),
             f"one or more course Totals are missing/unreadable, so this Grand Total "
             f"({grand_obtained:g}) can't be trusted")

    if not record.get("registration_no"):
        notes.append("missing registration number")
    if not record.get("roll_no"):
        notes.append("missing roll number")

    return notes, items
