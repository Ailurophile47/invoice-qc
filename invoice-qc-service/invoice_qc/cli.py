"""
Command-line interface for the Invoice QC Service.

Usage examples:
    py -m invoice_qc.cli extract --pdf-dir sample_pdfs --output output/extracted.json
    py -m invoice_qc.cli validate --input output/extracted.json --report output/validation_report.json
    py -m invoice_qc.cli full-run --pdf-dir sample_pdfs --report output/validation_report.json
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Optional

import typer

from .extractor import extract_invoices_from_directory
from .schema import BulkValidationReport, Invoice
from .validator import validate_invoices

app = typer.Typer(help="Invoice extraction and validation CLI.")


def _ensure_parent_directory(path: Path) -> None:
    """
    Ensure the parent directory for a file path exists.
    """
    if path.parent and not path.parent.exists():
        path.parent.mkdir(parents=True, exist_ok=True)


@app.command()
def extract(
    pdf_dir: str = typer.Option(
        ...,
        "--pdf-dir",
        help="Directory containing PDF invoices to extract from.",
    ),
    output: str = typer.Option(
        "output/extracted.json",
        "--output",
        help="Path to write extracted invoice data as JSON.",
    ),
) -> None:
    """
    Extract structured data from PDF invoices in a directory.
    """
    pdf_directory = Path(pdf_dir)
    if not pdf_directory.exists() or not pdf_directory.is_dir():
        typer.echo(f"PDF directory not found: {pdf_directory}", err=True)
        raise typer.Exit(code=1)

    invoices = extract_invoices_from_directory(pdf_directory)

    output_path = Path(output)
    _ensure_parent_directory(output_path)
    with output_path.open("w", encoding="utf-8") as f:
        json.dump([inv.dict() for inv in invoices], f, indent=2, default=str)

    typer.echo(f"Extracted {len(invoices)} invoices to {output_path}")


@app.command()
def validate(
    input: str = typer.Option(
        "output/extracted.json",
        "--input",
        help="JSON file containing extracted invoices.",
    ),
    report: str = typer.Option(
        "output/validation_report.json",
        "--report",
        help="Path to write the validation report as JSON.",
    ),
) -> None:
    """
    Validate invoice JSON according to schema and business rules.
    """
    input_path = Path(input)
    if not input_path.exists():
        typer.echo(f"Input JSON not found: {input_path}", err=True)
        raise typer.Exit(code=1)

    invoices_data = json.loads(input_path.read_text(encoding="utf-8") or "[]")
    invoices = [Invoice.parse_obj(obj) for obj in invoices_data]

    report_obj: BulkValidationReport = validate_invoices(invoices)

    report_path = Path(report)
    _ensure_parent_directory(report_path)
    with report_path.open("w", encoding="utf-8") as f:
        json.dump(report_obj.dict(), f, indent=2, default=str)

    # Print summary to CLI
    summary = report_obj.summary
    typer.echo(f"Total invoices: {summary.total_invoices}")
    typer.echo(f"Valid invoices: {summary.valid_invoices}")
    typer.echo(f"Invalid invoices: {summary.invalid_invoices}")
    typer.echo(f"Top errors: {', '.join(summary.top_errors) if summary.top_errors else 'None'}")

    # Exit non-zero if there are invalid invoices
    if summary.invalid_invoices > 0:
        raise typer.Exit(code=2)


@app.command("full-run")
def full_run(
    pdf_dir: str = typer.Option(
        ...,
        "--pdf-dir",
        help="Directory containing PDF invoices to process.",
    ),
    report: str = typer.Option(
        "output/validation_report.json",
        "--report",
        help="Path to write the validation report as JSON.",
    ),
) -> None:
    """
    Perform extraction and validation in a single command.
    """
    temp_extracted_path = Path("output/extracted.json")
    _ensure_parent_directory(temp_extracted_path)

    # Step 1: Extract
    invoices = extract_invoices_from_directory(pdf_dir)
    with temp_extracted_path.open("w", encoding="utf-8") as f:
        json.dump([inv.dict() for inv in invoices], f, indent=2, default=str)

    # Step 2: Validate
    report_obj = validate_invoices(invoices)
    report_path = Path(report)
    _ensure_parent_directory(report_path)
    with report_path.open("w", encoding="utf-8") as f:
        json.dump(report_obj.dict(), f, indent=2, default=str)

    summary = report_obj.summary
    typer.echo(f"Total invoices: {summary.total_invoices}")
    typer.echo(f"Valid invoices: {summary.valid_invoices}")
    typer.echo(f"Invalid invoices: {summary.invalid_invoices}")
    typer.echo(f"Top errors: {', '.join(summary.top_errors) if summary.top_errors else 'None'}")

    if summary.invalid_invoices > 0:
        raise typer.Exit(code=2)


def main() -> None:
    """
    Entrypoint used when executing as a module.
    """
    app()


if __name__ == "__main__":
    main()


