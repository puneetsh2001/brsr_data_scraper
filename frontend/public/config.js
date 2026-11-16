// Frontend configuration (edit per environment; no data lives here).
// apiBase: the analytics API origin. Empty string = same origin (the API
// serves this frontend at /app/). For a separate dev server, set e.g.
// "http://localhost:8000" and enable CORS on the API (BRSR_CORS_ORIGINS).
window.BRSR_CONFIG = Object.assign({
  apiBase: "",
  // metric keys shown as overview summary cards, in this order; values,
  // units and statuses always come from the API
  summaryMetricKeys: [
    "energy_total", "energy_renewable_total", "energy_intensity_per_rupee",
    "scope1", "scope2", "water_withdrawal_total", "waste_generated_total",
    "board_total", "board_female", "posh_complaints_filed"
  ],
  locale: "en-IN"
}, window.BRSR_CONFIG || {});
