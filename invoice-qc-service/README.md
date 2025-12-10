Invoice QC Service

A complete Invoice Extraction & Quality Control Service that reads invoice PDFs, extracts structured JSON data, validates the data using custom rules, and exposes both a CLI tool and a FastAPI backend.

1. Overview

This system performs:

PDF → JSON extraction

Schema-based validation

Rule-based Quality Control

CLI automation

HTTP API for integration

(Bonus ready) A UI can be added later.

This project simulates an internal B2B invoice processing tool.

2. Schema & Validation Design
2.1 Invoice Schema (Fields)
Field	Description
invoice_number	Unique invoice identifier
invoice_date	Date invoice issued
due_date	Payment due date
seller_name	Seller company name
seller_tax_id	Seller tax identifier (GST/VAT)
buyer_name	Buyer company name
buyer_tax_id	Buyer tax ID
currency	INR/EUR/USD etc.
net_total	Total before taxes
tax_amount	Tax amount
gross_total	Final payable amount
line_items	List of invoice items
Line Item Fields

description

quantity

unit_price

line_total

2.2 Validation Rules
Completeness & Format Rules

invoice_number must not be empty

invoice_date must be parseable

seller_name and buyer_name must not be empty

Business Rules

net_total + tax_amount ≈ gross_total (tolerance ±0.5)

sum(line_items.line_total) ≈ net_total

Anomaly Rules

Totals must not be negative

Detect duplicate invoice_number + seller_name

These ensure correctness and basic fraud/error detection.

3. Architecture
3.1 Overall Flow
PDFs → extractor.py → extracted.json → validator.py → validation_report.json → API/CLI/UI

3.2 File Structure
invoice-qc-service/
├── README.md
├── requirements.txt
├── invoice_qc/
│   ├── __init__.py
│   ├── schema.py
│   ├── extractor.py
│   ├── validator.py
│   ├── cli.py
│   └── api/
│       ├── __init__.py
│       └── main.py
├── sample_pdfs/
├── output/
│   ├── extracted.json
│   └── validation_report.json
└── ai-notes/

4. Components
4.1 Extraction Module (extractor.py)

Uses pdfplumber

Extracts text → regex → fields

Handles missing values gracefully

Outputs list of invoices in JSON

4.2 Validation Module (validator.py)

Validates each invoice

Returns:

per-invoice errors

global summary

Detects duplicates

4.3 CLI (cli.py)

Commands supported:

extract
validate
full-run


Prints:

total invoices

valid / invalid count

top errors

4.4 HTTP API (FastAPI)

Endpoints:

GET /health
POST /validate-json
POST /extract-and-validate-pdfs (optional)


Uses validator internally.

5. Setup & Installation
1. Create virtual env
python -m venv venv
source venv/bin/activate    # Mac/Linux
venv\Scripts\activate       # Windows

2. Install dependencies
pip install -r requirements.txt

6. Running the CLI
Extract PDFs
python -m invoice_qc.cli extract \
  --pdf-dir sample_pdfs \
  --output output/extracted.json

Validate JSON
python -m invoice_qc.cli validate \
  --input output/extracted.json \
  --report output/validation_report.json

Full Run
python -m invoice_qc.cli full-run \
  --pdf-dir sample_pdfs \
  --report output/validation_report.json

7. Running the API

Start the FastAPI server:

uvicorn invoice_qc.api.main:app --reload

Example call:
POST http://localhost:8000/validate-json

8. AI Usage Notes

Tools Used:

ChatGPT for schema brainstorming & regex ideas

Cursor for code generation skeletons

Example issue:

One suggested regex for invoice date extraction matched too many unrelated numbers; modified it to restrict DD/MM/YYYY and YYYY-MM-DD formats.

9. Assumptions & Limitations

PDFs assumed to contain clean text extractable by pdfplumber.

Line items support basic table structures only.

Totals tolerance set to ±0.5 for floating precision issues.

Real-world invoice formats vary heavily; this is a simplified academic version.