// Vitest runs with globals enabled, so @testing-library/react registers its
// own cleanup after each test.
import "@testing-library/jest-dom/vitest";

window.BRSR_CONFIG = {
  apiBase: "",
  summaryMetricKeys: ["energy_total"],
  locale: "en-IN",
};
