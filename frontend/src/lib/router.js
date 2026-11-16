// Hash routes, identical to the previous UI: #/c/<company_id>/<fy>/<tab>?<params>
import { useEffect, useState } from "react";

export function parseRoute(hash = window.location.hash) {
  const raw = hash.replace(/^#\/?/, "");
  const [path, qs] = raw.split("?");
  const [kind, id, fyLabel, tab] = path.split("/").map(decodeURIComponent);
  return { kind, id, fy: fyLabel, tab: tab || "overview", params: new URLSearchParams(qs || "") };
}

export function routeHash(id, fyLabel, tab, params) {
  const q = params ? new URLSearchParams(Object.entries(params)
    .filter(([, v]) => v !== undefined && v !== null)).toString() : "";
  return `#/c/${encodeURIComponent(id)}/${encodeURIComponent(fyLabel || "")}/${tab}${q ? "?" + q : ""}`;
}

export function routeTo(id, fyLabel, tab, params) {
  window.location.hash = routeHash(id, fyLabel, tab, params);
}

export function useHashRoute() {
  const [hash, setHash] = useState(window.location.hash);
  useEffect(() => {
    const on = () => setHash(window.location.hash);
    window.addEventListener("hashchange", on);
    return () => window.removeEventListener("hashchange", on);
  }, []);
  return { hash, route: parseRoute(hash) };
}
