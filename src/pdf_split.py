"""
Stage 1: Split the master PDF (containing many students' grade cards, one
per page) into individual page images for GPT to read.

Uses PyMuPDF (the `fitz` package), which is a pure Python wheel with the PDF
renderer built in - no external "poppler" binary to install/PATH, unlike
pdf2image. Just `pip install pymupdf` and it works.
"""
from pathlib import Path
import fitz  # PyMuPDF


def split_pdf_to_images(pdf_path: str, out_dir: str, dpi: int = 220) -> list:
    """
    Converts every page of pdf_path into a JPEG in out_dir.
    Returns the list of image file paths, in page order.
    dpi=220 keeps files small enough for fast/cheap GPT vision calls while
    staying readable; bump to 300 if small print is being misread.
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    doc = fitz.open(pdf_path)
    stem = Path(pdf_path).stem
    zoom = dpi / 72  # PDF points are 72 per inch; fitz scales via a matrix
    mat = fitz.Matrix(zoom, zoom)

    paths = []
    for i in range(doc.page_count):
        page = doc[i]
        pix = page.get_pixmap(matrix=mat)
        img_path = out_dir / f"{stem}_page{i + 1:04d}.jpg"
        pix.save(str(img_path), jpg_quality=90)
        paths.append(str(img_path))
    doc.close()
    return paths
