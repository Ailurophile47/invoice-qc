"""
FastAPI application for the Invoice QC Service.

Endpoints
---------
- GET /health
- POST /validate-json
- POST /extract-and-validate-pdfs  (optional, uses uploaded PDF files)
"""

from __future__ import annotations

import io
from typing import List

from fastapi import FastAPI, File, UploadFile
from fastapi.middleware.cors import CORSMiddleware

from ..extractor import extract_invoice_from_pdf
from ..schema import BulkValidationReport, Invoice
from ..validator import validate_invoices

app = FastAPI(title="Invoice QC Service", version="1.0.0")

# Basic CORS configuration (can be tightened in production)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
async def health() -> dict:
    """
    Simple health-check endpoint.
    """
    return {"status": "ok"}


@app.post("/validate-json", response_model=BulkValidationReport)
async def validate_json(invoices: List[Invoice]) -> BulkValidationReport:
    """
    Validate JSON payload representing one or more invoices.

    Body should be a JSON array of invoice objects following the Invoice schema.
    """
    report = validate_invoices(invoices)
    return report


@app.post("/extract-and-validate-pdfs", response_model=BulkValidationReport)
async def extract_and_validate_pdfs(
    files: List[UploadFile] = File(..., description="One or more PDF invoice files."),
) -> BulkValidationReport:
    """
    Optional endpoint to upload PDF files, extract invoice data and validate it.
    """
    invoices: List[Invoice] = []
    for file in files:
        content = await file.read()
        buffer = io.BytesIO(content)
        invoice = extract_invoice_from_pdf(buffer)
        # Use uploaded filename as a fallback invoice identifier
        if not invoice.invoice_number:
            invoice.invoice_number = file.filename
        invoices.append(invoice)

    return validate_invoices(invoices)


# For local development convenience:
#   uvicorn invoice_qc.api.main:app --reload


