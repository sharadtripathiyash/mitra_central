"""Table catalog — short descriptions used by the fast LLM (Groq) to identify
which tables are relevant to a user question. Only names + purpose — NOT full
schemas (those are in table_schemas.py).
"""

TABLE_CATALOG = """
AVAILABLE QAD TABLES:

1. pt_mstr — Item Master: item definitions, descriptions, UM, planning params, lot/serial control, buyer, vendor, status.
2. in_mstr — Inventory Master: item-site inventory balances, qty on hand, allocated, on order, available, ROP, safety stock, MRP flags.
3. ld_det — Location Detail: granular inventory by site/location/lot/serial, qty on hand, status, grade, expiry dates.
4. lad_det — Location Allocation Detail: per-order allocations to locations/lots with picked quantities and timestamps.
5. loc_mstr — Location Master: storage locations with capacity, status, single-item flags.
6. po_mstr — Purchase Order Master: PO header with supplier, currency, status, dates, totals, site.
7. pod_det — Purchase Order Detail: PO line items with quantities ordered/received/returned, costs, due dates.
8. so_mstr — Sales Order Master: SO header with customer, ship-to, billing, currency, status, dates.
9. sod_det — Sales Order Detail: SO line items with quantities ordered/shipped/invoiced, prices, due dates.
10. wo_mstr — Work Order Master: production WO header with item, qty ordered/completed, schedule dates, status.
11. wod_det — Work Order Detail: WO component requirements with required/issued/allocated quantities.
12. tr_hist — Transaction History: complete audit trail of inventory movements with quantities, lots, costs, order refs.
13. bom_mstr — BOM Master: header info for BOMs/formulas with batch sizes and formulation attributes.
14. ps_mstr — Product Structure: parent-component relationships (BOM lines), qty per assembly, effective dates.
15. sct_det — Cost Detail: item-site cost sets with material/labor/burden/overhead/subcontract cost elements.
16. si_mstr — Site Master: site definitions with defaults, cost set assignments, status.
17. mrp_det — MRP Detail: material requirements planning output with action messages, planned orders, supply/demand.

RESPOND WITH ONLY A JSON ARRAY of relevant table names. Example: ["pt_mstr", "in_mstr"]
"""
