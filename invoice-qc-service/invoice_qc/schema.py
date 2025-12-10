"""
Data models and schema definitions for invoices and validation output.

All external components (extractor, validator, API, CLI) should use these
Pydantic models to ensure a consistent contract.
"""

from __future__ import annotations

from datetime import date
from typing import List, Optional

from pydantic import BaseModel, Field, validator


class InvoiceLineItem(BaseModel):
    """
    Represents a single line item in an invoice.
    """

    description: Optional[str] = Field(
        default=None, description="Human-readable description of the line item."
    )
    quantity: Optional[float] = Field(
        default=None, description="Quantity of the item or service."
    )
    unit_price: Optional[float] = Field(
        default=None, description="Unit price for the item or service."
    )
    line_total: Optional[float] = Field(
        default=None, description="Total for this line (quantity * unit_price)."
    )

    @validator("line_total", always=True)
    def compute_line_total(cls, v, values):
        """
        If line_total is missing but quantity and unit_price are available,
        compute line_total automatically.
        """
        if v is not None:
            return v
        qty = values.get("quantity")
        price = values.get("unit_price")
        if qty is not None and price is not None:
            try:
                return float(qty) * float(price)
            except (TypeError, ValueError):
                return None
        return None


class Invoice(BaseModel):
    """
    Core invoice schema used across the service.

    Many fields are optional at this stage because the extractor may not be able
    to populate everything. The validator is responsible for enforcing
    completeness and business rules.
    """

    invoice_number: Optional[str] = Field(
        default=None, description="Invoice identifier as shown on the document."
    )
    invoice_date: Optional[date] = Field(
        default=None,
        description="Invoice issue date. Parsed into a date when possible.",
    )
    due_date: Optional[date] = Field(
        default=None,
        description="Payment due date. Parsed into a date when possible.",
    )
    seller_name: Optional[str] = Field(
        default=None, description="Legal name of the seller."
    )
    seller_tax_id: Optional[str] = Field(
        default=None, description="Tax identifier of the seller."
    )
    buyer_name: Optional[str] = Field(
        default=None, description="Legal name of the buyer."
    )
    buyer_tax_id: Optional[str] = Field(
        default=None, description="Tax identifier of the buyer."
    )
    currency: Optional[str] = Field(
        default=None, description="Currency code, e.g. 'USD', 'EUR'."
    )
    net_total: Optional[float] = Field(
        default=None, description="Net total amount before tax."
    )
    tax_amount: Optional[float] = Field(
        default=None, description="Total tax amount."
    )
    gross_total: Optional[float] = Field(
        default=None, description="Total amount including tax."
    )
    line_items: List[InvoiceLineItem] = Field(
        default_factory=list, description="List of line items on the invoice."
    )

    class Config:
        orm_mode = True


class InvoiceValidationError(BaseModel):
    """
    Represents a single validation error for an invoice.
    """

    code: str = Field(..., description="Short machine-readable error code.")
    message: str = Field(..., description="Human-readable description of the error.")
    field: Optional[str] = Field(
        default=None,
        description="Optional field name associated with the error (if applicable).",
    )


class InvoiceValidationResult(BaseModel):
    """
    Validation result for a single invoice.
    """

    invoice_id: Optional[str] = Field(
        default=None,
        description="Identifier for the invoice, usually the invoice_number.",
    )
    is_valid: bool = Field(..., description="True if invoice passed all checks.")
    errors: List[InvoiceValidationError] = Field(
        default_factory=list, description="List of validation errors."
    )


class ValidationSummary(BaseModel):
    """
    Aggregate summary for validating multiple invoices.
    """

    total_invoices: int = Field(..., description="Total number of invoices checked.")
    valid_invoices: int = Field(
        ..., description="Number of invoices that passed all checks."
    )
    invalid_invoices: int = Field(
        ..., description="Number of invoices that failed at least one check."
    )
    top_errors: List[str] = Field(
        default_factory=list,
        description=(
            "List of most common error messages or codes across all invoices."
        ),
    )


class BulkValidationReport(BaseModel):
    """
    Structure used when returning a full validation report for many invoices.
    """

    results: List[InvoiceValidationResult] = Field(
        default_factory=list, description="Per-invoice validation results."
    )
    summary: ValidationSummary = Field(
        ..., description="High-level validation statistics."
    )


