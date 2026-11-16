// Run an async loader whenever `key` changes; stale results are ignored.
import { useCallback, useEffect, useRef, useState } from "react";

export function useAsync(load, key, { enabled = true } = {}) {
  const [state, setState] = useState({ loading: enabled, data: null, error: null });
  const seq = useRef(0);
  const loadRef = useRef(load);
  loadRef.current = load;

  const run = useCallback(() => {
    const mine = ++seq.current;
    setState({ loading: true, data: null, error: null });
    Promise.resolve()
      .then(() => loadRef.current())
      .then((data) => { if (mine === seq.current) setState({ loading: false, data, error: null }); })
      .catch((error) => { if (mine === seq.current) setState({ loading: false, data: null, error }); });
  }, []);

  useEffect(() => {
    if (!enabled) { seq.current++; setState({ loading: false, data: null, error: null }); return; }
    run();
    return () => { seq.current++; };
  }, [key, enabled, run]);

  return { ...state, reload: run };
}
