import type { DomainGraphNode } from '@/lib/api/domain';

/**
 * Graph view mode. Inlined across the graph page and its floating widgets up to
 * C2; centralized here so the URL-state hook and the toolbar share one source.
 */
export type ViewMode = 'picker' | 'neighborhood' | 'full' | 'influence';

/**
 * Layer selection — replaces the old `includeSignals` boolean with a 3-state
 * progression. `entities` shows entity nodes only (the default; the fetch
 * already excludes signals). `decisions` adds decision signals on top.
 * `signals` shows every signal type (decisions, action items, key points…).
 *
 * The fetch maps `layers !== 'entities'` to the backend `include_signals` flag;
 * the `decisions` vs `signals` distinction is then refined client-side by
 * `nodePassesLayer` in the page's filteredNodes memo.
 */
export type GraphLayers = 'entities' | 'decisions' | 'signals';

/**
 * Pure, unit-testable layer predicate applied per node in the page's
 * filteredNodes memo. Entity nodes (no `signal_type`) always pass; signal nodes
 * pass according to the active layer.
 */
export function nodePassesLayer(node: DomainGraphNode, layers: GraphLayers): boolean {
  const st = node.attributes?.signal_type;
  if (!st) return true; // entity nodes always pass
  if (layers === 'signals') return true;
  if (layers === 'decisions') return st === 'decision';
  return false; // 'entities' (defensive; fetch already excludes signals)
}
