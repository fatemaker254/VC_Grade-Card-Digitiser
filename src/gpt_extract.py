"""
Stage 2: Extract
Sends one marksheet page image to GPT-4o with a strict JSON schema and gets
back fully structured data: student identity fields + every course row,
with whatever components that course actually has (Theoretical / Practical /
Tutorial / Internal Assessment / etc. - this varies by department, so the
schema doesn't hard-code a fixed set of components).
"""
import base64
import json
import time
from pathlib import Path
from openai import OpenAI

MODEL = "gpt-4o"  # vision-capable; swap for a newer vision model if you have one

SYSTEM_PROMPT = """You are an expert at reading Indian university grade cards / marksheets \
(this batch is from University of Calcutta CBCS/CCF grade cards, but read whatever is on the page).
Extract every field exactly as printed. Do not guess, invent, or "correct" any number, code, or name - \
if something is illegible, use null for that field and set "low_confidence": true on that record.
Marks, credits and credit points must be copied digit-for-digit as printed.
A course's "components" are whichever rows appear under it (e.g. Theoretical, Practical, Tutorial, \
Internal Assessment, Project) - include exactly the components printed for that course, no more, no fewer.
"""

JSON_SCHEMA = {
    "name": "marksheet_extraction",
    "strict": True,
    "schema": {
        "type": "object",
        "properties": {
            "university": {"type": "string"},
            "exam_title": {"type": "string", "description": "full exam/grade card title line, e.g. 'B.A. Semester - III (Honours) Examination (Under CBCS), 2025'"},
            "semester_roman": {"type": "string", "description": "just the semester number as printed, e.g. 'III'"},
            "student_name": {"type": "string"},
            "registration_no": {"type": "string"},
            "roll_no": {"type": "string"},
            "abc_id": {"type": ["string", "null"]},
            "courses": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "course_code": {"type": "string"},
                        "course_type": {"type": "string", "description": "e.g. 'Discipline Specific Core Course', 'Core Course', 'Minor Course', 'Skill Enhancement Course'"},
                        "course_name": {"type": "string"},
                        "components": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "name": {"type": "string", "description": "e.g. Theoretical, Practical, Tutorial, Internal Assessment"},
                                    "year": {"type": ["string", "null"]},
                                    "full_marks": {"type": ["number", "null"]},
                                    "marks_obtained": {"type": ["string", "null"], "description": "usually a number as string; keep as printed (e.g. 'AB', '19 +1')"}
                                },
                                "required": ["name", "year", "full_marks", "marks_obtained"],
                                "additionalProperties": False
                            }
                        },
                        "total_full_marks": {"type": ["number", "null"]},
                        "total_marks_obtained": {"type": ["number", "null"]},
                        "credit": {"type": ["number", "null"]},
                        "credit_points": {"type": ["number", "null"]},
                        "grade": {"type": ["string", "null"]},
                        "status": {"type": ["string", "null"]},
                        "low_confidence": {"type": "boolean"}
                    },
                    "required": ["course_code", "course_type", "course_name", "components",
                                 "total_full_marks", "total_marks_obtained", "credit",
                                 "credit_points", "grade", "status", "low_confidence"],
                    "additionalProperties": False
                }
            },
            "grand_total_full_marks": {"type": ["number", "null"]},
            "grand_total_marks_obtained": {"type": ["number", "null"]},
            "grand_total_credit": {"type": ["number", "null"]},
            "grand_total_credit_points": {"type": ["number", "null"]},
            "sgpa": {"type": ["string", "null"]},
            "remarks": {"type": ["string", "null"]},
            "low_confidence": {"type": "boolean", "description": "true if ANY field on the page was hard to read"}
        },
        "required": ["university", "exam_title", "semester_roman", "student_name",
                     "registration_no", "roll_no", "abc_id", "courses",
                     "grand_total_full_marks", "grand_total_marks_obtained",
                     "grand_total_credit", "grand_total_credit_points",
                     "sgpa", "remarks", "low_confidence"],
        "additionalProperties": False
    }
}


def _encode_image(path: str) -> str:
    with open(path, "rb") as f:
        return base64.b64encode(f.read()).decode("utf-8")


def extract_marksheet(image_path: str, client: OpenAI, cache_dir: str = None,
                       max_retries: int = 3) -> dict:
    """
    Calls GPT-4o vision on one marksheet image and returns the parsed JSON dict.
    Caches the raw response to cache_dir/<image_stem>.json so re-runs after a
    crash don't re-spend API calls on pages already processed.
    """
    cache_path = None
    if cache_dir:
        cache_path = Path(cache_dir) / f"{Path(image_path).stem}.json"
        if cache_path.exists():
            return json.loads(cache_path.read_text())

    b64 = _encode_image(image_path)
    last_err = None
    for attempt in range(max_retries):
        try:
            resp = client.chat.completions.create(
                model=MODEL,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": [
                        {"type": "text", "text": "Extract this grade card into the required JSON structure."},
                        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}", "detail": "high"}}
                    ]}
                ],
                response_format={"type": "json_schema", "json_schema": JSON_SCHEMA},
                temperature=0,
            )
            data = json.loads(resp.choices[0].message.content)
            data["_source_image"] = image_path
            if cache_path:
                cache_path.parent.mkdir(parents=True, exist_ok=True)
                cache_path.write_text(json.dumps(data, indent=2))
            return data
        except Exception as e:
            last_err = e
            time.sleep(2 ** attempt)
    raise RuntimeError(f"GPT extraction failed for {image_path} after {max_retries} tries: {last_err}")
