"""Manufacturing-specific business rules for Mitra SQL agent."""

RULES = {
    "WIP_BY_WORK_ORDER": {
        "keywords": ["wip", "work in progress", "open work orders", "production orders"],
        "patterns": [r"w\.?i\.?p", r"work\s+in\s+progress", r"open\s+work\s+orders?"],
        "logic": "Work orders that are not closed, showing ordered vs completed quantities to indicate WIP.",
        "sql": (
            "SELECT TOP {limit} wo.wo_nbr AS \"WO Number\", wo.wo_part AS \"Item\", "
            "pt.pt_desc1 AS \"Description\", wo.wo_site AS \"Site\", "
            "wo.wo_qty_ord AS \"Qty Ordered\", wo.wo_qty_comp AS \"Qty Completed\", "
            "(wo.wo_qty_ord - wo.wo_qty_comp) AS \"WIP Qty\", "
            "wo.wo_due_date AS \"Due Date\", wo.wo_status AS \"Status\" "
            "FROM PUB.wo_mstr AS wo "
            "JOIN PUB.pt_mstr AS pt ON wo.wo_part = pt.pt_part AND wo.wo_domain = pt.pt_domain "
            "WHERE wo.wo_domain = '{domain}' AND wo.wo_status NOT IN ('C','X') "
            "ORDER BY wo.wo_due_date ASC"
        ),
        "followup": "Would you like to check component availability for these work orders?",
    },
    "COMPONENT_SHORTAGE": {
        "keywords": ["component shortage", "material shortage", "missing components"],
        "patterns": [r"component\s+shortage", r"material\s+shortage", r"missing\s+components?"],
        "logic": "Work order components where required quantity exceeds issued quantity, indicating shortages that may block production.",
        "sql": (
            "SELECT TOP {limit} wod.wod_nbr AS \"WO Number\", wod.wod_lot AS \"Lot\", "
            "wod.wod_part AS \"Component\", pt.pt_desc1 AS \"Description\", "
            "wod.wod_qty_req AS \"Qty Required\", wod.wod_qty_iss AS \"Qty Issued\", "
            "(wod.wod_qty_req - wod.wod_qty_iss) AS \"Shortage\" "
            "FROM PUB.wod_det AS wod "
            "JOIN PUB.pt_mstr AS pt ON wod.wod_part = pt.pt_part AND wod.wod_domain = pt.pt_domain "
            "WHERE wod.wod_domain = '{domain}' AND wod.wod_qty_req > wod.wod_qty_iss "
            "ORDER BY (wod.wod_qty_req - wod.wod_qty_iss) DESC"
        ),
        "followup": "Would you like to check if purchase orders exist for these short components?",
    },
    "PRODUCTION_COMPLETED": {
        "keywords": ["production completed", "completed work orders", "finished production"],
        "patterns": [r"production\s+completed", r"completed\s+work\s+orders?", r"finished\s+production"],
        "logic": "Work orders with status Closed or where completed quantity equals ordered quantity.",
        "sql": None,
        "followup": "Would you like to see yield analysis for completed production?",
    },
    "BOM_EXPLOSION": {
        "keywords": ["bom explosion", "bill of material", "bom components", "recipe", "ingredients"],
        "patterns": [r"bom\s+explosion", r"bill\s+of\s+material", r"bom\s+components?"],
        "logic": "Explode the BOM/product structure for a given parent item showing all components, quantities per assembly, and operations.",
        "sql": None,
        "followup": "Would you like to check inventory for these components?",
    },
}
