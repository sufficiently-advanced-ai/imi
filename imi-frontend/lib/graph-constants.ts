/** Shared node size map for Cytoscape graph rendering.
 *  Used by both the SSE animation path and the steady-state renderer
 *  so nodes don't visually jump when the stream completes. */
export const NODE_SIZES: Record<string, number> = {
  // example-client domain entity types
  member: 60,
  focus_area: 65,
  cohort: 75,
  document: 14,
  // Generic entity types
  person: 60,
  project: 70,
  team: 65,
  account: 75,
  product: 65,
  capability: 60,
  skill: 14,
  signal: 40,
  default: 55,
};

/** ── Influence-map encodings ────────────────────────────────────────────
 *  Used only by the dedicated "Influence Map" view mode of the domain graph.
 *  Stakeholder nodes are re-encoded so sentiment (stance) and power
 *  (influence) read at a glance instead of every stakeholder looking alike.
 *  See docs/superpowers/specs/2026-06-04-practice-kb-influence-mapping-design.md
 */

/** Stance → fill color. Diverging champion→blocker palette, kept in the muted
 *  editorial chroma of ENTITY_COLORS so it reads as the same visual family.
 *  Hand-checked toward oklch(~0.55-0.65 L, ~0.09-0.13 C). */
export const STANCE_COLORS: Record<string, string> = {
  champion:  '#4f9d69', // moss green — strongest advocate
  supporter: '#5fa6a8', // muted teal
  neutral:   '#9b9aa8', // tinted gray
  skeptic:   '#c5996a', // warm sand-amber
  blocker:   '#bf5a4c', // terracotta-red — actively opposed
};

/** Influence → node diameter. High-influence players dominate the canvas;
 *  stakeholders otherwise fall to NODE_SIZES.default (55) and look uniform. */
export const INFLUENCE_SIZES: Record<string, number> = {
  high:   82,
  medium: 56,
  low:    40,
};

/** Display order + labels for the influence-mode legend (champion → blocker). */
export const STANCE_ORDER: readonly string[] = [
  'champion',
  'supporter',
  'neutral',
  'skeptic',
  'blocker',
];

export const STANCE_LABELS: Record<string, string> = {
  champion:  'Champion',
  supporter: 'Supporter',
  neutral:   'Neutral',
  skeptic:   'Skeptic',
  blocker:   'Blocker',
};
