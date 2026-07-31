import { useCallback, useEffect, useState } from 'react';

/**
 * Load one thing, tolerate its own failure, expose a reload.
 *
 * The scan section used to load all six of its resources in a single
 * `Promise.all` shared by every block, so visiting one page fetched everything
 * and one shared status string reported the outcome of six unrelated
 * operations. Each page now owns exactly the resources it renders.
 *
 * A failed load leaves `data` at its fallback rather than blanking the page —
 * an unavailable endpoint should degrade one panel, not the whole section.
 */
export function useResource(fetcher, fallback = null, deps = []) {
  const [data, setData] = useState(fallback);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  // eslint-disable-next-line react-hooks/exhaustive-deps
  const run = useCallback(fetcher, deps);

  const reload = useCallback(async () => {
    try {
      setData(await run());
      setError('');
    } catch (e) {
      setError(e.message || String(e));
    } finally {
      setLoading(false);
    }
  }, [run]);

  useEffect(() => { reload(); }, [reload]);

  return { data, loading, error, reload, setData };
}

/**
 * Status line for one page's mutations. Returns a setter plus the props needed
 * to render <Notice>, so pages don't each re-declare the same two bits of state.
 */
export function useStatus() {
  const [status, setStatus] = useState(null);

  const say = useCallback((text, tone = 'info') => setStatus(text ? { text, tone } : null), []);
  const fail = useCallback((text) => setStatus({ text, tone: 'bad' }), []);
  const clear = useCallback(() => setStatus(null), []);

  return { status, say, fail, clear };
}
