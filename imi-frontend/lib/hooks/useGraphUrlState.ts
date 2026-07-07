'use client';

import { useCallback, useMemo } from 'react';
import { usePathname, useRouter, useSearchParams } from 'next/navigation';
import type { GraphLayers, ViewMode } from '@/components/graph/layers';

const VIEW_MODES: readonly ViewMode[] = ['picker', 'neighborhood', 'full', 'influence'];
const LAYER_VALUES: readonly GraphLayers[] = ['entities', 'decisions', 'signals'];

export interface GraphUrlState {
  mode?: ViewMode;
  seed?: string;
  depth?: number;
  layers?: GraphLayers;
}

interface GraphSyncState {
  mode: ViewMode;
  seed: string | null;
  depth: number;
  layers: GraphLayers;
}

/**
 * Parse-once / sync-on-change bridge between the graph page's view state and the
 * URL query string. Makes neighborhood/influence views shareable without
 * disturbing the `?snapshot` param the page reads directly.
 *
 * - `initial` is parsed ONCE on mount from the current search params. Invalid
 *   values are omitted so the page falls back to its own defaults.
 * - `sync` writes back via `router.replace` (scroll-preserving), omitting
 *   defaults, preserving any existing `snapshot`, and no-opping when the URL
 *   already matches.
 */
export function useGraphUrlState(): {
  initial: GraphUrlState;
  sync(s: GraphSyncState): void;
} {
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();

  // Parse once on mount. searchParams is a stable-ish object across renders, but
  // we deliberately capture only the first value so later URL writes don't
  // re-derive `initial` and fight the page's own state.
  const initial = useMemo<GraphUrlState>(() => {
    const result: GraphUrlState = {};

    const mode = searchParams.get('mode');
    if (mode && (VIEW_MODES as readonly string[]).includes(mode)) {
      result.mode = mode as ViewMode;
    }

    const seed = searchParams.get('seed');
    if (seed) result.seed = seed;

    const depthRaw = searchParams.get('depth');
    if (depthRaw !== null) {
      const depth = Number(depthRaw);
      if (Number.isInteger(depth) && depth >= 1 && depth <= 3) {
        result.depth = depth;
      }
    }

    const layers = searchParams.get('layers');
    if (layers && (LAYER_VALUES as readonly string[]).includes(layers)) {
      result.layers = layers as GraphLayers;
    }

    return result;
    // Intentionally empty deps: parse only on mount.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const sync = useCallback(
    (s: GraphSyncState) => {
      const params = new URLSearchParams();

      // Preserve an existing snapshot param — the page reads it directly and we
      // must not drop it on a state write.
      const snapshot = searchParams.get('snapshot');
      if (snapshot) params.set('snapshot', snapshot);

      // Omit defaults: picker mode, depth 1, entities layer, null seed.
      if (s.mode !== 'picker') params.set('mode', s.mode);
      if (s.seed) params.set('seed', s.seed);
      if (s.depth !== 1) params.set('depth', String(s.depth));
      if (s.layers !== 'entities') params.set('layers', s.layers);

      const qs = params.toString();
      const current = searchParams.toString();
      if (qs === current) return; // no-op when the URL already matches

      router.replace(`${pathname}${qs ? `?${qs}` : ''}`, { scroll: false });
    },
    [router, pathname, searchParams],
  );

  return { initial, sync };
}
