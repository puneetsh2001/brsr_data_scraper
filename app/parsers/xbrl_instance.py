"""
XBRL instance parser (Phase 6).

Parses one BRSR XBRL instance into plain dicts:

  facts       every element carrying a contextRef, in document order, with
              a stable fact_id, source line, unit measure, decimals /
              precision, parsed numeric value and sign
  contexts    entity, period and dimensions (segment + scenario)
  dimensions  one row per context dimension (explicit and typed)
  units       measures (simple, divide, multiply)
  duplicates  facts with the same concept, context content, unit and
              language, classified COMPLETE / CONSISTENT / INCONSISTENT
              (XBRL Duplicates 1.0 terms)
  issues      structural problems, each with severity and source line

Rules
  - Values are never changed, rounded or converted. `value` is the element
    text with surrounding whitespace removed; `numeric_value` is that text
    parsed as an exact Decimal, or empty when it is not a valid number.
  - Nothing is skipped silently: a fact with an undefined context or unit
    is kept and reported.
  - A document-level rounding declaration (LevelOfRounding...) is recorded,
    never applied.
  - Concept types come from the SEBI taxonomy version the filing declares
    in its schemaRef; numeric-ness is never guessed from the value.
"""

import hashlib
import re
from collections import defaultdict
from decimal import Decimal, InvalidOperation, ROUND_HALF_EVEN
from functools import lru_cache

from lxml import etree

XBRLI = "http://www.xbrl.org/2003/instance"
LINK = "http://www.xbrl.org/2003/linkbase"
XLINK = "http://www.w3.org/1999/xlink"
XBRLDI = "http://xbrl.org/2006/xbrldi"
XSI_NIL = "{http://www.w3.org/2001/XMLSchema-instance}nil"
XML_LANG = "{http://www.w3.org/XML/1998/namespace}lang"

# downloaded files: no entity expansion, no network; text blocks can be big
PARSER = etree.XMLParser(resolve_entities=False, no_network=True,
                         huge_tree=True, remove_comments=False)

DECIMAL_RE = re.compile(r"^[+-]?(\d+(\.\d*)?|\.\d+)$")

XBRLI_NUMERIC = {
    "decimalItemType", "floatItemType", "doubleItemType", "integerItemType",
    "nonPositiveIntegerItemType", "negativeIntegerItemType", "longItemType",
    "intItemType", "shortItemType", "byteItemType",
    "nonNegativeIntegerItemType", "unsignedLongItemType",
    "unsignedIntItemType", "unsignedShortItemType", "unsignedByteItemType",
    "positiveIntegerItemType", "monetaryItemType", "sharesItemType",
    "pureItemType", "fractionItemType"}
DTR_NON_NUMERIC = {
    "noLangTokenItemType", "noLangStringItemType", "prefixedContentItemType",
    "SQNameItemType", "SQNamesItemType", "escapedItemType",
    "xmlNodesItemType", "xmlItemType", "textBlockItemType",
    "domainItemType", "gYearListItemType", "dateTimeItemType"}

# unit ids that name a measure; used only to flag disagreement for review
UNIT_ID_WORDS = {
    "inr": "INR", "rupees": "INR", "pure": "pure", "percent": "pure",
    "joule": "J", "joules": "J", "kilojoule": "kJ", "megajoule": "MJ",
    "gigajoule": "GJ", "gigajoules": "GJ", "terajoule": "TJ",
    "petajoule": "PJ", "tonne": "t", "tonnes": "t", "metrictonne": "t",
    "kilolitre": "kl", "kiloliter": "kl", "kiloliters": "kl",
    "kilolitres": "kl", "litre": "l", "liter": "l", "mtco2e": "MtCO2e",
    "tco2e": "tCO2e", "kwh": "kWh", "mwh": "MWh", "shares": "shares"}

ROUNDING_CONCEPT = re.compile(r"LevelOfRounding", re.I)


# ==========================================================
# concept types per taxonomy version
# ==========================================================

def type_family(data_type, base_types):
    """monetary / shares / pure / numeric / non_numeric"""
    if not data_type:
        return "non_numeric"
    prefix, _, local = data_type.rpartition(":")
    seen = set()
    while prefix not in ("xbrli", "num", "dtr-types", "nonnum") \
            and data_type in base_types and data_type not in seen:
        seen.add(data_type)
        data_type = base_types[data_type]
        prefix, _, local = data_type.rpartition(":")
    if prefix == "xbrli":
        if local == "monetaryItemType":
            return "monetary"
        if local == "sharesItemType":
            return "shares"
        if local == "pureItemType":
            return "pure"
        return "numeric" if local in XBRLI_NUMERIC else "non_numeric"
    if prefix == "num":
        return "pure" if local == "percentItemType" else "numeric"
    if prefix == "dtr-types":
        return "non_numeric" if local in DTR_NON_NUMERIC else "numeric"
    return "non_numeric"


@lru_cache(maxsize=None)
def concept_types(version):
    """{concept: (data_type, family, period_type, abstract)} or {}"""
    from app.taxonomy.taxonomy_registry import (discover_versions,
                                                parse_concepts, parse)
    source = next((s for s in discover_versions()
                   if s["version"] == version), None)
    if source is None:
        return {}
    bases = {}
    types_xsd = source["folder"] / "core" / "in-capmkt-types.xsd"
    if types_xsd.exists():
        xs = "{http://www.w3.org/2001/XMLSchema}"
        for ct in parse(types_xsd).iter(xs + "complexType", xs + "simpleType"):
            restriction = next(ct.iter(xs + "restriction"), None)
            if ct.get("name") and restriction is not None:
                bases["in-capmkt-types:" + ct.get("name")] = \
                    restriction.get("base", "")
    return {name: (c["data_type"], type_family(c["data_type"], bases),
                   c["period_type"], c["abstract"])
            for name, c in parse_concepts(source["folder"]).items()}


# ==========================================================
# helpers
# ==========================================================

def lname(tag):
    return tag.split("}")[-1] if isinstance(tag, str) else ""


def ns(tag):
    return tag[1:].split("}")[0] if isinstance(tag, str) and \
        tag.startswith("{") else ""


def text(el):
    return (el.text or "").strip()


def parse_decimal(value):
    if not DECIMAL_RE.match(value):
        return None
    try:
        return Decimal(value)
    except InvalidOperation:
        return None


def fmt_decimal(d):
    """Exact text form, no exponent, no float conversion."""
    if d is None:
        return ""
    s = format(d, "f")
    return s


def sign_of(d):
    if d is None:
        return ""
    return "ZERO" if d == 0 else ("NEGATIVE" if d < 0 else "POSITIVE")


def round_to(d, decimals):
    if decimals == "INF":
        return d
    q = Decimal(1).scaleb(-int(decimals))
    return d.quantize(q, rounding=ROUND_HALF_EVEN) if int(decimals) >= 0 \
        else (d / q).quantize(Decimal(1), rounding=ROUND_HALF_EVEN) * q


def taxonomy_version_from_href(href):
    match = re.search(r"(\d{4}-\d{2}-\d{2})", href or "")
    return match.group(1) if match else ""


# ==========================================================
# parser
# ==========================================================

def parse_instance(path, doc_key):
    """Parse one XBRL instance. doc_key prefixes every fact_id."""
    out = {"facts": [], "contexts": [], "dimensions": [], "units": [],
           "duplicates": [], "issues": [], "footnotes": [], "document": {}}
    issues = out["issues"]

    def problem(severity, code, detail, line="", fact_id=""):
        issues.append({"doc_key": doc_key, "line": line, "fact_id": fact_id,
                       "severity": severity, "code": code,
                       "detail": detail})

    try:
        root = etree.parse(str(path), PARSER).getroot()
    except etree.XMLSyntaxError as error:
        problem("ERROR", "XML_NOT_WELL_FORMED", str(error))
        return out
    if lname(root.tag) != "xbrl" or ns(root.tag) != XBRLI:
        problem("ERROR", "NOT_AN_XBRL_INSTANCE", f"root element {root.tag}")
        return out

    # ---- schemaRef / taxonomy version --------------------------------
    hrefs = [el.get(f"{{{XLINK}}}href", "") for el in root
             if lname(el.tag) == "schemaRef" and ns(el.tag) == LINK]
    versions = sorted({taxonomy_version_from_href(h) for h in hrefs} - {""})
    version = versions[0] if len(versions) == 1 else ""
    if not hrefs:
        problem("ERROR", "NO_SCHEMA_REF", "instance declares no taxonomy")
    elif len(versions) != 1:
        problem("ERROR", "TAXONOMY_VERSION_UNCLEAR", " | ".join(hrefs))
    types = concept_types(version) if version else {}
    if version and not types:
        problem("ERROR", "TAXONOMY_VERSION_NOT_REGISTERED", version)
    out["document"] = {"doc_key": doc_key, "schema_refs": " | ".join(hrefs),
                       "taxonomy_version": version}

    # ---- contexts ----------------------------------------------------
    contexts = {}
    for el in root.iter(f"{{{XBRLI}}}context"):
        cid = el.get("id", "")
        line = el.sourceline
        if not cid:
            problem("ERROR", "CONTEXT_WITHOUT_ID", "", line)
            continue
        if cid in contexts:
            problem("ERROR", "DUPLICATE_CONTEXT_ID", cid, line)
            continue
        ident = next(el.iter(f"{{{XBRLI}}}identifier"), None)
        period = next(el.iter(f"{{{XBRLI}}}period"), None)
        start = end = instant = ""
        ptype = ""
        if period is not None:
            for p in period:
                name = lname(p.tag)
                if name == "startDate":
                    start = text(p)
                elif name == "endDate":
                    end = text(p)
                elif name == "instant":
                    instant = text(p)
                elif name == "forever":
                    ptype = "forever"
        ptype = ptype or ("instant" if instant else
                          "duration" if start or end else "")
        if not ptype:
            problem("ERROR", "CONTEXT_WITHOUT_PERIOD", cid, line)
        elif ptype == "duration" and (not start or not end):
            problem("ERROR", "DURATION_INCOMPLETE", cid, line)
        elif ptype == "duration" and end < start:
            problem("ERROR", "PERIOD_END_BEFORE_START",
                    f"{cid}: {start} > {end}", line)
        for value in (start, end, instant):
            if value and not re.match(r"^\d{4}-\d{2}-\d{2}", value):
                problem("ERROR", "INVALID_PERIOD_DATE", f"{cid}: {value}",
                        line)

        dims = []
        for container in ("segment", "scenario"):
            for c in el.iter(f"{{{XBRLI}}}{container}"):
                for member in c:
                    kind = lname(member.tag)
                    if ns(member.tag) != XBRLDI:
                        problem("WARNING", "NON_DIMENSIONAL_CONTENT",
                                f"{cid}: {member.tag} in {container}",
                                member.sourceline)
                        continue
                    typed = kind == "typedMember"
                    dims.append({
                        "doc_key": doc_key, "context_id": cid,
                        "container": container,
                        "dimension": member.get("dimension", ""),
                        "member": "" if typed else text(member),
                        "typed_value": " ".join(
                            t.strip() for t in member.itertext()
                            if t.strip()) if typed else "",
                        "is_typed": typed})
        dim_names = [d["dimension"] for d in dims]
        if len(dim_names) != len(set(dim_names)):
            problem("ERROR", "REPEATED_DIMENSION_IN_CONTEXT", cid, line)
        dims.sort(key=lambda d: (d["dimension"], d["member"],
                                 d["typed_value"]))
        dim_key = " | ".join(
            f"{d['dimension']}={d['member'] or '[' + d['typed_value'] + ']'}"
            for d in dims)
        scheme = ident.get("scheme", "") if ident is not None else ""
        identifier = text(ident) if ident is not None else ""
        content = "\x1f".join([scheme, identifier, ptype, start, end,
                               instant, dim_key])
        contexts[cid] = {
            "doc_key": doc_key, "context_id": cid,
            "entity_scheme": scheme, "entity_identifier": identifier,
            "period_type": ptype, "period_start": start, "period_end": end,
            "instant": instant, "dimension_count": len(dims),
            "dimension_key": dim_key,
            "context_key": hashlib.sha1(content.encode()).hexdigest()[:16],
            "line": line}
        out["dimensions"] += dims
    entities = {(c["entity_scheme"], c["entity_identifier"])
                for c in contexts.values()}
    if len(entities) > 1:
        problem("ERROR", "MULTIPLE_ENTITY_IDENTIFIERS",
                " | ".join(f"{s} {i}" for s, i in sorted(entities)))
    by_content = defaultdict(list)
    for c in contexts.values():
        by_content[c["context_key"]].append(c["context_id"])
    for ids in by_content.values():
        if len(ids) > 1:
            problem("INFO", "EQUIVALENT_CONTEXTS", " | ".join(ids))

    # ---- units -------------------------------------------------------
    units = {}
    for el in root.iter(f"{{{XBRLI}}}unit"):
        uid = el.get("id", "")
        if uid in units:
            problem("ERROR", "DUPLICATE_UNIT_ID", uid, el.sourceline)
            continue
        divide = next(el.iter(f"{{{XBRLI}}}divide"), None)
        if divide is not None:
            num = [text(m) for n in divide.iter(f"{{{XBRLI}}}unitNumerator")
                   for m in n.iter(f"{{{XBRLI}}}measure")]
            den = [text(m) for d in divide.iter(
                f"{{{XBRLI}}}unitDenominator")
                for m in d.iter(f"{{{XBRLI}}}measure")]
            kind = "divide"
        else:
            num = [text(m) for m in el.iter(f"{{{XBRLI}}}measure")]
            den = []
            kind = "simple" if len(num) == 1 else "multiply"
        measure = "*".join(sorted(num)) + ("/" + "*".join(sorted(den))
                                           if den else "")
        if not num:
            problem("ERROR", "UNIT_WITHOUT_MEASURE", uid, el.sourceline)
        units[uid] = {"doc_key": doc_key, "unit_id": uid, "unit_kind": kind,
                      "numerator": " * ".join(num),
                      "denominator": " * ".join(den), "measure": measure,
                      "line": el.sourceline}
        named = UNIT_ID_WORDS.get(uid.lower().split("per")[0])
        if named and kind != "multiply" and num and \
                num[0].split(":")[-1] != named:
            problem("WARNING", "UNIT_ID_DISAGREES_WITH_MEASURE",
                    f"unit id {uid} but measure {measure}", el.sourceline)

    # ---- footnotes ---------------------------------------------------
    footnote_of = defaultdict(list)
    for link in root.iter(f"{{{LINK}}}footnoteLink"):
        locs, notes = {}, {}
        for child in link:
            kind = child.get(f"{{{XLINK}}}type")
            label = child.get(f"{{{XLINK}}}label")
            if kind == "locator":
                locs.setdefault(label, []).append(
                    child.get(f"{{{XLINK}}}href", "").split("#")[-1])
            elif kind == "resource":
                notes[label] = " ".join(t.strip() for t in child.itertext()
                                        if t.strip())
        for arc in link:
            if arc.get(f"{{{XLINK}}}type") != "arc":
                continue
            for xml_id in locs.get(arc.get(f"{{{XLINK}}}from"), []):
                note = notes.get(arc.get(f"{{{XLINK}}}to"), "")
                footnote_of[xml_id].append(note)
                out["footnotes"].append({"doc_key": doc_key,
                                         "fact_xml_id": xml_id,
                                         "footnote": note})

    # ---- facts -------------------------------------------------------
    used_contexts, used_units = set(), set()
    seq = 0
    for el in root.iter():
        if not isinstance(el.tag, str) or el.get("contextRef") is None:
            continue
        seq += 1
        fact_id = f"{doc_key}#{seq:06d}"
        line = el.sourceline
        concept = lname(el.tag)
        cref = el.get("contextRef", "")
        uref = el.get("unitRef")
        decimals = el.get("decimals")
        precision = el.get("precision")
        is_nil = el.get(XSI_NIL, "").strip().lower() in ("true", "1")
        codes = []

        def flag(severity, code, detail=""):
            codes.append(code)
            problem(severity, code, detail or concept, line, fact_id)

        if len(el):
            flag("WARNING", "FACT_HAS_CHILD_ELEMENTS",
                 f"{concept}: value taken from all text content")
            value = " ".join(t.strip() for t in el.itertext() if t.strip())
        else:
            value = (el.text or "").strip()
        if el.getparent() is not root:
            flag("WARNING", "FACT_NOT_TOP_LEVEL",
                 f"{concept} inside {lname(el.getparent().tag)}")

        context = contexts.get(cref)
        if context is None:
            flag("ERROR", "UNDEFINED_CONTEXT", f"{concept}: {cref}")
        used_contexts.add(cref)

        ctype = types.get(concept)
        if types and ctype is None:
            flag("WARNING", "CONCEPT_NOT_IN_TAXONOMY",
                 f"{concept} not in {version}")
        family = ctype[1] if ctype else ("numeric" if uref is not None
                                         else "unknown")
        numeric = family in ("monetary", "shares", "pure", "numeric")
        if ctype and ctype[3]:
            flag("ERROR", "ABSTRACT_CONCEPT_REPORTED", concept)

        unit = units.get(uref) if uref is not None else None
        if uref is not None:
            used_units.add(uref)
            if unit is None:
                flag("ERROR", "UNDEFINED_UNIT", f"{concept}: {uref}")
        if numeric and uref is None:
            flag("ERROR", "NUMERIC_FACT_WITHOUT_UNIT", concept)
        if not numeric and uref is not None and ctype:
            flag("WARNING", "UNIT_ON_NON_NUMERIC_FACT", f"{concept}: {uref}")
        if unit and ctype:
            m = unit["measure"]
            if family == "monetary" and not m.startswith("iso4217:"):
                flag("WARNING", "UNIT_TYPE_MISMATCH",
                     f"monetary {concept} with {m}")
            if family == "shares" and m != "xbrli:shares":
                flag("WARNING", "UNIT_TYPE_MISMATCH",
                     f"shares {concept} with {m}")
            if family == "pure" and m != "xbrli:pure":
                flag("WARNING", "UNIT_TYPE_MISMATCH",
                     f"pure/percent {concept} with {m}")

        if decimals is not None and precision is not None:
            flag("ERROR", "DECIMALS_AND_PRECISION", concept)
        if numeric and not is_nil and decimals is None and precision is None:
            flag("ERROR", "NUMERIC_WITHOUT_DECIMALS_OR_PRECISION", concept)
        if is_nil and (decimals is not None or precision is not None):
            flag("WARNING", "NIL_FACT_WITH_DECIMALS", concept)
        if decimals is not None and decimals != "INF" and \
                not re.match(r"^-?\d+$", decimals):
            flag("ERROR", "INVALID_DECIMALS", f"{concept}: {decimals!r}")
        if not numeric and (decimals is not None or precision is not None) \
                and ctype:
            flag("WARNING", "DECIMALS_ON_NON_NUMERIC_FACT", concept)

        number = None
        if is_nil:
            status = "NIL"
            if value:
                flag("ERROR", "NIL_FACT_HAS_VALUE", concept)
        elif not value:
            status = "EMPTY"
            if numeric:
                flag("ERROR", "EMPTY_NUMERIC_FACT", concept)
        else:
            status = "REPORTED"
            if numeric:
                number = parse_decimal(value)
                if number is None:
                    flag("ERROR", "NOT_A_VALID_NUMBER",
                         f"{concept}: {value[:60]!r}")
        places = ""
        if number is not None and "." in value:
            places = len(value.split(".")[1])
        if number is not None and decimals not in (None, "INF") and \
                re.match(r"^-?\d+$", decimals or "") and places != "" \
                and places > int(decimals):
            flag("INFO", "VALUE_MORE_PRECISE_THAN_DECIMALS",
                 f"{concept}: {value} decimals={decimals}")
        if ROUNDING_CONCEPT.search(concept) and value:
            out["document"]["declared_rounding"] = value
            problem("INFO", "ROUNDING_DECLARED_NOT_APPLIED",
                    f"{concept} = {value}", line, fact_id)

        out["facts"].append({
            "fact_id": fact_id, "doc_key": doc_key, "line": line,
            "xml_id": el.get("id", ""), "concept": concept,
            "namespace": ns(el.tag), "context_id": cref,
            "context_key": context["context_key"] if context else "",
            "entity_identifier": context["entity_identifier"]
            if context else "",
            "period_type": context["period_type"] if context else "",
            "period_start": context["period_start"] if context else "",
            "period_end": context["period_end"] if context else "",
            "instant": context["instant"] if context else "",
            "dimension_key": context["dimension_key"] if context else "",
            "unit_id": uref or "",
            "unit_measure": unit["measure"] if unit else "",
            "data_type": ctype[0] if ctype else "",
            "type_family": family, "value": value, "value_status": status,
            "numeric_value": fmt_decimal(number), "sign": sign_of(number),
            "decimals": decimals or "", "precision": precision or "",
            "value_decimal_places": places,
            "xml_lang": el.get(XML_LANG, ""),
            "footnotes": " | ".join(footnote_of.get(el.get("id", ""), [])),
            "duplicate_group": "", "duplicate_class": "",
            "issue_codes": " | ".join(codes)})

    for cid in contexts:
        if cid not in used_contexts:
            problem("INFO", "UNUSED_CONTEXT", cid, contexts[cid]["line"])
    for uid in units:
        if uid not in used_units:
            problem("INFO", "UNUSED_UNIT", uid, units[uid]["line"])

    out["contexts"] = list(contexts.values())
    out["units"] = list(units.values())
    out["duplicates"] = classify_duplicates(out["facts"], doc_key, problem)
    return out


# ==========================================================
# duplicates
# ==========================================================

def classify_duplicates(facts, doc_key, problem):
    groups = defaultdict(list)
    for f in facts:
        if not f["context_key"]:
            continue
        groups[(f["namespace"], f["concept"], f["context_key"],
                f["unit_measure"], f["xml_lang"])].append(f)
    rows = []
    n = 0
    for key, members in groups.items():
        if len(members) < 2:
            continue
        n += 1
        group_id = f"{doc_key}#D{n:04d}"
        cls = duplicate_class(members)
        for f in members:
            f["duplicate_group"] = group_id
            f["duplicate_class"] = cls
            rows.append({"doc_key": doc_key, "duplicate_group": group_id,
                         "duplicate_class": cls, "fact_id": f["fact_id"],
                         "line": f["line"], "concept": f["concept"],
                         "context_id": f["context_id"],
                         "dimension_key": f["dimension_key"],
                         "unit_measure": f["unit_measure"],
                         "value": f["value"][:500],
                         "decimals": f["decimals"],
                         "value_status": f["value_status"]})
        severity = "ERROR" if cls == "INCONSISTENT" else "WARNING"
        problem(severity, f"DUPLICATE_FACT_{cls}",
                f"{members[0]['concept']} x{len(members)} "
                f"({members[0]['context_id']})", members[0]["line"],
                members[0]["fact_id"])
    return rows


def duplicate_class(members):
    statuses = {f["value_status"] for f in members}
    if len(statuses) > 1:
        return "INCONSISTENT"
    if statuses == {"NIL"}:
        return "COMPLETE"
    numeric = all(f["numeric_value"] for f in members)
    if not numeric:
        values = {" ".join(f["value"].split()) for f in members}
        return "COMPLETE" if len(values) == 1 else "INCONSISTENT"
    if len({(f["numeric_value"], f["decimals"]) for f in members}) == 1:
        return "COMPLETE"
    decs = [f["decimals"] for f in members if f["decimals"]]
    if len(decs) != len(members):
        return "INCONSISTENT"        # precision-only or missing decimals
    finite = [int(d) for d in decs if d != "INF"]
    lowest = str(min(finite)) if finite else "INF"
    rounded = {round_to(Decimal(f["numeric_value"]), lowest)
               for f in members}
    return "CONSISTENT" if len(rounded) == 1 else "INCONSISTENT"
