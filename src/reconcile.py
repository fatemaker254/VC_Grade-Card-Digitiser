"""
Stage 2b: Reconcile two independent extraction passes of the same page.

Pass 1 reads the page at normal resolution. Pass 2 re-renders the SAME page
at a higher zoom and reads it again from scratch. Because they're genuinely
different images (not the same image asked twice), a digit that GPT misread
in one pass is often read correctly in the other - so where the two passes
agree, confidence is high; where they disagree, we don't just guess, we
check which candidate keeps the row's arithmetic internally consistent
(components summing to the printed Total) and prefer that one. Anything
still ambiguous after that gets flagged in Needs Review - no manual editing
required to run the pipeline, only a final human glance at flagged rows.
"""
import copy

# Standard full-marks denominations used on these grade cards. Used as a
# tiebreaker when neither candidate's plausibility can be settled by the
# components-sum-to-total check (e.g. a Full Marks field mismatch).
PLAUSIBLE_FULL_MARKS = {5, 10, 15, 20, 25, 40, 50, 65, 75, 80, 100}


def _num(v):
    if v is None:
        return None
    s = str(v).strip()
    if s in ("", "AB", "None", "N.A.", "-"):
        return None
    s = s.split("+")[0].split("-")[0].strip()  # strip "+1" grace-mark suffixes
    try:
        return float(s)
    except ValueError:
        return None


def _norm(v):
    n = _num(v)
    return n if n is not None else (str(v).strip() if v is not None else None)


def _component_sum(components, overrides=None):
    """Sum marks_obtained across components, optionally overriding one by index."""
    total = 0.0
    any_val = False
    for i, comp in enumerate(components):
        val = comp.get("marks_obtained")
        if overrides and i in overrides:
            val = overrides[i]
        n = _num(val)
        if n is not None:
            total += n
            any_val = True
    return total if any_val else None


def _resolve_component_marks(comps1, comps2, idx, ref_total1, ref_total2):
    """Pick whichever candidate makes the component sum match a known total.
    Only trusted when the two passes agree on what that total actually is -
    if the totals themselves disagree, a self-consistency check can be
    fooled by a pass that misread multiple numbers in a matching way, so we
    flag rather than guess in that case."""
    v1 = comps1[idx].get("marks_obtained")
    v2 = comps2[idx].get("marks_obtained") if idx < len(comps2) else None

    if ref_total1 is not None and ref_total2 is not None and abs(ref_total1 - ref_total2) > 0.01:
        return v1, True, (f"marks_obtained disagreement unresolved (course Total also "
                           f"disagrees between passes): pass1={v1!r} pass2={v2!r}")

    ref = ref_total1 if ref_total1 is not None else ref_total2
    if ref is None:
        return v1, True, f"kept pass-1 value ({v1!r} vs {v2!r}), no Total to check against"

    sum1 = _component_sum(comps1)  # comps1 already holds v1
    sum2 = _component_sum(comps1, overrides={idx: v2})  # swap in v2 for this one slot

    d1 = abs((sum1 or 0) - ref)
    d2 = abs((sum2 or 0) - ref)
    if d1 < d2 - 0.01:
        return v1, False, None
    if d2 < d1 - 0.01:
        return v2, False, f"used pass-2 reading ({v2!r} over pass-1's {v1!r}) - matches printed Total"
    return v1, True, f"marks_obtained disagreement unresolved: pass1={v1!r} pass2={v2!r}"


def _resolve_full_marks(v1, v2):
    n1, n2 = _num(v1), _num(v2)
    if n1 in PLAUSIBLE_FULL_MARKS and n2 not in PLAUSIBLE_FULL_MARKS:
        return v1, False, None
    if n2 in PLAUSIBLE_FULL_MARKS and n1 not in PLAUSIBLE_FULL_MARKS:
        return v2, False, f"used pass-2 reading ({v2!r} over pass-1's {v1!r}) - a standard full-marks value"
    return v1, True, f"full_marks disagreement unresolved: pass1={v1!r} pass2={v2!r}"


def reconcile(rec1: dict, rec2: dict) -> tuple:
    """Returns (merged_record, list_of_notes). merged_record is rec1 with
    disagreeing fields resolved (or left as pass-1 + flagged)."""
    merged = copy.deepcopy(rec1)
    notes = []

    courses2_by_code = {c.get("course_code"): c for c in rec2.get("courses", [])}

    for c1 in merged.get("courses", []):
        code = c1.get("course_code", "?")
        c2 = courses2_by_code.get(code)
        if c2 is None:
            notes.append(f"{code}: course not found in verification pass (page framing may differ)")
            continue

        comps1 = c1.get("components", [])
        comps2 = c2.get("components", [])
        ref_total1 = _num(c1.get("total_marks_obtained"))
        ref_total2 = _num(c2.get("total_marks_obtained"))

        for idx, comp1 in enumerate(comps1):
            comp2 = comps2[idx] if idx < len(comps2) else {}
            if _norm(comp1.get("marks_obtained")) != _norm(comp2.get("marks_obtained")):
                resolved, ambiguous, note = _resolve_component_marks(
                    comps1, comps2, idx, ref_total1, ref_total2)
                comp1["marks_obtained"] = resolved
                if note:
                    notes.append(f"{code} [{comp1.get('name')}]: {note}")
                if ambiguous:
                    c1["low_confidence"] = True

            if _norm(comp1.get("full_marks")) != _norm(comp2.get("full_marks")):
                resolved, ambiguous, note = _resolve_full_marks(
                    comp1.get("full_marks"), comp2.get("full_marks"))
                comp1["full_marks"] = resolved
                if note:
                    notes.append(f"{code} [{comp1.get('name')}]: {note}")
                if ambiguous:
                    c1["low_confidence"] = True

        # Course-level total/credit/credit_points: agree -> trust; disagree -> flag
        # (there's no independent way to auto-pick between two course-level
        # totals, so these always go to Needs Review rather than being guessed).
        for field in ("total_full_marks", "total_marks_obtained", "credit", "credit_points"):
            v1, v2 = c1.get(field), c2.get(field)
            if _norm(v1) != _norm(v2):
                notes.append(f"{code}: {field} disagreement pass1={v1!r} pass2={v2!r}")
                c1["low_confidence"] = True

        for field in ("grade", "status"):
            v1, v2 = (c1.get(field) or "").strip(), (c2.get(field) or "").strip()
            if v1 != v2:
                notes.append(f"{code}: {field} disagreement pass1={v1!r} pass2={v2!r}")
                c1["low_confidence"] = True

    for field in ("grand_total_full_marks", "grand_total_marks_obtained",
                  "grand_total_credit", "grand_total_credit_points", "sgpa"):
        v1, v2 = rec1.get(field), rec2.get(field)
        if _norm(v1) != _norm(v2):
            notes.append(f"{field} disagreement: pass1={v1!r} pass2={v2!r}")
            merged["low_confidence"] = True

    return merged, notes
