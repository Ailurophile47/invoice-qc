"""
Invoice validation logic.

This module implements:
- Completeness and format checks for key fields
- Business rules (totals consistency)
- Anomaly detection rules (negative totals, duplicates)

The main entrypoints are:
- `validate_invoice` for a single invoice
- `validate_invoices` for a batch, including a summary
"""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date, datetime
from typing import Dict, Iterable, List, Tuple

from .schema import (
    BulkValidationReport,
    Invoice,
    InvoiceValidationError,
    InvoiceValidationResult,
    ValidationSummary,
)


TOLERANCE = 0.5


def _parse_maybe_date(value) -> date | None:
    """
    Best-effort parser for date-like values.
    Accepts either already-parsed date objects or common string formats.
    """
    if value is None:
        return None
    if isinstance(value, date):
        return value
    if isinstance(value, datetime):
        return value.date()
    if not isinstance(value, str):
        return None

    # Try a couple of common formats.
    for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%Y/%m/%d", "%m/%d/%Y"):
        try:
            return datetime.strptime(value.strip(), fmt).date()
        except ValueError:
            continue
    return None


def _almost_equal(a: float | None, b: float | None, tol: float = TOLERANCE) -> bool:
    """
    Check if two numbers are equal within a given tolerance.
    """
    if a is None or b is None:
        return False
    return abs(a - b) <= tol


def validate_invoice(invoice: Invoice) -> InvoiceValidationResult:
    """
    Validate a single invoice against completeness, business, and anomaly rules.

    Parameters
    ----------
    invoice:
        Invoice instance to validate.

    Returns
    -------
    InvoiceValidationResult
        Result indicating validity and list of errors.
    """
    errors: List[InvoiceValidationError] = []

    # --- Completeness / Format rules ---
    if not invoice.invoice_number or not str(invoice.invoice_number).strip():
        errors.append(
            InvoiceValidationError(
                code="MISSING_INVOICE_NUMBER",
                field="invoice_number",
                message="Invoice number must not be empty.",
            )
        )

    parsed_invoice_date = _parse_maybe_date(invoice.invoice_date)
    if invoice.invoice_date is not None and parsed_invoice_date is None:
        errors.append(
            InvoiceValidationError(
                code="INVALID_INVOICE_DATE",
                field="invoice_date",
                message="Invoice date could not be parsed.",
            )
        )

    if not (invoice.seller_name and str(invoice.seller_name).strip()):
        errors.append(
            InvoiceValidationError(
                code="MISSING_SELLER_NAME",
                field="seller_name",
                message="Seller name must not be empty.",
            )
        )

    if not (invoice.buyer_name and str(invoice.buyer_name).strip()):
        errors.append(
            InvoiceValidationError(
                code="MISSING_BUYER_NAME",
                field="buyer_name",
                message="Buyer name must not be empty.",
            )
        )

    # --- Business rules ---
    if (
        invoice.net_total is not None
        and invoice.tax_amount is not None
        and invoice.gross_total is not None
    ):
        if not _almost_equal(
            invoice.net_total + invoice.tax_amount, invoice.gross_total
        ):
            errors.append(
                InvoiceValidationError(
                    code="TOTAL_MISMATCH",
                    field="gross_total",
                    message=(
                        "net_total + tax_amount should equal gross_total within "
                        f"{TOLERANCE} tolerance."
                    ),
                )
            )

    # Sum of line item totals vs net_total
    if invoice.line_items and invoice.net_total is not None:
        line_sum = sum(
            item.line_total or 0.0 for item in invoice.line_items if item.line_total
        )
        if not _almost_equal(line_sum, invoice.net_total):
            errors.append(
                InvoiceValidationError(
                    code="LINE_ITEMS_TOTAL_MISMATCH",
                    field="line_items",
                    message=(
                        "Sum of line item totals should equal net_total "
                        f"within {TOLERANCE} tolerance."
                    ),
                )
            )

    # --- Anomaly rules ---
    for field_name in ("net_total", "tax_amount", "gross_total"):
        value = getattr(invoice, field_name)
        if value is not None and value < 0:
            errors.append(
                InvoiceValidationError(
                    code="NEGATIVE_TOTAL",
                    field=field_name,
                    message=f"{field_name} must not be negative.",
                )
            )

    is_valid = len(errors) == 0
    return InvoiceValidationResult(
        invoice_id=invoice.invoice_number,
        is_valid=is_valid,
        errors=errors,
    )


def _detect_duplicates(
    invoices: Iterable[Invoice],
) -> Dict[Tuple[str | None, str | None], int]:
    """
    Detect duplicate (invoice_number, seller_name) pairs.
    Returns a counter dict keyed by (invoice_number, seller_name).
    """
    counter: Counter = Counter()
    for inv in invoices:
        key = (inv.invoice_number or None, inv.seller_name or None)
        counter[key] += 1
    return {k: c for k, c in counter.items() if c > 1}


def validate_invoices(invoices: List[Invoice]) -> BulkValidationReport:
    """
    Validate a list of invoices and return detailed results plus a summary.

    This function also applies duplicate detection for the
    (invoice_number, seller_name) combination.
    """
    per_invoice_results: List[InvoiceValidationResult] = []
    duplicates = _detect_duplicates(invoices)

    # Pre-build a lookup for faster duplicate detection annotation
    duplicate_keys = {k for k, count in duplicates.items() if count > 1}

    for invoice in invoices:
        result = validate_invoice(invoice)
        key = (invoice.invoice_number or None, invoice.seller_name or None)
        if key in duplicate_keys:
            result.errors.append(
                InvoiceValidationError(
                    code="DUPLICATE_INVOICE",
                    field="invoice_number",
                    message=(
                        "Duplicate combination of invoice_number and seller_name "
                        "detected across the dataset."
                    ),
                )
            )
            result.is_valid = False
        per_invoice_results.append(result)

    total_invoices = len(per_invoice_results)
    invalid_invoices = sum(1 for r in per_invoice_results if not r.is_valid)
    valid_invoices = total_invoices - invalid_invoices

    # Compute top error codes/messages
    error_counter: Counter = Counter()
    for r in per_invoice_results:
        for e in r.errors:
            error_counter[e.code] += 1

    top_errors = [code for code, _ in error_counter.most_common(5)]

    summary = ValidationSummary(
        total_invoices=total_invoices,
        valid_invoices=valid_invoices,
        invalid_invoices=invalid_invoices,
        top_errors=top_errors,
    )

    return BulkValidationReport(results=per_invoice_results, summary=summary)


