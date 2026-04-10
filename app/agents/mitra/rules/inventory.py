"""Inventory-specific business rules for Mitra SQL agent."""

RULES = {
    "LOW_INVENTORY": {
        "keywords": ["low inventory", "low stock", "stock shortage", "inventory shortage", "out of stock"],
        "patterns": [r"low\s+(inventory|stock)", r"stock\s+shortage", r"inventory\s+shortage"],
        "logic": "Items where current quantity on hand is below safety stock threshold. Safety stock (pt_sfty_stk) is the minimum level a company wants to maintain.",
        "sql": (
            "SELECT TOP {limit} pt.pt_part AS \"Part Number\", pt.pt_desc1 AS \"Description\", "
            "inv.in_site AS \"Site\", inv.in_qty_oh AS \"Qty On Hand\", "
            "pt.pt_sfty_stk AS \"Safety Stock\" "
            "FROM PUB.in_mstr AS inv "
            "JOIN PUB.pt_mstr AS pt ON inv.in_part = pt.pt_part AND inv.in_domain = pt.pt_domain "
            "WHERE inv.in_domain = '{domain}' AND inv.in_qty_oh < pt.pt_sfty_stk "
            "ORDER BY (pt.pt_sfty_stk - inv.in_qty_oh) DESC"
        ),
        "followup": "Would you like to see items below reorder point or check which items need to be ordered?",
    },
    "WHAT_TO_ORDER": {
        "keywords": ["what to order", "what should i order", "items to order", "need to order"],
        "patterns": [r"what\s+(to|should\s+i)\s+order", r"items\s+to\s+order", r"need\s+to\s+order"],
        "logic": "Items where quantity on hand has fallen below the reorder point (ROP). ROP is the level at which a new order should be placed.",
        "sql": (
            "SELECT TOP {limit} pt.pt_part AS \"Part Number\", pt.pt_desc1 AS \"Description\", "
            "inv.in_site AS \"Site\", inv.in_qty_oh AS \"Qty On Hand\", "
            "inv.in_rop AS \"Reorder Point\", inv.in_qty_ord AS \"Qty On Order\" "
            "FROM PUB.in_mstr AS inv "
            "JOIN PUB.pt_mstr AS pt ON inv.in_part = pt.pt_part AND inv.in_domain = pt.pt_domain "
            "WHERE inv.in_domain = '{domain}' AND inv.in_qty_oh < inv.in_rop "
            "ORDER BY (inv.in_rop - inv.in_qty_oh) DESC"
        ),
        "followup": "Would you like me to show supplier details for these items?",
    },
    "ITEMS_BELOW_REORDER": {
        "keywords": ["below reorder point", "items below reorder", "under reorder point"],
        "patterns": [r"below\s+reorder\s+point", r"under\s+reorder\s+point"],
        "logic": "Items where quantity on hand is below the reorder point, sorted by urgency (biggest gap first).",
        "sql": (
            "SELECT TOP {limit} im.in_part AS \"Part Number\", pt.pt_desc1 AS \"Description\", "
            "im.in_site AS \"Site\", im.in_qty_oh AS \"Qty On Hand\", "
            "pt.pt_rop AS \"Reorder Point\", pt.pt_sfty_stk AS \"Safety Stock\", "
            "pt.pt_buyer AS \"Buyer\", pt.pt_vend AS \"Vendor\" "
            "FROM PUB.in_mstr AS im "
            "JOIN PUB.pt_mstr AS pt ON im.in_part = pt.pt_part AND im.in_domain = pt.pt_domain "
            "WHERE im.in_domain = '{domain}' AND im.in_qty_oh < pt.pt_rop AND pt.pt_rop > 0 "
            "ORDER BY (pt.pt_rop - im.in_qty_oh) DESC"
        ),
        "followup": "Would you like to check if purchase orders exist for these items?",
    },
    "SLOW_MOVING": {
        "keywords": ["slow moving", "dead stock", "excess inventory", "overstocked"],
        "patterns": [r"slow\s+moving", r"dead\s+stock", r"excess\s+inventory", r"overstocked"],
        "logic": "Slow-moving items are those where quantity on hand significantly exceeds both average sales and average issuance, indicating the stock is not turning over.",
        "sql": (
            "SELECT TOP {limit} pt.pt_part AS \"Part Number\", pt.pt_desc1 AS \"Description\", "
            "inv.in_site AS \"Site\", inv.in_qty_oh AS \"Qty On Hand\", "
            "inv.in_avg_sls AS \"Avg Monthly Sales\", inv.in_avg_iss AS \"Avg Monthly Issues\" "
            "FROM PUB.in_mstr AS inv "
            "JOIN PUB.pt_mstr AS pt ON inv.in_part = pt.pt_part AND inv.in_domain = pt.pt_domain "
            "WHERE inv.in_domain = '{domain}' AND inv.in_qty_oh > 0 "
            "AND inv.in_qty_oh > inv.in_avg_sls AND inv.in_qty_oh > inv.in_avg_iss"
        ),
        "followup": "Would you like to see where these items are stored (locations)?",
    },
    "EXPIRING_THIS_MONTH": {
        "keywords": ["expiring this month", "expire this month", "this month expiry"],
        "patterns": [r"expir(e|ing)\s+this\s+month", r"this\s+month.*expir"],
        "logic": "Items with lot expiration dates falling within the current calendar month.",
        "sql": None,  # LLM generates — date functions vary by ODBC driver
        "followup": "Would you like to check for open orders that could consume this inventory?",
    },
    "EXPIRING_NEXT_MONTH": {
        "keywords": ["expiring next month", "expire next month", "next month expiry"],
        "patterns": [r"expir(e|ing)\s+next\s+month", r"next\s+month.*expir"],
        "logic": "Items with lot expiration dates falling within next calendar month.",
        "sql": None,
        "followup": "Would you like usage recommendations for these expiring items?",
    },
}
