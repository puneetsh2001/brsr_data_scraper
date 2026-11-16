// Thin client for the BRSR analytics API. The base URL comes from
// config.js (window.BRSR_CONFIG.apiBase); responses are cached per URL for
// the session because the data is read-only.

const BASE = (window.BRSR_CONFIG && window.BRSR_CONFIG.apiBase || "").replace(/\/$/, "");
const cache = new Map();

export class ApiError extends Error {
  constructor(status, message, url) {
    super(message);
    this.status = status;
    this.url = url;
  }
}

function query(params) {
  const q = new URLSearchParams();
  for (const [k, v] of Object.entries(params || {})) {
    if (v === undefined || v === null || v === "") continue;
    if (Array.isArray(v)) v.forEach((x) => q.append(k, x));
    else q.append(k, v);
  }
  const s = q.toString();
  return s ? `?${s}` : "";
}

export async function get(path, params) {
  const url = `${BASE}/api${path}${query(params)}`;
  if (cache.has(url)) return cache.get(url);
  const p = (async () => {
    let res;
    try {
      res = await fetch(url, { headers: { Accept: "application/json" } });
    } catch (e) {
      throw new ApiError(0, `The analytics API is not reachable (${e.message}).`, url);
    }
    let body = null;
    try { body = await res.json(); } catch (e) { /* non-JSON error */ }
    if (!res.ok) {
      const detail = body && body.detail;
      const msg = typeof detail === "string" ? detail
        : Array.isArray(detail) ? detail.map((d) => d.msg).join("; ")
        : `HTTP ${res.status}`;
      throw new ApiError(res.status, msg, url);
    }
    return body;
  })();
  cache.set(url, p);
  p.catch(() => cache.delete(url));
  return p;
}

const enc = encodeURIComponent;

export const api = {
  health: () => get("/health"),
  catalog: () => get("/metrics"),
  search: (q, limit = 25) => get("/companies", { q, limit }),
  company: (id) => get(`/companies/${enc(id)}`),
  yearMetrics: (id, fy, metrics) =>
    get(`/companies/${enc(id)}/years/${enc(fy)}/metrics`, metrics ? { metric: metrics } : undefined),
  pillar: (id, fy, p) => get(`/companies/${enc(id)}/years/${enc(fy)}/pillars/${enc(p)}`),
  workforce: (id, fy) => get(`/companies/${enc(id)}/years/${enc(fy)}/workforce`),
  posh: (id, fy) => get(`/companies/${enc(id)}/years/${enc(fy)}/posh`),
  energy: (id, fy) => get(`/companies/${enc(id)}/years/${enc(fy)}/energy`),
  quality: (id, fy) => get(`/companies/${enc(id)}/years/${enc(fy)}/data-quality`),
  history: (id, metric, breakdown) =>
    get(`/companies/${enc(id)}/metrics/${enc(metric)}/history`, { breakdown }),
  compare: (metric, companies, fy, breakdown) =>
    get("/compare", { metric, companies, fy, breakdown }),
};
