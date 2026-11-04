import io
import json
import xml.etree.ElementTree as ET
from pathlib import Path

import pandas as pd
import pytesseract
from bs4 import BeautifulSoup
from docx import Document
from PIL import Image, ImageOps
from pptx import Presentation
from pypdf import PdfReader

from core.config import (
    SUPPORTED_EXTENSIONS,
    SPREADSHEET_EXTENSIONS,
    IMAGE_EXTENSIONS,
)


def decode_text(data: bytes) -> str:
    """
    Try common encodings before falling back to UTF-8 replacement.
    """
    for encoding in ("utf-8-sig", "utf-8", "cp1252", "latin-1"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def _extract_pptx_shape_text(shape) -> list[str]:
    """
    Recursively extract text from shapes, including grouped shapes and tables.
    """
    lines = []
    if getattr(shape, "has_text_frame", False):
        if shape.text and shape.text.strip():
            lines.append(shape.text.strip())

    if getattr(shape, "has_table", False):
        for row in shape.table.rows:
            row_text = " | ".join(
                cell.text.strip()
                for cell in row.cells
                if cell.text.strip()
            )
            if row_text:
                lines.append(row_text)

    sub_shapes = getattr(shape, "shapes", None)
    if sub_shapes:
        for sub_shape in sub_shapes:
            lines.extend(_extract_pptx_shape_text(sub_shape))

    return lines


def extract_text(filename: str, data: bytes) -> str:
    """
    Extract readable text from all supported formats.
    Includes Phase 3 OCR fallback for scanned/image-only PDFs.
    """
    ext = Path(filename).suffix.lower()

    if ext not in SUPPORTED_EXTENSIONS:
        raise ValueError(
            f"Unsupported file type: {ext or '(no extension)'}. "
            f"Supported extensions: {', '.join(sorted(SUPPORTED_EXTENSIONS))}"
        )

    # 1. PDF Documents (with Phase 3 Scanned PDF OCR Fallback)
    if ext == ".pdf":
        reader = PdfReader(io.BytesIO(data))

        if reader.is_encrypted:
            raise ValueError(
                "This PDF is password-protected. Remove its password before uploading."
            )

        pages = []
        for page_number, page in enumerate(reader.pages, start=1):
            page_text = page.extract_text() or ""
            
            # If standard text exists, use it
            if page_text.strip():
                pages.append(f"[Page {page_number}]\n{page_text.strip()}")
            else:
                # Scanned page fallback: extract embedded images and run OCR
                ocr_fragments = []
                for img_file in getattr(page, "images", []):
                    try:
                        img = Image.open(io.BytesIO(img_file.data))
                        img = ImageOps.exif_transpose(img).convert("RGB")
                        text_from_img = pytesseract.image_to_string(img, lang="eng").strip()
                        if text_from_img:
                            ocr_fragments.append(text_from_img)
                    except Exception:
                        pass
                if ocr_fragments:
                    pages.append(f"[Page {page_number} (OCR)]\n" + "\n".join(ocr_fragments))

        text = "\n\n".join(pages)
        if not text.strip():
            raise ValueError(
                "No selectable text or readable scanned pages were found in this PDF."
            )
        return text

    # 2. Microsoft Word (.docx)
    if ext == ".docx":
        document = Document(io.BytesIO(data))
        sections = []

        for paragraph in document.paragraphs:
            if paragraph.text.strip():
                sections.append(paragraph.text)

        for table in document.tables:
            for row in table.rows:
                sections.append(
                    " | ".join(cell.text.strip() for cell in row.cells if cell.text.strip())
                )

        return "\n".join(sections)

    # 3. Microsoft PowerPoint (.pptx)
    if ext == ".pptx":
        presentation = Presentation(io.BytesIO(data))
        slides = []

        for slide_number, slide in enumerate(presentation.slides, start=1):
            slide_lines = []

            for shape in slide.shapes:
                slide_lines.extend(_extract_pptx_shape_text(shape))

            if getattr(slide, "has_notes_slide", False) and slide.notes_slide:
                notes_frame = getattr(slide.notes_slide, "notes_text_frame", None)
                if notes_frame and notes_frame.text and notes_frame.text.strip():
                    slide_lines.append(f"Notes: {notes_frame.text.strip()}")

            if slide_lines:
                slides.append(f"[Slide {slide_number}]\n" + "\n".join(slide_lines))

        return "\n\n".join(slides)

    # 4. Spreadsheets (.csv, .tsv, .xlsx, .xls, .ods)
    if ext in SPREADSHEET_EXTENSIONS:
        output = []

        if ext == ".csv":
            decoded = decode_text(data)
            sheets = {
                "CSV": pd.read_csv(io.StringIO(decoded), dtype=str, keep_default_na=False)
            }
        elif ext == ".tsv":
            decoded = decode_text(data)
            sheets = {
                "TSV": pd.read_csv(io.StringIO(decoded), sep="\t", dtype=str, keep_default_na=False)
            }
        else:
            sheets = pd.read_excel(io.BytesIO(data), sheet_name=None, dtype=str)

        for sheet_name, dataframe in sheets.items():
            if dataframe.empty:
                continue

            output.append(f"[Sheet: {sheet_name}]")
            limited = dataframe.head(1000).fillna("")
            output.append(limited.to_csv(index=False))

            if len(dataframe) > 1000:
                output.append("[Only the first 1000 rows were indexed.]")

        return "\n\n".join(output)

    # 5. Image OCR
    if ext in IMAGE_EXTENSIONS:
        image = Image.open(io.BytesIO(data))
        image = ImageOps.exif_transpose(image)
        image = image.convert("RGB")

        try:
            text = pytesseract.image_to_string(image, lang="eng")
        except Exception as ocr_err:
            raise RuntimeError(
                f"OCR failed: {ocr_err}. Verify that Tesseract OCR is installed "
                "and available on system PATH."
            ) from ocr_err

        if not text.strip():
            raise ValueError("OCR did not find readable text in this image.")

        return text

    # 6. HTML & XML
    if ext in {".html", ".htm"}:
        soup = BeautifulSoup(decode_text(data), "html.parser")
        for tag in soup(["script", "style", "noscript", "svg"]):
            tag.decompose()
        return soup.get_text(separator="\n", strip=True)

    if ext == ".xml":
        raw_xml = decode_text(data)
        try:
            soup = BeautifulSoup(raw_xml, "xml")
            return soup.get_text(separator="\n", strip=True)
        except Exception:
            try:
                root = ET.fromstring(raw_xml)
                return "".join(root.itertext()).strip()
            except Exception:
                return raw_xml

    # 7. Code, JSON, YAML, Markdown, Text
    text = decode_text(data)
    if ext == ".json":
        try:
            text = json.dumps(json.loads(text), ensure_ascii=False, indent=2)
        except json.JSONDecodeError:
            pass

    return text

