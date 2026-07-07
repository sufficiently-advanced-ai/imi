/**
 * Tests for useGraphUrlState — the parse-once / sync-on-change bridge between
 * the graph page's view state and the URL query string.
 *
 * - parses valid mode/seed/depth/layers params
 * - ignores invalid values (omitted from `initial`)
 * - sync omits defaults (picker / depth 1 / entities / null seed)
 * - sync preserves an existing snapshot param
 * - sync no-ops when the URL already matches
 */

import { renderHook } from '@testing-library/react';

const mockReplace = jest.fn();
let currentParams = new URLSearchParams();

jest.mock('next/navigation', () => ({
  useRouter: () => ({ replace: mockReplace }),
  usePathname: () => '/graph',
  useSearchParams: () => currentParams,
}));

import { useGraphUrlState } from '@/lib/hooks/useGraphUrlState';

function setParams(qs: string) {
  currentParams = new URLSearchParams(qs);
}

beforeEach(() => {
  mockReplace.mockClear();
  setParams('');
});

describe('useGraphUrlState — parsing', () => {
  it('parses valid mode/seed/depth/layers', () => {
    setParams('mode=neighborhood&seed=acme&depth=2&layers=decisions');
    const { result } = renderHook(() => useGraphUrlState());
    expect(result.current.initial).toEqual({
      mode: 'neighborhood',
      seed: 'acme',
      depth: 2,
      layers: 'decisions',
    });
  });

  it('ignores invalid values', () => {
    setParams('mode=wormhole&depth=9&layers=bogus');
    const { result } = renderHook(() => useGraphUrlState());
    expect(result.current.initial).toEqual({});
  });

  it('ignores non-integer / out-of-range depth', () => {
    setParams('depth=1.5');
    const { result } = renderHook(() => useGraphUrlState());
    expect(result.current.initial.depth).toBeUndefined();
  });
});

describe('useGraphUrlState — sync', () => {
  it('omits defaults (picker / depth 1 / entities / null seed) → bare path', () => {
    // Start from a non-default URL so syncing to all-defaults produces a diff
    // (and thus a bare-path replace) rather than a no-op.
    setParams('mode=neighborhood&seed=acme');
    const { result } = renderHook(() => useGraphUrlState());
    result.current.sync({ mode: 'picker', seed: null, depth: 1, layers: 'entities' });
    expect(mockReplace).toHaveBeenCalledWith('/graph', { scroll: false });
  });

  it('serializes non-default state', () => {
    const { result } = renderHook(() => useGraphUrlState());
    result.current.sync({ mode: 'neighborhood', seed: 'acme', depth: 2, layers: 'decisions' });
    expect(mockReplace).toHaveBeenCalledWith(
      '/graph?mode=neighborhood&seed=acme&depth=2&layers=decisions',
      { scroll: false },
    );
  });

  it('preserves an existing snapshot param', () => {
    setParams('snapshot=snap-123');
    const { result } = renderHook(() => useGraphUrlState());
    result.current.sync({ mode: 'neighborhood', seed: 'acme', depth: 1, layers: 'entities' });
    expect(mockReplace).toHaveBeenCalledWith(
      '/graph?snapshot=snap-123&mode=neighborhood&seed=acme',
      { scroll: false },
    );
  });

  it('no-ops when the URL already matches', () => {
    setParams('mode=neighborhood&seed=acme');
    const { result } = renderHook(() => useGraphUrlState());
    result.current.sync({ mode: 'neighborhood', seed: 'acme', depth: 1, layers: 'entities' });
    expect(mockReplace).not.toHaveBeenCalled();
  });

  it('no-ops when already bare and state is all-defaults', () => {
    const { result } = renderHook(() => useGraphUrlState());
    result.current.sync({ mode: 'picker', seed: null, depth: 1, layers: 'entities' });
    expect(mockReplace).not.toHaveBeenCalled();
  });
});
