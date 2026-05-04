# usda-nfc-les-parser

A Python tool for parsing USDA NFC Earnings & Leave Statements (Form AD-334) from PDF into structured JSON or CSV.

Built for federal employees and developers who want machine-readable pay data from NFC-processed statements available via [Employee Personal Page (EPP)](https://www.nfc.usda.gov/epps/).

---

## Features

- Extracts all core financial fields: gross pay, net pay, total deductions
- Captures all standard deduction line items (retirement, TSP, taxes, health, etc.)
- Captures leave earnings (annual, sick, comp, credit hours)
- YTD leave status (accrued / used / balance)
- Financial validation: confirms `gross == net + deductions`
- Diagnostic fields: missing field detection, deduction/leave item counts
- Batch mode: parse an entire folder of PDFs at once
- CSV export with flattened deduction and leave columns
- CLI and importable Python module

---

## Requirements

```bash
pip install pdfplumber pandas
```

> **Note:** This parser requires text-based PDFs. Statements downloaded directly from EPP are text-based and work out of the box. Scanned/printed-and-photographed PDFs are not supported without OCR (see [Scanned PDFs](#scanned-pdfs)).

---

## Usage

### Command Line

```bash
# Single file — JSON to stdout
python usda_nfc_les_parser.py statement.pdf

# Single file — save JSON
python usda_nfc_les_parser.py statement.pdf --out-file parsed.json

# Single file — CSV to stdout
python usda_nfc_les_parser.py statement.pdf --output csv

# Batch folder — save CSV
python usda_nfc_les_parser.py ./statements/ --batch --output csv --out-file all_periods.csv

# Debug/verbose logging
python usda_nfc_les_parser.py statement.pdf --verbose
```

### As a Python Module

```python
from usda_nfc_les_parser import parse_les, parse_folder

# Single file
result = parse_les("statement.pdf")
print(result["net_pay_pp"])

# Batch
results = parse_folder("./statements/")
for r in results:
    print(r["pay_period_start"], r["net_pay_pp"])
```

---

## Output Fields

### Top-Level

| Field | Description | Example |
|---|---|---|
| `_source_file` | PDF filename | `"statement.pdf"` |
| `pay_period_start` | Pay period start date | `"04/05/2026"` |
| `pay_period_end` | Pay period end date | `"04/18/2026"` |
| `official_pay_date` | Official disbursement date | `"04/30/2026"` |
| `salary_rate` | Annual/hourly salary rate | `"123,456.00"` |
| `pay_type_code` | Pay type code | `"PA"` |
| `pay_type_label` | Pay type description | `"Per Annum"` |
| `gross_pay_hours` | Hours worked this pay period | `"80.00"` |
| `gross_pay_pp` | Gross pay this pay period | `"1,234.00"` |
| `gross_pay_ytd` | Gross pay year to date | `"12,345.00"` |
| `net_pay_pp` | Net pay this pay period | `"1,234.00"` |
| `net_pay_ytd` | Net pay year to date | `"12,234.00"` |
| `total_deductions_pp` | Total deductions this pay period | `"1,234.00"` |
| `total_deductions_ytd` | Total deductions year to date | `"12,345.00"` |

### Pay Type Codes

| Code | Description |
|---|---|
| `PA` | Per Annum |
| `PH` | Per Hour |
| `SES` | Senior Executive Service |
| `AD` | Administratively Determined |
| `EX` | Executive Schedule |
| `SL` | Senior Level |

### `deductions[]`

Array of deduction line items found on the statement. Only deductions present on the statement are included — optional deductions (e.g. HSA, 401k) will not appear if the employee is not enrolled.

| Field | Description |
|---|---|
| `item` | NFC item code |
| `code` | NFC sub-code |
| `description` | Human-readable label |
| `pp_amount` | Amount this pay period |
| `ytd_amount` | Amount year to date |

**Supported deduction codes:**

| Item | Code | Description |
|---|---|---|
| 75 | 02 | Retirement (FERS) |
| 75 | 15 | TSP-FERS |
| 75 | 16 | TSP-FERS Catch-Up |
| 75 | 17 | 401(K) |
| 75 | 18 | 401(K) Catch-Up |
| 75 | 25 | 401(K) Taxable |
| 76 | — | Social Security (OASDI) |
| 77 | — | Federal Income Tax |
| 78 | — | State Income Tax |
| 83 | — | FEHBA Health Insurance |
| 86 | — | Dental/Vision |
| 88 | 60 | Health Savings Account |
| 89 | — | Flexible Spending Account |
| 97 | — | Medicare Tax |

### `leave_earnings[]`

Array of earnings/leave line items from the earnings section.

| Field | Description |
|---|---|
| `code` | NFC item code |
| `description` | Leave/earnings type |
| `hours` | Hours this pay period |
| `amount` | Dollar amount this pay period |

**Supported leave earnings codes:**

| Code | Description |
|---|---|
| 50 | Credit Hours |
| 51 | Separation Maintenance Allowance (Taxable) |
| 52 | Cycle Program Earnings |
| 61 | Annual Leave |
| 62 | Sick Leave |
| 64 | Compensatory Leave |
| 66 | Other Leave |

### `ytd_leave_status[]`

Year-to-date leave balances from the bottom table of the statement.

| Field | Description |
|---|---|
| `type` | Leave type (ANNUAL, SICK, COMP, etc.) |
| `accrued` | Hours accrued YTD |
| `used` | Hours used YTD |
| `balance` | Current balance |

### Diagnostic Fields

| Field | Type | Description |
|---|---|---|
| `_missing_fields` | list | Required fields not found in the PDF |
| `_financials_balance` | bool / null | `true` if gross == net + deductions; `null` if unverifiable |
| `_deductions_found` | int | Number of deduction line items extracted |
| `_leave_items_found` | int | Number of leave earnings line items extracted |
| `_error` | string | Present only on parse failure; describes the error |

---

## Compatibility

This parser targets statements generated by the USDA National Finance Center (NFC) and processed through EPP. It has been tested against the AD-334 (Rev. 8/17) form layout.

| Agency Payroll System | Compatible |
|---|---|
| USDA NFC (EPP) | ✅ Yes |
| DFAS (military LES) | ❌ No — different format |
| GSA Payroll (non-NFC) | ⚠️ Untested |
| Other NFC-serviced agencies | ✅ Likely (same form) |

NFC processes payroll for multiple federal agencies beyond USDA. Any agency using NFC/EPP should produce compatible AD-334 statements.

---

## Scanned PDFs

Statements downloaded directly from EPP are text-based and parse cleanly. If you have a scanned or photographed copy, `extract_text()` will return empty and the parser will exit with:

```
{"_error": "PDF appears to be scanned/image-based. OCR required (pytesseract)."}
```

OCR support is not included but can be added with `pytesseract` + `pdf2image`:

```bash
pip install pytesseract pdf2image
brew install tesseract  # macOS
# apt install tesseract-ocr  # Linux
```

---

## Privacy Notice

Earnings & Leave Statements contain sensitive personal information including masked SSNs, name, address, and salary data. 

- **Do not commit real PDF statements to version control**
- Use redacted or synthetically generated PDFs for testing
- The parser does not transmit any data externally

---

## License

MIT

---

## Contributing

Issues and PRs welcome. If you encounter a statement layout that doesn't parse correctly, please open an issue with a **fully redacted** text sample (run `pdfplumber` and paste `extract_text()` output with PII removed).
