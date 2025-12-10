"""
Multilingual (EN + DE) PDF invoice extraction utilities using pdfplumber and pytesseract.

Features:
- Layout-aware text reconstruction using page.extract_words()
- Language detection (simple heuristics) to switch between English/German patterns
- Number normalization for German (comma decimals) and English (dot decimals)
- OCR fallback using pdf2image + pytesseract for scanned PDFs or poor text extraction
- Produces output compatible with `Invoice` Pydantic model (imported from .schema)
"""

from __future__ import annotations

import io
import os
import re
import tempfile
import logging
from pathlib import Path
from typing import Dict, List, Optional, Union, Tuple

import pdfplumber
from pdf2image import convert_from_path
from PIL import Image
import pytesseract

from .schema import Invoice, InvoiceLineItem

# Configure Tesseract path for Windows
TESSE_RECT_PATHS = [
    r"C:\Program Files\Tesseract-OCR\tesseract.exe",
    r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
]
for p in TESSE_RECT_PATHS:
    if os.path.exists(p):
        pytesseract.pytesseract.tesseract_cmd = p
        break

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

# ---------------- Patterns ----------------
EN_INVOICE_NO = re.compile(r"(?:Invoice\s*(?:No\.?|Number)?\s*[:#]?\s*)([A-Za-z0-9\-_/]+)", re.IGNORECASE)
DE_INVOICE_NO = re.compile(r"(?:Rechnungs(?:nr\.|nummer)?|Belegnr\.?)\s*[:#]?\s*([A-Za-z0-9\-_/]+)", re.IGNORECASE)

EN_DATE = re.compile(r"(\d{4}[-/]\d{1,2}[-/]\d{1,2}|\d{1,2}[-/]\d{1,2}[-/]\d{2,4})")
DE_DATE = EN_DATE  # same formats usually; keep same regex

EN_TOTAL_KEYWORDS = ["total", "subtotal", "grand total", "amount due"]
DE_TOTAL_KEYWORDS = ["gesamtwert", "gesamtbetrag", "betrag", "gesamt", "gesamtwert inkl", "gesamtwert inkl. mwst"]

EN_VAT_KEYWORDS = ["tax", "vat"]
DE_VAT_KEYWORDS = ["mwst", "umsatzsteuer"]

# ---------------- Utilities ----------------


def _safe_temp_pdf_from_bytes(b: Union[bytes, bytearray, io.BytesIO]) -> Tuple[str, tempfile.NamedTemporaryFile]:
    """Write bytes/file-like to a temporary PDF file and return its path."""
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".pdf")
    if isinstance(b, (bytes, bytearray)):
        tmp.write(b)
    else:
        # assume BytesIO or file-like
        tmp.write(b.read())
    tmp.flush()
    tmp.close()
    return tmp.name, tmp


def detect_language(text: str) -> str:
    """Simple heuristic-based language detection for German vs English."""
    low = text.lower()
    german_markers = ["gesamtwert", "mwst", "kundennummer", "bestellung", "artikelbeschreibung", "menge"]
    german_count = sum(1 for w in german_markers if w in low)
    english_markers = ["invoice", "total", "tax", "quantity", "description"]
    english_count = sum(1 for w in english_markers if w in low)
    return "de" if german_count >= english_count and german_count > 0 else "en"


def normalize_number(num_str: str, lang: str = "en") -> Optional[float]:
    """
    Normalize numeric strings to float.
    German convention: thousands sep = '.' and decimal = ','
    English convention: thousands sep = ',' and decimal = '.'
    """
    if not num_str:
        return None
    s = num_str.strip()
    # remove currency symbols and stray characters
    s = re.sub(r"[^\d,.\-]", "", s)
    if lang == "de":
        # convert "1.285,20" -> "1285.20"
        # But be careful: "160,0000" might be "160.0000" (rare). We'll treat last comma as decimal.
        if s.count(",") >= 1:
            parts = s.split(",")
            integer_part = "".join(parts[:-1]).replace(".", "")
            decimal_part = parts[-1]
            normalized = f"{integer_part}.{decimal_part}"
        else:
            normalized = s.replace(".", "")
    else:
        # en: remove thousands commas
        normalized = s.replace(",", "")
    try:
        return float(normalized)
    except Exception:
        return None


def _group_words_to_lines(words: List[dict], y_tolerance: int = 3) -> List[str]:
    """
    Given words from pdfplumber page.extract_words(), group by approximate y coordinate to form lines.
    Sort words in each line by x0 to preserve left-to-right order.
    """
    # Each word dict includes 'text', 'x0', 'top', ...
    # bucket by rounded top (or center)
    buckets: Dict[int, List[dict]] = {}
    for w in words:
        # Use vertical center
        y_center = int(round((w.get("top", 0) + w.get("bottom", 0)) / 2))
        # find an existing bucket within tolerance
        found_key = None
        for key in buckets:
            if abs(key - y_center) <= y_tolerance:
                found_key = key
                break
        if found_key is None:
            buckets[y_center] = [w]
        else:
            buckets[found_key].append(w)

    # Build lines sorted by y (top to bottom) and words by x0
    lines = []
    for y in sorted(buckets.keys(), reverse=False):
        line_words = sorted(buckets[y], key=lambda item: item.get("x0", 0))
        line_text = " ".join(w.get("text", "") for w in line_words).strip()
        if line_text:
            lines.append(line_text)
    return lines


def extract_text_layout_aware(source: Union[str, Path, bytes, io.BytesIO]) -> str:
    """
    Use pdfplumber to extract words and rebuild lines preserving column layout.
    Falls back to page.extract_text() if extract_words() yields nothing meaningful.
    """
    try:
        if isinstance(source, (str, Path)):
            pdf_path = Path(source)
            with pdfplumber.open(pdf_path) as pdf:
                page_texts = []
                for page in pdf.pages:
                    words = page.extract_words(extra_attrs=["x0", "x1", "top", "bottom"])
                    if words:
                        lines = _group_words_to_lines(words)
                        page_texts.append("\n".join(lines))
                    else:
                        # fallback to plain text
                        page_texts.append(page.extract_text() or "")
                return "\n\n".join(page_texts)
        else:
            buffer = io.BytesIO(source) if isinstance(source, (bytes, bytearray)) else source
            # pdfplumber.open accepts file-like, but ensure it's at start
            buffer.seek(0)
            with pdfplumber.open(buffer) as pdf:
                page_texts = []
                for page in pdf.pages:
                    words = page.extract_words(extra_attrs=["x0", "x1", "top", "bottom"])
                    if words:
                        lines = _group_words_to_lines(words)
                        page_texts.append("\n".join(lines))
                    else:
                        page_texts.append(page.extract_text() or "")
                return "\n\n".join(page_texts)
    except Exception as e:
        logging.warning(f"Layout-aware extraction failed: {e}. Falling back to pdfplumber.extract_text()")
        # Try a simple fallback
        try:
            if isinstance(source, (str, Path)):
                with pdfplumber.open(source) as pdf:
                    return "\n".join((page.extract_text() or "") for page in pdf.pages)
            else:
                buffer.seek(0)
                with pdfplumber.open(buffer) as pdf:
                    return "\n".join((page.extract_text() or "") for page in pdf.pages)
        except Exception as e2:
            logging.error(f"Fallback extraction failed: {e2}")
            return ""


# ---------------- OCR fallback ----------------


def _extract_text_with_ocr(source: Union[str, Path, bytes, io.BytesIO]) -> str:
    """
    Convert PDF pages to images and perform OCR with pytesseract.
    convert_from_path cannot take BytesIO directly; for bytes we create a temp file.
    """
    tmp_file_obj = None
    try:
        if isinstance(source, (str, Path)):
            pdf_path = str(source)
        else:
            path, tmp_file_obj = _safe_temp_pdf_from_bytes(source)
            pdf_path = path

        images = convert_from_path(pdf_path)
        ocr_pages = []
        for img in images:
            # if color mode is not RGB, convert
            if img.mode != "RGB":
                img = img.convert("RGB")
            text = pytesseract.image_to_string(img)
            ocr_pages.append(text)
        return "\n\n".join(ocr_pages)
    except Exception as e:
        logging.error(f"OCR extraction failed: {e}")
        return ""
    finally:
        if tmp_file_obj:
            try:
                os.remove(tmp_file_obj.name)
            except Exception:
                pass


# ---------------- Field extractors ----------------


def _guess_invoice_number(text: str, lang: str = "en") -> Optional[str]:
    if lang == "de":
        m = DE_INVOICE_NO.search(text)
    else:
        m = EN_INVOICE_NO.search(text)
    return m.group(1).strip() if m else None


def _guess_dates(text: str, lang: str = "en") -> Dict[str, Optional[str]]:
    matches = EN_DATE.findall(text)
    invoice_date = matches[0] if matches else None
    due_date = matches[1] if len(matches) > 1 else None
    return {"invoice_date": invoice_date, "due_date": due_date}


def _guess_totals(text: str, lang: str = "en") -> Dict[str, Optional[float]]:
    """
    Finds gross/total and tax amounts using keyword heuristics.
    Returns numeric values (floats) normalized depending on language.
    """
    net_total = tax_amount = gross_total = None
    lines = text.splitlines()
    # Scan bottom-up, totals often near the end
    for line in reversed(lines):
        low = line.lower()
        # Find numbers on the line
        nums = re.findall(r"([0-9.,]+)", line)
        if not nums:
            continue
        # pick the last numeric token as likely the amount
        raw_val = nums[-1]
        val = normalize_number(raw_val, lang)
        if val is None:
            continue
        # match keywords
        if lang == "de":
            if any(k in low for k in ["mwst", "ust", "umsatzsteuer"]) and tax_amount is None:
                tax_amount = val
                continue
            if any(k in low for k in ["gesamtwert inkl", "gesamtwert inkl.", "gesamtwert", "gesamtbetrag", "gesamt"]) and gross_total is None:
                gross_total = val
                continue
            if "netto" in low and net_total is None:
                net_total = val
                continue
            if gross_total is None and any(k in low for k in DE_TOTAL_KEYWORDS):
                gross_total = val
        else:
            if any(k in low for k in EN_VAT_KEYWORDS) and tax_amount is None:
                tax_amount = val
                continue
            if any(k in low for k in EN_TOTAL_KEYWORDS) and gross_total is None:
                gross_total = val
                continue
            if net_total is None and "net" in low:
                net_total = val

    return {"net_total": net_total, "tax_amount": tax_amount, "gross_total": gross_total}


def _parse_line_items(text: str, lang: str = "en") -> List[InvoiceLineItem]:
    """
    Detects a table-like block using header keywords and parses subsequent lines.
    This is a heuristic parser that aims for simple invoices and may be improved further.
    """
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    items: List[InvoiceLineItem] = []

    # detect header row index
    header_idx = None
    for i, ln in enumerate(lines):
        low = ln.lower()
        if lang == "de":
            if ("pos" in low and "artikel" in low) or ("artikelbeschreibung" in low and "preis" in low) or ("menge" in low and "bestellwert" in low):
                header_idx = i
                break
        else:
            if ("description" in low and "price" in low) or ("quantity" in low and "description" in low):
                header_idx = i
                break

    # if header found, parse after it until we hit a totals block
    start = header_idx + 1 if header_idx is not None else 0
    for ln in lines[start:]:
        low = ln.lower()
        # stop when we reach a totals block in either language
        if lang == "de" and any(k in low for k in DE_TOTAL_KEYWORDS + DE_VAT_KEYWORDS + ["gesamt"]):
            break
        if lang == "en" and any(k in low for k in EN_TOTAL_KEYWORDS + EN_VAT_KEYWORDS + ["total"]):
            break

        # Use heuristics to extract numbers: find numeric tokens and treat them as qty, unit, total (if present)
        nums = re.findall(r"([0-9]+(?:[.,][0-9]+)?)", ln)
        # Attempt to split description vs numeric tokens:
        if nums:
            # remove numeric tokens from description
            desc = re.sub(r"[0-9]+(?:[.,][0-9]+)?", "", ln).strip(" -:|,.")
            # If desc becomes empty, fallback to the whole line minus last numeric token
            if not desc:
                # try split by multiple spaces near numbers; fallback to line minus last num
                desc = ln.rsplit(nums[-1], 1)[0].strip()
            qty = normalize_number(nums[0], lang)
            unit_price = normalize_number(nums[1], lang) if len(nums) >= 2 else None
            line_total = normalize_number(nums[2], lang) if len(nums) >= 3 else None

            # If only two numbers and the second is clearly larger than first, treat as price/total heuristics
            if len(nums) == 2 and unit_price is not None and qty is not None and unit_price > qty:
                # ambiguous: assume qty, unit_price
                pass

            # Create InvoiceLineItem if we have at least description and qty
            if desc and qty is not None:
                try:
                    items.append(
                        InvoiceLineItem(
                            description=desc,
                            quantity=qty,
                            unit_price=unit_price,
                            line_total=line_total,
                        )
                    )
                except Exception as e:
                    logging.debug(f"Failed to create InvoiceLineItem for line `{ln}`: {e}")
                    continue
        else:
            # No numbers: could be multi-line description; skip for now
            continue

    return items


# ---------------- High-level API ----------------


def _extract_text_with_fallbacks(source: Union[str, Path, bytes, io.BytesIO]) -> str:
    """
    Attempt layout-aware text extraction first; if result is empty or clearly garbage,
    fallback to plain extract_text and then OCR fallback.
    """
    text = extract_text_layout_aware(source)
    if text and len(text.strip()) > 20:
        # Heuristic: if text contains many single characters or strange breaks, may still be bad.
        # But we'll accept for now. If too short, fallback to OCR.
        return text

    logging.info("Layout-aware extraction returned little text; trying OCR fallback.")
    ocr_text = _extract_text_with_ocr(source)
    return ocr_text


def extract_invoice_from_pdf(source: Union[str, Path, bytes, io.BytesIO]) -> Invoice:
    """
    Extract a single invoice from a PDF source and returns a Pydantic Invoice.
    """
    text = _extract_text_with_fallbacks(source)
    if not text:
        logging.warning("No text extracted from PDF (even after OCR). Returning empty invoice model.")
        # Build a minimal raw dict for Invoice model; adapt fields if your Invoice model differs.
        raw = {
            "invoice_number": None,
            "invoice_date": None,
            "due_date": None,
            "seller_name": None,
            "seller_tax_id": None,
            "buyer_name": None,
            "buyer_tax_id": None,
            "currency": None,
            "net_total": None,
            "tax_amount": None,
            "gross_total": None,
            "line_items": [],
        }
        return Invoice.parse_obj(raw)

    lang = detect_language(text)
    totals = _guess_totals(text, lang)
    dates = _guess_dates(text, lang)
    invoice_number = _guess_invoice_number(text, lang)
    line_items = _parse_line_items(text, lang)

    raw = {
        "invoice_number": invoice_number,
        "invoice_date": dates.get("invoice_date"),
        "due_date": dates.get("due_date"),
        "seller_name": None,
        "seller_tax_id": None,
        "buyer_name": None,
        "buyer_tax_id": None,
        "currency": "EUR" if "eur" in text.lower() or "€" in text else None,
        "net_total": totals.get("net_total"),
        "tax_amount": totals.get("tax_amount"),
        "gross_total": totals.get("gross_total"),
        "line_items": [item.dict() for item in line_items],
    }

    return Invoice.parse_obj(raw)


def extract_invoices_from_directory(pdf_dir: Union[str, Path]) -> List[Invoice]:
    directory = Path(pdf_dir)
    invoices: List[Invoice] = []
    for pdf_path in sorted(directory.glob("*.pdf")):
        try:
            logging.info(f"Processing {pdf_path}")
            invoices.append(extract_invoice_from_pdf(pdf_path))
        except Exception as e:
            logging.error(f"Failed to extract {pdf_path}: {e}")
    return invoices
