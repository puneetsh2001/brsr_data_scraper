// Formatting and status vocabularies (labels for the API's status codes).

const LOCALE = (window.BRSR_CONFIG && window.BRSR_CONFIG.locale) || "en-IN";

// Exact decimal strings from the API are formatted without converting to a
// float (values such as 0.0000874155 or 72948754.5 keep every digit).
export function formatNumber(text) {
  if (text === null || text === undefined || text === "") return null;
  const s = String(text).trim();
  const m = s.match(/^(-?)(\d+)(?:\.(\d+))?$/);
  if (!m) return s;
  const [, sign, int, frac] = m;
  const grouped = BigInt(int).toLocaleString(LOCALE);
  return `${sign}${grouped}${frac ? "." + frac : ""}`;   // digits kept as filed
}

// Display labels for units. Only the label changes: the unit code and the
// value are never altered, and the exact unit is shown in the details drawer.
const MEASURES = {
  J: "J", kJ: "kJ", MJ: "MJ", GJ: "GJ", TJ: "TJ", PJ: "PJ",
  kWh: "kWh", MWh: "MWh", GWh: "GWh",
  tCO2e: "tCO₂e", ktCO2e: "ktCO₂e", MtCO2e: "MtCO₂e", GtCO2e: "GtCO₂e",
  t: "tonnes", kt: "kilotonnes", kg: "kg",
  kl: "kL", kL: "kL", m3: "m³", l: "litres", ML: "megalitres",
  INR: "₹", count: "count", "%": "%",
};
const NOTES = {
  MtCO2e: "MtCO₂e as filed: the symbol is ambiguous (metric tonnes or million tonnes). "
    + "It is converted only where an owner decision approved one reading.",
  GtCO2e: "GtCO₂e as filed: implausible for one company; not converted without an approved decision.",
  pure: "No physical unit: the filing reports this value with the XBRL unit 'pure'.",
};

function measure(code, filed) {
  const local = code.trim().split(":").pop();
  if (local === "pure") return filed ? "no unit stated" : "number";
  return MEASURES[local] || local;
}

// "Non-SI:GJ / iso4217:INR" -> "GJ per ₹"; filed=true for units as filed
export function unitLabel(unit, filed = false) {
  if (!unit) return "";
  const [num, den] = unit.split("/").map((x) => x && x.trim());
  return den ? `${measure(num, filed)} per ${measure(den, filed)}` : measure(num, filed);
}

// tooltip with the exact unit code and, for ambiguous units, an explanation
export function unitTitle(unit) {
  if (!unit) return "";
  const note = unit.split("/").map((p) => NOTES[p.trim().split(":").pop()]).find(Boolean);
  return note ? `${unit} — ${note}` : unit;
}

// [label, css class, explanation]
export const DISCLOSURE = {
  REPORTED: ["Reported", "b-ok", "Value reported in the BRSR XBRL filing."],
  REPORTED_ZERO: ["Reported zero", "b-zero", "The company reported the value 0 in its filing."],
  REPORTED_TEXT: ["Reported (text)", "b-ok", "Text value reported in the filing."],
  REPORTED_EMPTY: ["Empty in filing", "b-warn", "The fact is present in the filing but has no value."],
  REPORTED_PENDING_CROSSWALK_REVIEW: ["Pending crosswalk review", "b-warn",
    "The value reached this metric only through a candidate concept mapping that is awaiting review."],
  NOT_APPLICABLE: ["Not applicable", "b-na", "The filing states that this disclosure does not apply to the company."],
  NO_FILING_FOR_YEAR: ["No filing for year", "b-na", "There is no BRSR filing for this financial year."],
  EXTRACTION_ERROR: ["Extraction error", "b-bad", "A filing exists but could not be downloaded or parsed."],
  NO_VALUE_IN_FILING: ["No value in filing", "b-na",
    "The filing has no current-year value for this metric (not disclosed, or not in the filing's taxonomy version)."],
  NOT_IN_BRSR_XBRL: ["Not in BRSR XBRL", "b-na", "This metric is not part of the BRSR XBRL filing; it needs another source."],
};

export const BASIS = {
  REPORTED_AS_FILED: ["As filed", "b-ok", "Shown exactly as filed; no conversion was needed."],
  CALCULATED_UNIT_CONVERSION: ["Calculated: unit conversion", "b-calc",
    "Converted from the filed unit by a fixed unit-conversion rule (formula in the details)."],
  CALCULATED_APPROVED_UNIT_DECISION: ["Calculated: approved unit decision", "b-calc",
    "Converted using an owner-approved interpretation of an ambiguous unit."],
  REPORTED_NOT_NORMALISED: ["Not normalised", "b-warn",
    "The filed unit is missing, ambiguous or awaiting a decision; only the filed value is shown."],
};

// publication_status from the API: only the PUBLISHED* statuses are
// comparable (charts, comparisons, summary calculations)
export const PUBLICATION = {
  PUBLISHED: ["Published", "b-ok", "Reported and normalised; used in charts and comparisons."],
  PUBLISHED_APPROVED_DECISION: ["Published: approved decision", "b-calc",
    "Normalised under an owner-approved unit decision; used in charts and comparisons."],
  DEFERRED_DATA_QUALITY: ["Deferred data-quality issue", "b-warn",
    "Linked to an open deferred data-quality issue; shown as filed and excluded from charts and comparisons."],
  PENDING_SCALE_REVIEW: ["Suspected scale error — pending decision", "b-warn",
    "A filer scale-error record is awaiting an owner decision; the filed value is shown and it is not compared."],
  PENDING_CROSSWALK_REVIEW: ["Pending crosswalk review", "b-warn",
    "Reached this metric only through a candidate concept mapping; not compared."],
  PENDING_UNIT_DECISION: ["Pending unit decision", "b-warn",
    "The filed unit is ambiguous and awaits an owner decision; the filed value is shown, not compared."],
  FLAGGED_RESTATEMENT_DIFFERS: ["Flagged: restatement differs", "b-warn",
    "The unit interpretation is flagged because a later restatement differs; not verified, not compared."],
  UNDETERMINED_UNIT: ["Undetermined unit — manual review", "b-warn",
    "The unit could not be determined; the filed value is shown, not compared."],
  NOT_NORMALISED: ["Not comparable: unit not normalised", "b-na",
    "The filed unit is missing or ambiguous; the filed value is shown, not compared."],
  TEXT_VALUE: ["Text value", "b-na", "Text disclosure; not a number."],
};

export const isComparable = (v) => !!(v && v.comparable && v.value_numeric !== null && v.value_numeric !== undefined);

export function info(table, code) {
  return table[code] || [code || "Unknown", "b-na", ""];
}

export function fy(label) {
  return label ? `FY ${label}` : "";
}

export function metricLabel(m) {
  return m.metric_name || m.requirement_id;
}

// breakdown dimension labels, e.g. "Permanent Employees / Male"
export function breakdownLabel(dimensions) {
  if (!dimensions || !dimensions.length) return "";
  return dimensions.map((d) => d.label).join(" / ");
}

export function displayValue(v) {
  // returns {text, unit, missing}
  if (v.value !== null && v.value !== undefined) {
    const isNum = v.value_numeric !== null && v.value_numeric !== undefined;
    return { text: isNum ? formatNumber(v.value) : String(v.value),
             unit: isNum ? unitLabel(v.unit) : "", rawUnit: isNum ? v.unit || "" : "", missing: false };
  }
  if (v.withheld && (v.reported_value === null || v.reported_value === undefined)) {
    return { text: "Withheld", unit: "", missing: false, withheld: true };
  }
  if (v.reported_value !== undefined && v.reported_value !== null && v.reported_value !== "") {
    return { text: formatNumber(v.reported_value) ?? v.reported_value,
             unit: v.reported_unit ? unitLabel(v.reported_unit, true) : "no unit stated",
             rawUnit: v.reported_unit || "", missing: false, asFiled: true };
  }
  return { text: "Not available", unit: "", missing: true };
}
