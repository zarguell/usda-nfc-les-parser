"""
usda_nfc_les_parser.py

Parser for USDA NFC Earnings & Leave Statements (AD-334 form).
Extracts structured financial and leave data from PDF format.

Usage:
    python usda_nfc_les_parser.py statement.pdf
    python usda_nfc_les_parser.py statement.pdf --output json
    python usda_nfc_les_parser.py statement.pdf --output csv
    python usda_nfc_les_parser.py /path/to/folder/ --batch --output csv

Requirements:
    pip install pdfplumber pandas
"""

import re
import json
import sys
import logging
from pathlib import Path
from decimal import Decimal, InvalidOperation

try:
    import pdfplumber
except ImportError:
    sys.exit("Missing dependency: pip install pdfplumber")

try:
    import pandas as pd
    PANDAS_AVAILABLE = True
except ImportError:
    PANDAS_AVAILABLE = False

logging.basicConfig(level=logging.WARNING, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Constants — NFC AD-334 standard item codes
# ---------------------------------------------------------------------------

DEDUCTION_PATTERNS = [
    ("75", "02", "RETIREMENT",              r"RETIREMENT"),
    ("75", "15", "TSP-FERS",                r"TSP-FERS"),
    ("75", "16", "TSP-FERS CATCH-UP",       r"TSP-FERS CATCH-UP"),
    ("75", "17", "401(K)",                  r"401\(K\)(?!\(TAXABLE\))"),
    ("75", "18", "401(K) CATCH-UP",         r"401\(K\) CATCH-UP"),
    ("75", "25", "401(K)(TAXABLE)",         r"401\(K\)\(TAXABLE\)"),
    ("76", "",   "SOCIAL SECURITY (OASDI)", r"SOCIAL SECURITY \(OASDI\)"),
    ("77", "",   "FEDERAL TAX",             r"FEDERAL TAX"),
    ("78", "",   "STATE TAX",               r"ST TAX"),
    ("83", "",   "FEHBA",                   r"FEHBA"),
    ("86", "",   "DENTAL/VISION",           r"DENTAL.VISION"),
    ("88", "60", "HEALTH SAVINGS ACCOUNT",  r"HEALTH SAVINGS"),
    ("89", "",   "FLEXIBLE SPENDING",       r"FLEXIBLE SPENDING"),
    ("97", "",   "MEDICARE TAX",            r"MEDICARE TAX"),
]

LEAVE_EARNINGS_PATTERNS = [
    ("50", "CREDIT HOURS"),
    ("51", "SEP MNTCE ALLOW TAXABLE"),
    ("52", "CYCLE PROGRAM"),
    ("61", "ANNUAL LEAVE"),
    ("62", "SICK LEAVE"),
    ("64", "COMPENSATORY LEAVE"),
    ("66", "OTHER LEAVE"),
]

# Some earnings items (e.g., 51, 52) appear with a single dollar amount instead of
# hours+amount.  These are matched separately by a broader regex.
SINGLE_AMOUNT_EARNINGS_CODES = [
    ("51", "SEP MNTCE ALLOW TAXABLE"),
    ("52", "CYCLE PROGRAM"),
    ("44", "CASH AWARD"),
    ("44", "QSI"),
]

YTD_LEAVE_TYPES = [
    "ANN",
    "SICK",
    "COMP",
    "MILITARY",
    "TIME OFF AWARD",
    "CREDIT HOURS-BY PAY PERIOD",
    "RELIGIOUS COMP-BY PAY PERIOD",
    "TRAVEL COMP-BY PAY PERIOD",
    "BPAPRA COMPENSATORY",
    "BPAPRA OBLIGATED DEBT",
    "DISABLED VETERAN LEAVE",
]

PAY_TYPES = {
    "PA":  "Per Annum",
    "PH":  "Per Hour",
    "SES": "Senior Executive Service",
    "AD":  "Administratively Determined",
    "EX":  "Executive Schedule",
    "SL":  "Senior Level",
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _clean_amount(s):
    try:
        return Decimal(s.replace(",", ""))
    except (InvalidOperation, AttributeError):
        return None


def _validate_financials(data):
    gross = _clean_amount(data.get("gross_pay_pp"))
    net   = _clean_amount(data.get("net_pay_pp"))
    deds  = _clean_amount(data.get("total_deductions_pp"))
    if None in (gross, net, deds):
        return None
    return gross == net + deds


# ---------------------------------------------------------------------------
# Core parser
# ---------------------------------------------------------------------------

def parse_les(pdf_path):
    """
    Parse a USDA NFC Earnings & Leave Statement PDF.

    Args:
        pdf_path (str | Path): Path to the PDF file.

    Returns:
        dict: Structured pay data, or dict with '_error' key on failure.
    """
    pdf_path = Path(pdf_path)
    if not pdf_path.exists():
        return {"_error": f"File not found: {pdf_path}"}
    if pdf_path.suffix.lower() != ".pdf":
        return {"_error": f"Not a PDF file: {pdf_path}"}

    try:
        with pdfplumber.open(pdf_path) as pdf:
            pages = [p.extract_text() or "" for p in pdf.pages]
    except Exception as e:
        return {"_error": f"Could not open PDF: {e}"}

    text = "\n".join(pages)

    if not text.strip():
        return {"_error": "PDF appears to be scanned/image-based. OCR required (pytesseract)."}

    if "NFC" not in text and "AD-334" not in text and "EARNINGS AND LEAVE" not in text:
        logger.warning("Document may not be a USDA NFC earnings & leave statement.")

    data = {"_source_file": pdf_path.name}

    # -----------------------------------------------------------------------
    # Pay period dates
    # -----------------------------------------------------------------------
    m = re.search(r'(\d{2}/\d{2}/\d{4})\s+(\d{2}/\d{2}/\d{4})', text)
    if m:
        data["pay_period_start"] = m.group(1)
        data["pay_period_end"]   = m.group(2)

    # Official pay date — labeled first, fallback to first date after pay period end
    m = re.search(r'Official Pay Date\s+(\d{2}/\d{2}/\d{4})', text, re.IGNORECASE)
    if m:
        data["official_pay_date"] = m.group(1)
    else:
        all_dates = re.findall(r'\b(\d{2}/\d{2}/\d{4})\b', text)
        pp_end = data.get("pay_period_end", "")
        future_dates = [d for d in all_dates if d >= pp_end]
        if future_dates:
            data["official_pay_date"] = future_dates[-1]
        elif all_dates:
            data["official_pay_date"] = all_dates[-1]

    # -----------------------------------------------------------------------
    # Salary / pay type
    # -----------------------------------------------------------------------
    m = re.search(r'\$([\d,]+\.\d{2})\s+(PA|PH|SES|AD|EX|SL)\b', text)
    if m:
        data["salary_rate"]    = m.group(1)
        data["pay_type_code"]  = m.group(2)
        data["pay_type_label"] = PAY_TYPES.get(m.group(2), m.group(2))

    # -----------------------------------------------------------------------
    # Gross pay
    # -----------------------------------------------------------------------
    # pdfplumber often garbles multi-column headers (interleaved chars).
    # Strategy: find "GROSS PAY" then grab the last 2 dollar amounts on that line
    # (PP gross + YTD gross — the 80.00 before them is hours).
    m = re.search(
        r'GROSS PAY.*?([\d,]+\.\d{2})\s+([\d,]+\.\d{2})\s+([\d,]+\.\d{2})',
        text
    )
    if m:
        data["gross_pay_hours"] = m.group(1)
        data["gross_pay_pp"]    = m.group(2)
        data["gross_pay_ytd"]   = m.group(3)
    else:
        m = re.search(r'GROSS PAY.*?([\d,]+\.\d{2})\s+([\d,]+\.\d{2})', text)
        if m:
            data["gross_pay_pp"]  = m.group(1)
            data["gross_pay_ytd"] = m.group(2)

    # -----------------------------------------------------------------------
    # Net pay & total deductions
    # -----------------------------------------------------------------------
    m = re.search(r'NET PAY \*+\s+([\d,.]+)\s+([\d,.]+)', text)
    if m:
        data["net_pay_pp"]  = m.group(1)
        data["net_pay_ytd"] = m.group(2)

    m = re.search(r'TOTAL DEDUCTIONS \*+\s+([\d,.]+)\s+([\d,.]+)', text)
    if m:
        data["total_deductions_pp"]  = m.group(1)
        data["total_deductions_ytd"] = m.group(2)

    # -----------------------------------------------------------------------
    # Deduction line items
    # -----------------------------------------------------------------------
    deductions = []
    for item, code, label, desc_pattern in DEDUCTION_PATTERNS:
        code_part = rf'\s+{code}' if code else ''
        pattern = rf'{item}{code_part}\s+{desc_pattern}.*?([\d,]+\.\d{{2}})\s+([\d,]+\.\d{{2}})'
        m = re.search(pattern, text)
        if m:
            deductions.append({
                "item":        item,
                "code":        code,
                "description": label,
                "pp_amount":   m.group(1),
                "ytd_amount":  m.group(2),
            })
    data["deductions"] = deductions

    # -----------------------------------------------------------------------
    # Leave / earnings line items
    # -----------------------------------------------------------------------
    leave_earnings = []
    for code, label in LEAVE_EARNINGS_PATTERNS:
        m = re.search(rf'{code}\s+{re.escape(label)}\s+([\d,.]+)\s+([\d,.]+)', text)
        if m:
            leave_earnings.append({
                "code":        code,
                "description": label,
                "hours":       m.group(1),
                "amount":      m.group(2),
            })
    data["leave_earnings"] = leave_earnings

    # -----------------------------------------------------------------------
    # Earnings with single amount (no hours column) — e.g., 51, 52, 44
    # -----------------------------------------------------------------------
    for code, label in SINGLE_AMOUNT_EARNINGS_CODES:
        # Already matched above with hours+amount format — skip
        if any(le["code"] == code for le in leave_earnings):
            continue
        pattern = rf'{code}\s+.*?{re.escape(label)}\s+.*?([\d,]+\.\d{{2}})\s+([\d,]+\.\d{{2}})'
        m = re.search(pattern, text)
        if m:
            leave_earnings.append({
                "code":        code,
                "description": label,
                "hours":       "",
                "amount":      m.group(1),
                "ytd_amount":  m.group(2),
            })
        else:
            # Try single column (PP amount only, no YTD)
            pattern2 = rf'{code}\s+.*?{re.escape(label)}\s+([\d,]+\.\d{{2}})'
            m2 = re.search(pattern2, text)
            if m2:
                leave_earnings.append({
                    "code":        code,
                    "description": label,
                    "hours":       "",
                    "amount":      m2.group(1),
                })

    # -----------------------------------------------------------------------
    # YTD leave status (accrued / used / balance)
    # -----------------------------------------------------------------------
    ytd_leave = []
    for leave_type in YTD_LEAVE_TYPES:
        m = re.search(
            rf'(?<!\w){re.escape(leave_type)}\s+([\d,.]+)\s+([\d,.]+)\s+([\d,.]+)',
            text
        )
        if m:
            ytd_leave.append({
                "type":    leave_type,
                "accrued": m.group(1),
                "used":    m.group(2),
                "balance": m.group(3),
            })

    # Positional fallback — bottom table linearizes as bare number sequence
    # NFC AD-334 order: ANN accrued/used/balance, SICK accrued/used/balance, COMP balance
    if not ytd_leave:
        m = re.search(
            r'\b(\d+\.\d{2})\s+(\d+\.\d{2})\s+(\d+\.\d{2})\s+(\d+\.\d{2})\s+(\d+\.\d{2})\s+(\d+\.\d{2})\s+(\d+\.\d{2})\b',
            text
        )
        if m:
            n = m.groups()
            ytd_leave = [
                {"type": "ANNUAL", "accrued": n[0], "used": n[1], "balance": n[2]},
                {"type": "SICK",   "accrued": n[3], "used": n[4], "balance": n[5]},
                {"type": "COMP",   "accrued": n[6], "used": "",   "balance": ""},
            ]

    data["ytd_leave_status"] = ytd_leave

    # -----------------------------------------------------------------------
    # Validation & diagnostics
    # -----------------------------------------------------------------------
    required = [
        "gross_pay_pp", "net_pay_pp", "total_deductions_pp",
        "salary_rate", "pay_period_start", "pay_period_end",
    ]
    data["_missing_fields"]     = [f for f in required if f not in data]
    data["_financials_balance"] = _validate_financials(data)
    data["_deductions_found"]   = len(deductions)
    data["_leave_items_found"]  = len(leave_earnings)

    if data["_missing_fields"]:
        logger.warning("Missing fields: %s", data["_missing_fields"])
    if data["_financials_balance"] is False:
        logger.warning("Financials do not balance: gross != net + deductions")

    return data


# ---------------------------------------------------------------------------
# Batch mode
# ---------------------------------------------------------------------------

def parse_folder(folder_path):
    """Parse all PDFs in a folder. Returns list of result dicts."""
    folder = Path(folder_path)
    pdfs = sorted(folder.glob("*.pdf"))
    if not pdfs:
        logger.warning("No PDF files found in %s", folder)
        return []
    results = []
    for pdf in pdfs:
        logger.info("Parsing %s", pdf.name)
        result = parse_les(pdf)
        results.append(result)
    return results


# ---------------------------------------------------------------------------
# Output formatters
# ---------------------------------------------------------------------------

def to_csv(results, output_path=None):
    """Flatten results to CSV. Requires pandas."""
    if not PANDAS_AVAILABLE:
        sys.exit("CSV output requires pandas: pip install pandas")
    if isinstance(results, dict):
        results = [results]

    rows = []
    for r in results:
        row = {k: v for k, v in r.items()
               if not isinstance(v, (list, dict)) and not k.startswith("_")}
        for d in r.get("deductions", []):
            col = d["description"].replace(" ", "_").replace("(", "").replace(")", "")
            row[f"ded_{col}_pp"]  = d["pp_amount"]
            row[f"ded_{col}_ytd"] = d["ytd_amount"]
        for l in r.get("leave_earnings", []):
            col = l["description"].replace(" ", "_")
            row[f"leave_{col}_hours"]  = l["hours"]
            row[f"leave_{col}_amount"] = l["amount"]
        rows.append(row)

    df = pd.DataFrame(rows)
    if output_path:
        df.to_csv(output_path, index=False)
        print(f"Saved CSV: {output_path}")
    else:
        print(df.to_csv(index=False))


# ---------------------------------------------------------------------------
# CLI entrypoint
# ---------------------------------------------------------------------------

def main():
    import argparse

    parser = argparse.ArgumentParser(
        description="Parse USDA NFC Earnings & Leave Statement PDFs (AD-334)."
    )
    parser.add_argument("path",          help="PDF file or folder path (use --batch for folders)")
    parser.add_argument("--output",      choices=["json", "csv"], default="json",
                        help="Output format (default: json)")
    parser.add_argument("--batch",       action="store_true",
                        help="Parse all PDFs in a folder")
    parser.add_argument("--out-file",    help="Write output to this file path")
    parser.add_argument("--verbose",     action="store_true",
                        help="Enable debug logging")
    args = parser.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    results = parse_folder(args.path) if args.batch else parse_les(args.path)

    if args.output == "csv":
        to_csv(results, output_path=args.out_file)
    else:
        output = json.dumps(results, indent=2)
        if args.out_file:
            Path(args.out_file).write_text(output)
            print(f"Saved JSON: {args.out_file}")
        else:
            print(output)


if __name__ == "__main__":
    main()