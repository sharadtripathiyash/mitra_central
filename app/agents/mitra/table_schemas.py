"""Full CREATE TABLE DDL for all 17 QAD tables.

Used by the SQL generation LLM to produce accurate queries. Only the schemas
for tables identified by the table-identifier step are passed to the LLM to
keep context focused.
"""

SCHEMAS: dict[str, str] = {

"in_mstr": """CREATE TABLE in_mstr (
  in_part VARCHAR(255), in_site VARCHAR(255),
  in_qty_oh DECIMAL(15,2), in_qty_req DECIMAL(15,2), in_qty_all DECIMAL(15,2),
  in_qty_ord DECIMAL(15,2), in_qty_chg DECIMAL(15,2), in_qty_avail DECIMAL(15,2),
  in_sls_chg DECIMAL(15,2), in_iss_chg DECIMAL(15,2), in_iss_date DATE, in_rec_date DATE,
  in_cnt_date DATE, in_avg_iss DECIMAL(15,2), in_avg_sls DECIMAL(15,2), in_avg_date DATE,
  in_rop DECIMAL(15,2), in_sfty_stk DECIMAL(15,2), in_qty_nonet DECIMAL(15,2),
  in_mrp BOOLEAN, in_gl_set VARCHAR(255), in_cur_set VARCHAR(255), in_level INT,
  in_loc VARCHAR(255), in_loc_type VARCHAR(255), in_domain VARCHAR(255),
  PRIMARY KEY (in_domain, in_part, in_site)
);""",

"pt_mstr": """CREATE TABLE pt_mstr (
  pt_part VARCHAR(18) NOT NULL, pt_desc1 VARCHAR(24), pt_desc2 VARCHAR(24),
  pt_um VARCHAR(2), pt_draw VARCHAR(18), pt_prod_line VARCHAR(4), pt_group VARCHAR(8),
  pt_part_type VARCHAR(8), pt_status VARCHAR(8), pt_abc VARCHAR(1),
  pt_loc VARCHAR(8), pt_mrp BOOLEAN, pt_ord_pol VARCHAR(3), pt_ord_qty DECIMAL(15,2),
  pt_sfty_stk DECIMAL(15,2), pt_sfty_time DECIMAL(15,2), pt_rop DECIMAL(15,2),
  pt_buyer VARCHAR(8), pt_vend VARCHAR(8), pt_pm_code VARCHAR(1),
  pt_mfg_lead DECIMAL(15,2), pt_pur_lead INT, pt_lot_ser VARCHAR(1),
  pt_site VARCHAR(8), pt_routing VARCHAR(18), pt_bom_code VARCHAR(18),
  pt_domain VARCHAR(8),
  PRIMARY KEY (pt_domain, pt_part)
);""",

"ld_det": """CREATE TABLE ld_det (
  ld_loc VARCHAR(8) NOT NULL, ld_part VARCHAR(18) NOT NULL, ld_date DATE,
  ld_qty_oh DECIMAL(15,2), ld_lot VARCHAR(18) NOT NULL, ld_ref VARCHAR(8) NOT NULL,
  ld_cnt_date DATE, ld_expire DATE, ld_site VARCHAR(8) NOT NULL,
  ld_status VARCHAR(8), ld_qty_all DECIMAL(15,2), ld_grade VARCHAR(2),
  ld_rev VARCHAR(4), ld_domain VARCHAR(8) NOT NULL,
  PRIMARY KEY (ld_domain, ld_site, ld_loc, ld_part, ld_lot, ld_ref)
);""",

"lad_det": """CREATE TABLE lad_det (
  lad_dataset VARCHAR(12) NOT NULL, lad_nbr VARCHAR(18) NOT NULL,
  lad_line VARCHAR(8) NOT NULL, lad_site VARCHAR(8) NOT NULL,
  lad_loc VARCHAR(8) NOT NULL, lad_part VARCHAR(18) NOT NULL,
  lad_lot VARCHAR(18) NOT NULL, lad_qty_all DECIMAL(15,2),
  lad_qty_pick DECIMAL(15,2), lad_ref VARCHAR(8) NOT NULL,
  lad_act_pick DECIMAL(15,2), lad_timestamp DATETIME,
  lad_domain VARCHAR(8) NOT NULL,
  PRIMARY KEY (lad_domain, lad_dataset, lad_nbr, lad_line, lad_part, lad_site, lad_loc, lad_lot, lad_ref)
);""",

"loc_mstr": """CREATE TABLE loc_mstr (
  loc_loc VARCHAR(255), loc_site VARCHAR(255), loc_status VARCHAR(255),
  loc_type VARCHAR(255), loc_cap DECIMAL(15,2), loc_cap_um VARCHAR(255),
  loc_single BOOLEAN, loc_perm BOOLEAN, loc_desc VARCHAR(255),
  loc_domain VARCHAR(255),
  PRIMARY KEY (loc_domain, loc_site, loc_loc)
);""",

"po_mstr": """CREATE TABLE po_mstr (
  po_nbr VARCHAR(8) NOT NULL, po_vend VARCHAR(8), po_ship VARCHAR(8),
  po_ord_date DATE, po_cr_terms VARCHAR(8), po_buyer VARCHAR(8),
  po_bill VARCHAR(8), po_stat VARCHAR(2), po_curr VARCHAR(3),
  po_site VARCHAR(8), po_due_date DATE, po_type VARCHAR(1),
  po_order_total DECIMAL(15,2), po_base_order_total DECIMAL(15,2),
  po_domain VARCHAR(8) NOT NULL,
  PRIMARY KEY (po_domain, po_nbr)
);""",

"pod_det": """CREATE TABLE pod_det (
  pod_nbr VARCHAR(8) NOT NULL, pod_due_date DATE, pod_line INT NOT NULL,
  pod_part VARCHAR(18), pod_qty_ord DECIMAL(15,2), pod_qty_rcvd DECIMAL(15,2),
  pod_pur_cost DECIMAL(15,2), pod_std_cost DECIMAL(15,2), pod_desc VARCHAR(40),
  pod_um VARCHAR(2), pod_status VARCHAR(1), pod_site VARCHAR(8),
  pod_loc VARCHAR(8), pod_qty_rtnd DECIMAL(15,2), pod_need DATE,
  pod_domain VARCHAR(8) NOT NULL,
  PRIMARY KEY (pod_domain, pod_nbr, pod_line)
);""",

"so_mstr": """CREATE TABLE so_mstr (
  so_nbr VARCHAR(8) NOT NULL, so_cust VARCHAR(8), so_ship VARCHAR(8),
  so_bill VARCHAR(8), so_ord_date DATE, so_req_date DATE, so_due_date DATE,
  so_cr_terms VARCHAR(8), so_slspsn VARCHAR(8), so_curr VARCHAR(3),
  so_site VARCHAR(8), so_stat VARCHAR(2), so_type VARCHAR(8),
  so_po VARCHAR(22), so_domain VARCHAR(8) NOT NULL,
  PRIMARY KEY (so_domain, so_nbr)
);""",

"sod_det": """CREATE TABLE sod_det (
  sod_nbr VARCHAR(8) NOT NULL, sod_line INT NOT NULL, sod_part VARCHAR(18),
  sod_qty_ord DECIMAL(15,2), sod_qty_all DECIMAL(15,2), sod_qty_pick DECIMAL(15,2),
  sod_qty_ship DECIMAL(15,2), sod_qty_inv DECIMAL(15,2), sod_loc VARCHAR(8),
  sod_price DECIMAL(15,2), sod_desc VARCHAR(24), sod_um VARCHAR(2),
  sod_due_date DATE, sod_site VARCHAR(8), sod_status VARCHAR(8),
  sod_domain VARCHAR(8) NOT NULL,
  PRIMARY KEY (sod_domain, sod_nbr, sod_line)
);""",

"wo_mstr": """CREATE TABLE wo_mstr (
  wo_nbr VARCHAR(18) NOT NULL, wo_lot VARCHAR(18) NOT NULL,
  wo_part VARCHAR(18), wo_qty_ord DECIMAL(15,2), wo_qty_comp DECIMAL(15,2),
  wo_due_date DATE, wo_rel_date DATE, wo_ord_date DATE,
  wo_status VARCHAR(1), wo_site VARCHAR(8), wo_routing VARCHAR(18),
  wo_rev VARCHAR(4), wo_type VARCHAR(1), wo_domain VARCHAR(8) NOT NULL,
  PRIMARY KEY (wo_domain, wo_nbr, wo_lot)
);""",

"wod_det": """CREATE TABLE wod_det (
  wod_nbr VARCHAR(18), wod_lot VARCHAR(8) NOT NULL, wod_op INT NOT NULL,
  wod_part VARCHAR(18) NOT NULL, wod_site VARCHAR(8),
  wod_qty_req DECIMAL(15,2), wod_qty_all DECIMAL(15,2),
  wod_qty_pick DECIMAL(15,2), wod_qty_iss DECIMAL(15,2),
  wod_loc VARCHAR(8), wod_status VARCHAR(8),
  wod_domain VARCHAR(8) NOT NULL,
  PRIMARY KEY (wod_domain, wod_lot, wod_part, wod_op)
);""",

"tr_hist": """CREATE TABLE tr_hist (
  tr_part VARCHAR(18) NOT NULL, tr_date DATE, tr_type VARCHAR(8),
  tr_loc VARCHAR(8), tr_qty_chg DECIMAL(15,2), tr_um VARCHAR(2),
  tr_nbr VARCHAR(18), tr_addr VARCHAR(8), tr_price DECIMAL(15,2),
  tr_trnbr BIGINT NOT NULL, tr_lot VARCHAR(8), tr_serial VARCHAR(18),
  tr_effdate DATE, tr_site VARCHAR(8), tr_status VARCHAR(8),
  tr_ref VARCHAR(8), tr_line INT, tr_domain VARCHAR(8) NOT NULL,
  PRIMARY KEY (tr_domain, tr_trnbr)
);""",

"bom_mstr": """CREATE TABLE bom_mstr (
  bom_parent VARCHAR(18) NOT NULL, bom_desc VARCHAR(24),
  bom_batch DECIMAL(15,2), bom_formula BOOLEAN,
  bom_site VARCHAR(8), bom_loc VARCHAR(8), bom_mthd VARCHAR(1),
  bom_domain VARCHAR(8) NOT NULL,
  PRIMARY KEY (bom_domain, bom_parent)
);""",

"ps_mstr": """CREATE TABLE ps_mstr (
  ps_par VARCHAR(18) NOT NULL, ps_comp VARCHAR(18) NOT NULL,
  ps_ref VARCHAR(12) NOT NULL, ps_start DATE, ps_end DATE,
  ps_qty_per DECIMAL(15,2), ps_scrp_pct DECIMAL(15,2),
  ps_op INT, ps_item_no INT, ps_domain VARCHAR(8) NOT NULL,
  PRIMARY KEY (ps_domain, ps_par, ps_comp, ps_ref, ps_start)
);""",

"sct_det": """CREATE TABLE sct_det (
  sct_sim VARCHAR(8) NOT NULL, sct_part VARCHAR(18) NOT NULL,
  sct_cst_tot DECIMAL(15,2), sct_mtl_tl DECIMAL(15,2),
  sct_lbr_tl DECIMAL(15,2), sct_bdn_tl DECIMAL(15,2),
  sct_ovh_tl DECIMAL(15,2), sct_sub_tl DECIMAL(15,2),
  sct_mtl_ll DECIMAL(15,2), sct_lbr_ll DECIMAL(15,2),
  sct_cst_date DATE, sct_site VARCHAR(8) NOT NULL,
  sct_domain VARCHAR(8) NOT NULL,
  PRIMARY KEY (sct_domain, sct_sim, sct_part, sct_site)
);""",

"si_mstr": """CREATE TABLE si_mstr (
  si_site VARCHAR(255), si_desc VARCHAR(255), si_entity VARCHAR(255),
  si_status VARCHAR(255), si_auto_loc BOOLEAN,
  si_gl_set VARCHAR(255), si_cur_set VARCHAR(255),
  si_domain VARCHAR(255),
  PRIMARY KEY (si_domain, si_site)
);""",

"mrp_det": """CREATE TABLE mrp_det (
  mrp_dataset VARCHAR(12) NOT NULL, mrp_domain VARCHAR(8) NOT NULL,
  mrp_due_date DATE, mrp_line VARCHAR(8) NOT NULL,
  mrp_nbr VARCHAR(18) NOT NULL, mrp_part VARCHAR(18) NOT NULL,
  mrp_qty DECIMAL(15,2), mrp_rel_date DATE,
  mrp_site VARCHAR(8), mrp_type VARCHAR(8),
  PRIMARY KEY (mrp_domain, mrp_dataset, mrp_part, mrp_nbr, mrp_line)
);""",

}


def get_schemas_for_tables(table_names: list[str]) -> str:
    """Return formatted DDL for the requested tables."""
    parts = []
    for name in table_names:
        schema = SCHEMAS.get(name.lower().strip())
        if schema:
            parts.append(f"=== {name.upper()} ===\n{schema}")
    return "\n\n".join(parts) if parts else "No matching schemas found."
