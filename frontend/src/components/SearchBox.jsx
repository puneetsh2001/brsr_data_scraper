// Company search with a results list. The input is uncontrolled and uses a
// native "input" listener, so typing and programmatic input events behave
// the same.
import { useEffect, useRef, useState } from "react";
import { api } from "../lib/api.js";

export function SearchBox({ id, placeholder, ariaLabel, limit = 15, delay = 180, exclude = [],
  showOnFocus = true, renderMeta, onSelect, className = "search", style }) {
  const inputRef = useRef(null);
  const boxRef = useRef(null);
  const [state, setState] = useState({ open: false, items: [], error: null });
  const seq = useRef(0);
  const timer = useRef(null);
  const excludeKey = exclude.join(",");

  useEffect(() => {
    const input = inputRef.current;
    const search = async (q) => {
      const mine = ++seq.current;
      if (!showOnFocus && !q) { setState({ open: false, items: [], error: null }); return; }
      try {
        const r = await api.search(q, limit);
        if (mine !== seq.current) return;
        const items = r.companies.filter((c) => !excludeKey.split(",").includes(c.company_id));
        setState({ open: showOnFocus || items.length > 0, items, error: null });
      } catch (e) {
        if (mine === seq.current) setState({ open: true, items: [], error: e });
      }
    };
    const onInput = () => { clearTimeout(timer.current); timer.current = setTimeout(() => search(input.value.trim()), delay); };
    const onFocus = () => { if (showOnFocus) search(input.value.trim()); };
    const onKey = (e) => {
      if (e.key === "Escape") setState((s) => ({ ...s, open: false }));
      if (e.key === "Enter") { const first = boxRef.current.querySelector(".search-results button"); if (first) first.click(); }
    };
    const onDoc = (e) => { if (boxRef.current && !boxRef.current.contains(e.target)) setState((s) => ({ ...s, open: false })); };
    input.addEventListener("input", onInput);
    input.addEventListener("focus", onFocus);
    input.addEventListener("keydown", onKey);
    document.addEventListener("click", onDoc);
    return () => {
      clearTimeout(timer.current);
      input.removeEventListener("input", onInput);
      input.removeEventListener("focus", onFocus);
      input.removeEventListener("keydown", onKey);
      document.removeEventListener("click", onDoc);
    };
  }, [limit, delay, excludeKey, showOnFocus]);

  const choose = (c) => {
    setState({ open: false, items: [], error: null });
    inputRef.current.value = "";
    onSelect(c);
  };

  return (
    <div className={className} id={id} ref={boxRef} style={style}>
      <input ref={inputRef} type="search" placeholder={placeholder} aria-label={ariaLabel} autoComplete="off" />
      <div className="search-results" role="listbox" hidden={!state.open}>
        {state.error ? <div className="state error small">{state.error.message}</div>
          : state.items.length ? state.items.map((c) => (
            <button role="option" aria-selected="false" key={c.company_id} onClick={() => choose(c)}>
              <span>{c.legal_name}</span>
              <span className="sym">{renderMeta ? renderMeta(c) : c.nse_symbol}</span>
            </button>
          ))
            : <div className="state small">No company matches.</div>}
      </div>
    </div>
  );
}
