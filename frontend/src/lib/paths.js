// URL paths of the app (relative to the router basename /app):
//   /                                  company list
//   /c/:companyId                      -> latest processed financial year
//   /c/:companyId/:fy                  -> overview tab of that year
//   /c/:companyId/:fy/:tab?<params>    a screen; params hold metric,
//                                      breakdown, fy and companies selections
import { useCallback } from "react";
import { useNavigate } from "react-router";

export const TAB_KEYS = ["overview", "environmental", "social", "governance", "trends", "compare", "quality"];

export function companyPath(id, fy, tab, params) {
  let path = `/c/${encodeURIComponent(id)}`;
  if (fy) path += `/${encodeURIComponent(fy)}`;
  if (fy && tab) path += `/${encodeURIComponent(tab)}`;
  const q = params ? new URLSearchParams(Object.entries(params)
    .filter(([, v]) => v !== undefined && v !== null)).toString() : "";
  return q ? `${path}?${q}` : path;
}

// go(companyId, fy, tab, params, { replace }) -> navigate within the router
export function useGo() {
  const navigate = useNavigate();
  return useCallback((id, fy, tab, params, opts) => navigate(companyPath(id, fy, tab, params), opts),
    [navigate]);
}
