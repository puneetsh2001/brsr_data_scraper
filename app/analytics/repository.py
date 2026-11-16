"""SQL for the analytics API. Every statement is parameterised and only
reads the `brsr` schema loaded by the Phase 14 staging loader."""

UNIVERSE = "c.in_processing_universe = 'True'"


def _rows(cur):
    """Rows with SQL NULL text as '' (the loader stores blank non-key text
    as NULL); numbers and dates are left as they are."""
    return [{k: ("" if v is None else v) for k, v in r.items()}
            for r in cur.fetchall()]


def _row(cur):
    rows = _rows(cur)
    return rows[0] if rows else None

COMPANY_COLUMNS = """c.company_id, c.legal_name, c.common_name, c.cin,
    c.cin_status, c.isin, c.nse_symbol, c.nse_series, c.bse_scrip_code,
    c.bse_match_status, c.industry, c.company_status, c.website,
    c.registered_office, c.is_nse_brsr_filer, c.latest_verified_fy"""

VALUE_COLUMNS = """rv.requirement_id, rv.pillar, rv.metric_key, rv.breakdown,
    rv.master_isin, rv.fy_slot, rv.period_role, rv.metric_name,
    rv.current_concept, rv.crosswalk_status, rv.taxonomy_version,
    rv.data_type, rv.value_status, rv.dimensions, rv.period_start,
    rv.period_end, rv.instant_date, rv.context_reference, rv.source_file,
    rv.source_url, rv.original_value, rv.original_unit_id, rv.original_unit,
    rv.expected_unit_family, rv.normalised_value, rv.normalised_unit,
    rv.conversion_factor, rv.conversion_formula, rv.conversion_status,
    rv.conversion_note, rv.needs_review, rv.unit_decision, rv.filing_id,
    ca.multiplier_basis, ca.pending_review, ca.deferred_issue_ids,
    ca.scale_decision_id, ca.scale_decision_outcome,
    ca.unit_assumption_outcome, f.submission_date, f.reporting_period_start,
    f.reporting_period_end, f.xbrl_url, f.pdf_url, f.filing_version_status,
    f.version_number"""

VALUE_FROM = """brsr.requirement_value rv
    JOIN brsr.filing f ON f.filing_id = rv.filing_id
    LEFT JOIN brsr.conversion_audit ca
      ON ca.source_file = rv.source_file
     AND ca.context_reference = rv.context_reference
     AND ca.requirement_id = rv.requirement_id
     AND ca.breakdown = rv.breakdown
     AND ca.period_role = rv.period_role
     AND ca.metric_name = rv.metric_name"""


def search_companies(cur, q, limit, offset):
    like = f"%{q}%" if q else "%"
    cur.execute(f"""
        SELECT {COMPANY_COLUMNS}, count(*) OVER () AS total
          FROM brsr.company c
         WHERE {UNIVERSE}
           AND (c.legal_name ILIKE %(q)s OR c.common_name ILIKE %(q)s
                OR c.nse_symbol ILIKE %(q)s OR c.isin ILIKE %(q)s
                OR c.bse_scrip_code ILIKE %(q)s OR c.cin ILIKE %(q)s
                OR c.company_id ILIKE %(q)s)
         ORDER BY c.legal_name, c.company_id
         LIMIT %(limit)s OFFSET %(offset)s""",
                {"q": like, "limit": limit, "offset": offset})
    return _rows(cur)


def get_company(cur, key):
    """By company id, ISIN or NSE symbol, within the processing universe."""
    cur.execute(f"""
        SELECT {COMPANY_COLUMNS} FROM brsr.company c
         WHERE {UNIVERSE}
           AND (c.company_id = %(k)s OR c.isin = %(k)s
                OR upper(c.nse_symbol) = upper(%(k)s))
         ORDER BY (c.company_id = %(k)s) DESC LIMIT 1""", {"k": key})
    return _row(cur)


def identifiers(cur, company_id):
    cur.execute("""
        SELECT identifier_type, value, valid_from, valid_to, is_current,
               source
          FROM brsr.company_identifier WHERE company_id = %s
         ORDER BY identifier_type, valid_from NULLS FIRST, value""",
                (company_id,))
    return _rows(cur)


def filings(cur, company_id):
    cur.execute("""
        SELECT filing_id, fy_slot, reporting_period_start,
               reporting_period_end, submission_date, revision_date,
               xbrl_url, pdf_url, taxonomy_version, filing_version_status,
               version_number, versions_in_slot, is_latest_valid,
               processing_status, download_status, error_details
          FROM brsr.filing
         WHERE company_id = %s AND in_processing_scope = 'True'
         ORDER BY fy_slot, version_number::int""", (company_id,))
    return _rows(cur)


def requirement_map(cur):
    cur.execute("""
        SELECT requirement_id, pillar, requirement, metric_key,
               current_concept, dimension_filter, breakdown_axes,
               expected_unit, applicability_concept, source, notes
          FROM brsr.requirement_concept_map
         ORDER BY requirement_id, current_concept""")
    return _rows(cur)


def values(cur, isins, fy=None, requirement_ids=None, breakdown=None,
           role="CY"):
    where = ["rv.master_isin = ANY(%(isins)s)", "rv.period_role = %(role)s"]
    params = {"isins": list(isins), "role": role}
    if fy is not None:
        where.append("rv.fy_slot = %(fy)s")
        params["fy"] = fy
    if requirement_ids is not None:
        where.append("rv.requirement_id = ANY(%(reqs)s)")
        params["reqs"] = list(requirement_ids)
    if breakdown is not None:
        where.append("rv.breakdown = %(breakdown)s")
        params["breakdown"] = breakdown
    cur.execute(f"""
        SELECT {VALUE_COLUMNS} FROM {VALUE_FROM}
         WHERE {' AND '.join(where)}
         ORDER BY rv.master_isin, rv.fy_slot, rv.requirement_id,
                  rv.breakdown, rv.metric_name""", params)
    return _rows(cur)


def applicability(cur, filing_ids, concepts):
    """{(filing_id, concept): {values}} for the applicability concepts."""
    if not filing_ids or not concepts:
        return {}
    cur.execute("""
        SELECT filing_id, concept, lower(value) AS value
          FROM brsr.xbrl_fact
         WHERE filing_id = ANY(%s) AND concept = ANY(%s)
           AND value_status = 'REPORTED'""",
                (list(filing_ids), list(concepts)))
    out = {}
    for r in _rows(cur):
        out.setdefault((r["filing_id"], r["concept"]), set()).add(r["value"])
    return out


def review_items(cur, isin, fy):
    cur.execute("""
        SELECT recommendation_id, category, requirement_id, breakdown,
               metric_name, status, severity, confidence, detection_rules,
               reason, recommended_decision, source_decision, source_file,
               context_reference
          FROM brsr.review_recommendation
         WHERE master_isin = %s AND fy_slot = %s
         ORDER BY category, recommendation_id""", (isin, fy))
    return _rows(cur)


def deferred_issues(cur, isin, fy):
    cur.execute("""
        SELECT issue_id, issue_group, metric, item, period_role, status,
               reason_for_deferral, risk_impact, must_resolve_before
          FROM brsr.deferred_issue
         WHERE master_isin = %s AND fy_slot = %s ORDER BY issue_id""",
                (isin, fy))
    return _rows(cur)
