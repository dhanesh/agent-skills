import { useEffect, useState } from 'react';

/**
 * useHydrated — returns false during SSR and the first client render, then
 * true after the component has mounted in the browser.
 *
 * This is the linchpin of the progressive-enhancement contract (RT-3 / U3):
 * because the very first render (server + first client paint) returns the
 * SAME tree as a no-JS visitor would see, the server-rendered HTML always
 * contains the full, usable static fallback. Enhancement is applied only
 * once `hydrated` flips to true on the client.
 */
export function useHydrated(): boolean {
  const [hydrated, setHydrated] = useState(false);
  useEffect(() => {
    setHydrated(true);
  }, []);
  return hydrated;
}

/**
 * usePrefersReducedMotion — respects the OS "reduce motion" setting so
 * transitions can be disabled for users who request it.
 */
export function usePrefersReducedMotion(): boolean {
  const [reduced, setReduced] = useState(false);
  useEffect(() => {
    const mq = window.matchMedia('(prefers-reduced-motion: reduce)');
    const update = () => setReduced(mq.matches);
    update();
    mq.addEventListener('change', update);
    return () => mq.removeEventListener('change', update);
  }, []);
  return reduced;
}
