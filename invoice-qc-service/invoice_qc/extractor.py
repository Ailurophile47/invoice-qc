"""
PDF invoice extraction utilities using pdfplumber.

The goal is to provide a simple, reasonably robust baseline extractor that:
- Loads a PDF
- Extracts raw text
- Uses regular expressions and heuristics to find core invoice fields
- Optionally parses very simple line item tables

The output of this module should always be compatible with the `Invoice`
Pydantic model defined in `invoice_qc.schema`.
"""

from __future__ import annotations

import io
import re
import logging
from pathlib import Path
from typing import Dict, List, Optional, Union

import pdfplumber
import pytesseract
from pdf2image import convert_from_path

from .schema import Invoice, InvoiceLineItem

# ------------------ Configure Logging ------------------ #

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# ------------------ Patterns ------------------ #

INVOICE_NUMBER_PATTERN = re.compile(
    r"(?:Invoice\s*(?:No\.?|Number)?\s*[:#]?\s*)([A-Za-z0-9\-_/]+)", re.IGNORECASE
)

DATE_PATTERN = re.compile(
    r"(\d{4}[-/]\d{1,2}[-/]\d{1,2}|\d{1,2}[-/]\d{1,2}[-/]\d{2,4})"
)

TOTAL_PATTERN = re.compile(
    r"(?:Total\s*(?:Amount)?\s*[:]?|\bGross\b|\bNet\b|\bTax\b)\s*([0-9.,]+)",
    re.IGNORECASE,
)

CURRENCY_PATTERN = re.compile(
    r"\b(USD|EUR|GBP|INR|JPY|CNY|AUD|CAD|CHF|SEK|NOK|ZAR)\b", re.IGNORECASE
)


# ------------------ Enhanced Text Extraction ------------------ #

def pdf_plumber_open(obj):
    """
    Small wrapper for pdfplumber.open to make it easy to patch or extend later.
    """
    return pdfplumber.open(obj)


def _extract_text_from_pdf(
    source: Union[str, Path, bytes, io.BytesIO]
) -> str:
    """
    Extract text from all pages of a PDF using pdfplumber.
    Fallback to OCR if no text is extracted.
    """
    try:
        # Path-like
        if isinstance(source, (str, Path)):
            pdf_path = Path(source)
            with pdf_plumber_open(pdf_path) as pdf:
                texts = [(page.extract_text() or "") for page in pdf.pages]
            extracted_text = "\n".join(texts)
        else:
            # Bytes or file-like
            buffer = io.BytesIO(source) if isinstance(source, (bytes, bytearray)) else source
            with pdf_plumber_open(buffer) as pdf:
                texts = [(page.extract_text() or "") for page in pdf.pages]
            extracted_text = "\n".join(texts)

        if not extracted_text.strip():
            logging.warning("No text extracted using pdfplumber. Falling back to OCR.")
            extracted_text = _extract_text_with_ocr(source)

        return extracted_text

    except Exception as e:
        logging.error(f"Failed to extract text from PDF: {e}")
        raise


def _extract_text_with_ocr(source: Union[str, Path, bytes, io.BytesIO]) -> str:
    """
    Extract text from a PDF using OCR as a fallback.
    """
    try:
        if isinstance(source, (str, Path)):
            images = convert_from_path(source)
        else:
            buffer = io.BytesIO(source) if isinstance(source, (bytes, bytearray)) else source
            images = convert_from_path(buffer)

        ocr_text = "\n".join([pytesseract.image_to_string(image) for image in images])
        return ocr_text

    except Exception as e:
        logging.error(f"OCR extraction failed: {e}")
        return ""


# ------------------ Utils ------------------ #

def _safe_parse_float(value: Optional[str]) -> Optional[float]:
    """Convert numeric string → float."""
    if not value:
        return None
    try:
        return float(value.replace(",", "").strip())
    except Exception:
        return None


def _guess_invoice_number(text: str) -> Optional[str]:
    match = INVOICE_NUMBER_PATTERN.search(text)
    return match.group(1).strip() if match else None


def _guess_dates(text: str) -> Dict[str, Optional[str]]:
    matches = DATE_PATTERN.findall(text)
    # DATE_PATTERN returns strings, not tuples
    invoice_date = matches[0] if matches else None
    due_date = matches[1] if len(matches) > 1 else None
    return {"invoice_date": invoice_date, "due_date": due_date}


def _guess_currency(text: str) -> Optional[str]:
    match = CURRENCY_PATTERN.search(text)
    return match.group(1).upper() if match else None


def _guess_totals(text: str) -> Dict[str, Optional[float]]:
    net_total = tax_amount = gross_total = None

    for line in text.splitlines():
        numbers = re.findall(r"([0-9.,]+)", line)
        if not numbers:
            continue

        value = _safe_parse_float(numbers[-1])
        if value is None:
            continue

        lower = line.lower()
        if "net" in lower and net_total is None:
            net_total = value
        elif "tax" in lower and tax_amount is None:
            tax_amount = value
        elif ("gross" in lower or "total" in lower) and gross_total is None:
            gross_total = value

    return {
        "net_total": net_total,
        "tax_amount": tax_amount,
        "gross_total": gross_total,
    }


def _parse_simple_line_items(text: str) -> List[InvoiceLineItem]:
    """
    Extremely simple table parser.
    Only lines with ≥3 numbers are considered valid line items.
    """
    items = []

    for line in text.splitlines():
        numbers = re.findall(r"([0-9]+(?:[.,][0-9]+)?)", line)

        if len(numbers) < 2:
            continue

        desc = re.sub(r"[0-9]+(?:[.,][0-9]+)?", "", line).strip()

        qty = _safe_parse_float(numbers[0])
        unit_price = _safe_parse_float(numbers[1]) if len(numbers) >= 2 else None
        line_total = _safe_parse_float(numbers[2]) if len(numbers) >= 3 else None

        # Heuristic filters
        if not desc:
            continue
        if qty is None:
            continue

        items.append(
            InvoiceLineItem(
                description=desc,
                quantity=qty,
                unit_price=unit_price,
                line_total=line_total,
            )
        )

    return items


# ------------------ High-level APIs ------------------ #

def extract_invoice_from_pdf(
    source: Union[str, Path, bytes, io.BytesIO]
) -> Invoice:
    """
    Extract a single invoice from a PDF.
    """
    text = _extract_text_from_pdf(source)
    dates = _guess_dates(text)

    raw = {
        "invoice_number": _guess_invoice_number(text),
        "invoice_date": dates["invoice_date"],
        "due_date": dates["due_date"],
        "seller_name": None,
        "seller_tax_id": None,
        "buyer_name": None,
        "buyer_tax_id": None,
        "currency": _guess_currency(text),
        "net_total": _guess_totals(text)["net_total"],
        "tax_amount": _guess_totals(text)["tax_amount"],
        "gross_total": _guess_totals(text)["gross_total"],
        "line_items": [item.dict() for item in _parse_simple_line_items(text)],
    }

    return Invoice.parse_obj(raw)


def extract_invoices_from_directory(pdf_dir: Union[str, Path]) -> List[Invoice]:
    """
    Extract all invoices in a directory.
    """
    directory = Path(pdf_dir)
    invoices = []

    for pdf_path in sorted(directory.glob("*.pdf")):
        try:
            logging.info(f"Processing file: {pdf_path}")
            invoices.append(extract_invoice_from_pdf(pdf_path))
        except Exception as e:
            logging.error(f"Failed to process {pdf_path}: {e}")

    return invoices


