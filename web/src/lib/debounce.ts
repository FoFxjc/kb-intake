import { useEffect, useState } from 'react';

export const SEARCH_DEBOUNCE_MS = 200;

/** `value` once it has not changed for `ms` milliseconds (the search box: one request per pause in typing). */
export function useDebounced<T>(value: T, ms: number = SEARCH_DEBOUNCE_MS): T {
  const [out, setOut] = useState(value);
  useEffect(() => {
    if (Object.is(out, value)) return;
    const t = window.setTimeout(() => setOut(value), ms);
    return () => window.clearTimeout(t);
  }, [value, ms, out]);
  return out;
}
