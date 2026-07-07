'use client';

import { useEffect, useState } from 'react';

/**
 * SSR-safe media-query hook. Returns `false` on the server and on the first
 * client render (no `window`), then syncs to the real match after mount and
 * stays subscribed to changes. The `false` default means callers should treat
 * "unknown" as the small/mobile branch, which degrades gracefully (a Sheet
 * still works on desktop; a desktop overlay would not fit on mobile).
 */
export function useMediaQuery(query: string): boolean {
  const [matches, setMatches] = useState(false);

  useEffect(() => {
    if (typeof window === 'undefined' || !window.matchMedia) return;
    const mql = window.matchMedia(query);
    const onChange = () => setMatches(mql.matches);
    onChange();
    mql.addEventListener('change', onChange);
    return () => mql.removeEventListener('change', onChange);
  }, [query]);

  return matches;
}
