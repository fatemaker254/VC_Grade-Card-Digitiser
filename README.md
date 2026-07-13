# Marksheet -> Excel pipeline

Turns one big PDF of student grade cards (any mix of departments and
semesters) into a folder tree of Excel files:

```
output/
  Semester_3/
    Biochemistry.xlsx      <- one row per student, all BCMM courses
    Bengali.xlsx            <- one row per student, all BNGA courses
  Semester_4/
    ...
```

Each workbook matches the layout of your reference template: Roll No,
Registration No, Student Name, then per course the Theory/Practical (or
Tutorial/Internal Assessment, whatever that department actually has) marks,
full marks, total, grade, credit, credit points, status - followed by Grand
Total, Credits, Credit Points, SGPA, Remarks, and a **Needs Review** column.

## 1. Install

```bash
pip install -r requirements.txt
```

No other system dependencies needed - PDF rendering is handled by PyMuPDF,
a self-contained Python package (no separate "poppler" install/PATH setup
required).

## 2. Set your OpenAI API key

Easiest: copy `.env.example` to `.env` and put your real key in it:

```
OPENAI_API_KEY=sk-proj-...your full key...
```

`main.py` loads `.env` automatically (via `python-dotenv`, in requirements.txt)
- just make sure the file is named exactly `.env` and sits in the same folder
as `main.py`, and that the key has no quotes and no trailing spaces.

Alternative, without a `.env` file - set it directly in your terminal
session (note this only lasts for that terminal window):

```powershell
# Windows PowerShell
$env:OPENAI_API_KEY = "sk-proj-..."
```
```bash
# Mac/Linux
export OPENAI_API_KEY=sk-proj-...
```

Get your key from https://platform.openai.com/api-keys - it should be a long
string starting with `sk-`, not a placeholder.

## 3. Put your PDF in `input/`

One PDF containing every student's grade card, one page per student, in
any order - the pipeline sorts everything by semester and subject on its
own.

## 4. Run it

```bash
python main.py --pdf input/all_marksheets.pdf --output output
```

That's the only file you run. It will:
1. Split the PDF into page images at normal resolution (`cache/pages/`) and
   again at higher zoom for cross-checking (`cache/pages_hires/`)
2. Send each page to GPT-4o **twice**, independently, and get back
   structured JSON (cached in `cache/gpt_json/` and `cache/gpt_json_verify/`
   - if the run crashes or you stop it, just re-run the same command;
   pages already processed are skipped for free)
3. Reconcile the two independent reads: where they agree, trust it; where
   they disagree, auto-resolve using arithmetic consistency (components
   summing to the printed Total) where possible, otherwise flag for review
   - see "How accuracy is protected" below
4. Group students by semester + subject and write the Excel files

## How accuracy is protected

Every page is read by GPT-4o **twice**, independently:
1. Once from a normal-resolution render of the page
2. Once from a second, higher-zoom re-render of the *same* page (`--verify-dpi`, default 350)

Because these are genuinely different images, a digit GPT misread in one
pass is often read correctly in the other. The two reads are compared
field by field:
- **Agree** -> trusted as-is
- **Disagree** -> the pipeline checks which candidate keeps that course's
  arithmetic consistent (its components summing to the printed Total) and
  automatically uses that one
- **Still ambiguous** (e.g. the Total itself also disagrees between passes,
  so self-consistency can't be trusted either way) -> flagged in **Needs
  Review** rather than guessed

This roughly **doubles API cost per page** (two GPT calls instead of one).
If you'd rather skip it and rely on the single-pass + arithmetic-only
checks, add `--single-pass`:

```bash
python main.py --pdf input/all_marksheets.pdf --output output --single-pass
```

## 5. Fix subject names (one-time, per new department)

The first time the pipeline meets a new course-code prefix (e.g. `HISG`),
it can't yet know you call that department "History" - it writes a
placeholder using the prefix itself and lists it at the end of the run:

```
NOTE: these course-code prefixes have no friendly subject name yet: ['HISG']
Edit subject_map.json to rename them and re-run.
```

Open `subject_map.json`, change `"HISG": "HISG"` to `"HISG": "History"`,
and re-run the same command. Because everything is cached, this re-run
costs no extra API calls and finishes in seconds - it just renames the
output files.

## 6. Review flagged rows

Any row with something in the **Needs Review** column (highlighted yellow)
had numbers that the two independent GPT passes couldn't agree on and
couldn't be auto-resolved by arithmetic, or that failed a final sanity
check (e.g. Grand Total credits not matching the sum of course credits).
This is the only manual step in the whole pipeline - check those specific
cells against the original page image in `cache/pages/` (or
`cache/pages_hires/`) before trusting them. Everything else has already
been double-checked automatically.

## Cost / scale notes

- With two-pass verification (the default), GPT-4o vision costs roughly
  $0.02-0.06 per page (double a single pass). For ~1200 pages that's
  typically $30-70 total - test on a handful of pages first
  (`--pdf a_10_page_sample.pdf`) to confirm quality before running the full
  batch. Use `--single-pass` to halve this if you're comfortable with
  arithmetic-only checking.
- The cache means a full 1200-page batch is safe to run in chunks or resume
  after any interruption.
- If a whole department is unexpectedly missing from the output, check
  `output/_failed_pages.json` for pages that errored out (network hiccups,
  rate limits) - re-running the same command retries only those.

## Files

- `main.py` - the only script you run
- `src/pdf_split.py` - PDF -> page images
- `src/gpt_extract.py` - GPT-4o vision call + JSON schema
- `src/reconcile.py` - compares the two independent passes, auto-resolves
  disagreements via arithmetic consistency, flags what it can't
- `src/validate.py` - final arithmetic sanity checks -> "Needs Review" notes
- `src/build_excel.py` - builds the per-subject workbook, dynamic columns
- `src/config.py` - semester-folder naming + course-prefix-to-subject map
- `subject_map.json` - editable prefix -> department name lookup (auto-created)
