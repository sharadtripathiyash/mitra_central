"""Purchase-specific business rules for Mitra SQL agent."""

RULES = {
    "OPEN_PURCHASE_ORDERS": {
        "keywords": ["open purchase orders", "pending po", "open po", "pending purchase"],
        "patterns": [r"open\s+purchase\s+orders?", r"open\s+po", r"pending\s+po"],
        "logic": "Purchase orders that have not been fully received or closed.",
        "sql": (
            "SELECT TOP {limit} po.po_nbr AS \"PO Number\", po.po_vend AS \"Supplier\", "
            "po.po_ord_date AS \"Order Date\", po.po_due_date AS \"Due Date\", "
            "po.po_stat AS \"Status\", po.po_site AS \"Site\" "
            "FROM PUB.po_mstr AS po "
            "WHERE po.po_domain = '{domain}' AND po.po_stat != 'C' "
            "ORDER BY po.po_due_date ASC"
        ),
        "followup": "Would you like to see line details or check for late deliveries?",
    },
    "LATE_DELIVERIES": {
        "keywords": ["late deliveries", "overdue po", "delayed purchase", "late purchase"],
        "patterns": [r"late\s+deliver", r"overdue\s+po", r"delayed\s+purchase"],
        "logic": "PO lines where the due date has passed but qty received is less than qty ordered.",
        "sql": None,
        "followup": "Would you like to see supplier performance for these late orders?",
    },
    "PURCHASE_RECEIPTS": {
        "keywords": ["purchase receipts", "received items", "po receipts"],
        "patterns": [r"purchase\s+receipt", r"po\s+receipt", r"received\s+items"],
        "logic": "Purchase order lines that have been received, showing received quantities.",
        "sql": None,
        "followup": "Would you like to analyze supplier performance based on these receipts?",
    },
    "TOP_SUPPLIERS_SPEND": {
        "keywords": ["top suppliers", "supplier spend", "vendor spend"],
        "patterns": [r"top\s+\d*\s*suppliers?", r"supplier\s+spend", r"vendor\s+spend"],
        "logic": "Suppliers ranked by total purchase value across their PO lines.",
        "sql": None,
        "followup": "Would you like to compare delivery performance across these suppliers?",
    },
}
