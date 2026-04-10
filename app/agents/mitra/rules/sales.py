"""Sales-specific business rules for Mitra SQL agent."""

RULES = {
    "OPEN_SALES_ORDERS": {
        "keywords": ["open sales orders", "pending sales orders", "open so"],
        "patterns": [r"open\s+sales?\s+orders?", r"pending\s+sales?\s+orders?"],
        "logic": "Sales orders that are not yet fully shipped or closed (status is Open).",
        "sql": (
            "SELECT TOP {limit} so.so_nbr AS \"SO Number\", so.so_cust AS \"Customer\", "
            "so.so_ord_date AS \"Order Date\", so.so_due_date AS \"Due Date\", "
            "so.so_stat AS \"Status\", so.so_site AS \"Site\" "
            "FROM PUB.so_mstr AS so "
            "WHERE so.so_domain = '{domain}' AND so.so_stat != 'C' "
            "ORDER BY so.so_due_date ASC"
        ),
        "followup": "Would you like to see line-level details for any of these orders?",
    },
    "TOP_CUSTOMERS_REVENUE": {
        "keywords": ["top customers", "best customers", "customers by revenue"],
        "patterns": [r"top\s+\d*\s*customers", r"customers\s+by\s+revenue"],
        "logic": "Customers ranked by total order value from sales order details.",
        "sql": None,  # LLM generates — aggregation varies by question
        "followup": "Would you like to see the sales trend for the top customer?",
    },
    "SALES_BACKLOG": {
        "keywords": ["sales backlog", "backlog", "unshipped orders"],
        "patterns": [r"sales?\s+backlog", r"unshipped\s+orders?"],
        "logic": "Backlog = ordered quantity minus shipped quantity on open sales order lines. Shows items where there is still outstanding demand.",
        "sql": (
            "SELECT TOP {limit} sod.sod_nbr AS \"SO Number\", sod.sod_line AS \"Line\", "
            "sod.sod_part AS \"Item\", pt.pt_desc1 AS \"Description\", "
            "sod.sod_qty_ord AS \"Qty Ordered\", sod.sod_qty_ship AS \"Qty Shipped\", "
            "(sod.sod_qty_ord - sod.sod_qty_ship) AS \"Backlog\", "
            "sod.sod_due_date AS \"Due Date\" "
            "FROM PUB.sod_det AS sod "
            "JOIN PUB.pt_mstr AS pt ON sod.sod_part = pt.pt_part AND sod.sod_domain = pt.pt_domain "
            "WHERE sod.sod_domain = '{domain}' "
            "AND sod.sod_qty_ord > sod.sod_qty_ship "
            "ORDER BY sod.sod_due_date ASC"
        ),
        "followup": "Would you like to check inventory availability for these backlog items?",
    },
}
