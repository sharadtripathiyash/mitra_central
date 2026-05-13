"""Universal QAD-standard module acronyms — same meaning across ALL customers.

ONLY add things here that are TRULY universal QAD/ERP terminology. Anything
customer-specific (RTDC, MRN, SPA, DOA, MDM, etc.) must NOT live here —
those are derived per-customer by Pass A from the customer's own code.

Rule of thumb: if asking a different QAD customer would give a different
meaning for an acronym, do NOT put it in this file.
"""

QAD_STANDARD_GLOSSARY: dict[str, str] = {
    # ── Financials (QAD core, customer-invariant) ───────────────────────────
    "AP":   "Accounts Payable",
    "AR":   "Accounts Receivable",
    "GL":   "General Ledger",
    "GLT":  "GL Transaction",
    "FA":   "Fixed Assets",
    "FX":   "Foreign Exchange",

    # ── Distribution / Order Management ─────────────────────────────────────
    "SO":   "Sales Order",
    "PO":   "Purchase Order",
    "EDI":  "Electronic Data Interchange (standard transaction codes "
            "include 850 PO, 855 PO Ack, 856 ASN, 810 Invoice, 844 Debit Auth)",

    # ── Manufacturing ───────────────────────────────────────────────────────
    "BOM":  "Bill of Materials",
    "WO":   "Work Order",
    "MRP":  "Material Requirements Planning",

    # ── Inventory ───────────────────────────────────────────────────────────
    "ICS":  "Inventory Control System",
    "POD":  "Purchase Order Document",

    # ── India GST regulatory (these ARE universal to all QAD India customers)
    "EINV": "E-Invoice — GST regulatory submission to government portal",
    "EWB":  "E-Way Bill — GST regulatory transport document",
    "IRN":  "Invoice Reference Number (GST E-Invoice subsystem)",
    "GST":  "Goods and Services Tax",
    "TCS":  "Tax Collected at Source",
    "TDS":  "Tax Deducted at Source",
    "CGST": "Central Goods and Services Tax",
    "SGST": "State Goods and Services Tax",
    "IGST": "Integrated Goods and Services Tax",
    "UTGST":"Union Territory Goods and Services Tax",
}


def format_for_prompt() -> str:
    """Render the glossary as a numbered text block for inclusion in prompts."""
    lines = ["STANDARD QAD GLOSSARY (universal — applies to every QAD customer):"]
    for tag, desc in sorted(QAD_STANDARD_GLOSSARY.items()):
        lines.append(f"  {tag:<6} = {desc}")
    return "\n".join(lines)


def lookup(prefix: str) -> str | None:
    """Returns the standard meaning if prefix is a known QAD universal acronym."""
    return QAD_STANDARD_GLOSSARY.get(prefix.upper())
