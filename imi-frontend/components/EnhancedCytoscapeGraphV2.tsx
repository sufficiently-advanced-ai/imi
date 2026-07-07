'use client';

import React, { useEffect, useRef, useState } from 'react';
import { useTheme } from 'next-themes';
import cytoscape from 'cytoscape';

interface DomainGraphNode {
  id: string;
  entityType: string;
  attributes: Record<string, any>;
  degree?: number;
  size?: number;
  icon?: string;
  opacity?: number;
  styles?: Record<string, any>;
}

interface DomainGraphEdge {
  id: string;
  source: string;
  target: string;
  relationshipType?: string;
  relationship_type?: string;
  style?: string;
  thickness?: number;
  label?: string;
  labelStyle?: Record<string, any>;
  strength?: number;
}

interface DomainConfig {
  entities: Record<string, any>;
  relationships: Record<string, any>;
}

interface DomainDisplayConfig {
  colors?: Record<string, string>;
  shapes?: Record<string, string>;
  icons?: Record<string, string>;
  [key: string]: any;
}

interface EnhancedCytoscapeGraphProps {
  domainConfig: DomainConfig | null;
  displayConfig: DomainDisplayConfig;
  nodes: DomainGraphNode[];
  edges: DomainGraphEdge[];
  entityTypeFilter: string[];
  relationshipTypeFilter: string[];
  onNodeClick?: (node: DomainGraphNode) => void;
  onEdgeClick?: (edge: DomainGraphEdge) => void;
  onNodeHover?: (node: DomainGraphNode | null) => void;
  // Right-click handlers. Position is in viewport (clientX/Y) coords so the
  // consumer can place a context menu directly.
  onNodeContextMenu?: (node: DomainGraphNode, position: { x: number; y: number }) => void;
  onEdgeContextMenu?: (edge: DomainGraphEdge, position: { x: number; y: number }) => void;
  // Fired on every select/unselect. Receives the current full selection.
  onSelectionChange?: (selection: { nodes: DomainGraphNode[]; edges: DomainGraphEdge[] }) => void;
  cyRef?: React.MutableRefObject<cytoscape.Core | null>;
  onInitialized?: () => void;
  // Visual hierarchy: when a seed + mode are provided, render as an ego graph
  // with concentric rings keyed on hop distance from the seed. This makes the
  // graph's structure spatially legible instead of letting force-directed
  // physics squash it into a clump.
  viewMode?: 'picker' | 'neighborhood' | 'full' | 'influence';
  seedId?: string | null;
}

// Force-directed layout for the full-graph view. For neighborhood mode we
// override this with a concentric layout (see buildLayoutConfig below) so
// hop-distance from the seed becomes a spatial property the eye can read.
const COSE_LAYOUT_CONFIG = {
  name: 'cose',
  fit: true,
  padding: 60,
  avoidOverlap: true,
  animate: false,
  nodeRepulsion: function(_unusedNode: any) { return 6500; },
  idealEdgeLength: function(_unusedEdge: any) { return 120; },
  edgeElasticity: function(_unusedEdge: any) { return 120; },
  nestingFactor: 1.2,
  gravity: 0.6,
  numIter: 300,
  initialTemp: 1000,
  coolingFactor: 0.9,
  minTemp: 1.0,
};

// Build a layout config keyed on mode. Neighborhood mode uses force-directed
// (cose) with the seed pinned at origin (pinning is applied in the data sync
// effect via .lock(), not here). This produces organic clusters: nodes that
// share many mutual edges pull together, isolates drift outward. The visual
// result reads as a knowledge graph — irregular shape, genuine structure —
// rather than a sterile wheel of spokes that pure concentric produces.
function buildLayoutConfig(viewMode: string | undefined, hasSeed: boolean): any {
  if (viewMode === 'influence') {
    // Same organic COSE as the full graph, but tuned for the wider node-size
    // range (high-influence nodes are ~2x the low ones). More repulsion and
    // longer ideal edges give the big nodes room; avoidOverlap stops them
    // swallowing their neighbors.
    return {
      ...COSE_LAYOUT_CONFIG,
      avoidOverlap: true,
      nodeRepulsion: function() { return 9000; },
      idealEdgeLength: function() { return 150; },
    };
  }
  if (viewMode === 'neighborhood' && hasSeed) {
    return {
      name: 'cose',
      fit: true,
      padding: 60,
      animate: false,
      // Seed repels neighbors harder than neighbors repel each other — keeps
      // the seed-centric framing even though the layout is organic.
      nodeRepulsion: (node: any) => node.data('isSeed') ? 15000 : 4500,
      idealEdgeLength: (edge: any) => {
        const s = edge.source?.()?.data?.('isSeed');
        const t = edge.target?.()?.data?.('isSeed');
        // Seed spokes a bit longer so neighbors push outward from it.
        return (s || t) ? 160 : 120;
      },
      edgeElasticity: () => 150,
      nestingFactor: 1.2,
      // Low gravity: the pinned seed provides the anchoring. High gravity
      // would squish the whole graph back into a tight ball.
      gravity: 0.15,
      numIter: 400,
      initialTemp: 1200,
      coolingFactor: 0.92,
      minTemp: 1.0,
      randomize: true,               // fresh randomness → real variation per run
    };
  }
  return COSE_LAYOUT_CONFIG;
}

// Editorial palette — muted, uniform chroma (~0.10-0.13 in OKLCH), tinted
// toward the brand purple hue (280°). Unlike the saturated blue/emerald/amber
// combo, these read as a coherent family: same loudness, different identity.
// All hex values hand-checked against oklch() equivalents.
const ENTITY_COLORS: Record<string, string> = {
  person:     '#8478c4',  // ≈ oklch(60% 0.12 280)  — soft violet
  project:    '#5fa6a8',  // ≈ oklch(65% 0.09 195)  — muted teal
  team:       '#b08c4c',  // ≈ oklch(64% 0.11 75)   — warm ochre
  account:    '#a36a58',  // ≈ oklch(55% 0.10 25)   — terracotta
  product:    '#9d6ca2',  // ≈ oklch(57% 0.10 320)  — mauve
  capability: '#7e8a5a',  // ≈ oklch(58% 0.08 115)  — moss
  skill:      '#c5996a',  // ≈ oklch(70% 0.10 65)   — warm sand
  member:     '#8478c4',  // alias for person-like
  focus_area: '#b08c4c',  // alias for topic-like
  cohort:     '#7e8a5a',  // alias for group-like
  signal:     '#d4924a',  // ≈ oklch(70% 0.14 50)   — stays warmest/most distinct
  document:   '#9b9aa8',  // tinted neutral (purple hint)
  default:    '#7d7789',  // ≈ oklch(55% 0.03 280)  — tinted neutral
};

// Edge tones — tinted dark neutrals. Light and dark mode both get a hint of
// the brand hue so the graph lines feel part of the same family as the nodes.
const EDGE_TONES = {
  light: { base: '#5a5366', hover: '#6d28d9', muted: '#c7c4d0' },
  dark:  { base: '#8c8599', hover: '#a78bfa', muted: '#3e3a48' },
};

import { NODE_SIZES, STANCE_COLORS, INFLUENCE_SIZES } from '@/lib/graph-constants';

// Influence-map edge classification. Maps relationship types to a visual
// "kind" so the stylesheet can distinguish the formal org chart from informal
// power lines. Only applied in influence view mode.
const FORMAL_REL_TYPES = new Set(['reports_to', 'manages']);
const INFORMAL_REL_TYPES = new Set(['influences', 'influenced_by']);
// Inverse relationship types — suppressed in influence mode so each pair draws
// a single, correctly-pointed arrow rather than a double-headed duplicate.
const INVERSE_REL_TYPES = new Set(['influenced_by', 'manages']);

// Build Cytoscape stylesheet based on theme.
// Aesthetic: editorial / architectural. Graph as diagram, not physics demo.
// - Typography carries weight (Geist, medium weight, generous letter-spacing)
// - Edges are thick enough to actually define the graph's structure
// - Seed gets a strong, intentional emphasis via border + label weight
// - Colors tinted toward brand hue (purple 280°), uniform chroma across types
function buildStylesheet(isDark: boolean): any[] {
  const edgeTone = isDark ? EDGE_TONES.dark : EDGE_TONES.light;
  const labelColor = isDark ? '#e8e6ef' : '#2a2530';
  const labelShadow = isDark ? 'rgba(0,0,0,0.9)' : 'rgba(255,255,255,0.95)';
  // Accent = brand purple. Used sparingly (the 10% in 60-30-10).
  const accent = isDark ? '#a78bfa' : '#7c3aed';

  return [
    // Base node styles. Cytoscape's stylesheet dialect is stricter than CSS:
    // no letter-spacing, no CSS variables in font-family, no numeric weights.
    // Using literal family names that the browser resolves at canvas render.
    {
      selector: 'node',
      style: {
        'background-color': 'data(color)',
        'shape': 'data(nodeShape)',
        'label': 'data(label)',
        'text-valign': 'bottom',
        'text-halign': 'center',
        'text-margin-y': 6,
        'font-size': '13px',
        'font-weight': 'normal',
        'font-family': 'Geist, -apple-system, BlinkMacSystemFont, sans-serif',
        'color': labelColor,
        'text-outline-width': 3,
        'text-outline-color': labelShadow,
        'text-outline-opacity': 1,
        'text-background-opacity': 0,
        'width': 'data(size)',
        'height': 'data(size)',
        'text-max-width': '140px',
        'text-wrap': 'wrap',
        'border-width': 1.5,
        'border-color': isDark ? '#1a1822' : '#ffffff',
        'border-opacity': 1,
        'background-opacity': 1,
        'overlay-padding': 2,
        'transition-property': 'background-color, width, height, border-width, border-color',
        'transition-duration': '0.2s',
        'z-index': 10
      }
    },
    // The seed of a neighborhood view — the structural anchor.
    // Override the type color entirely with the brand accent so there's
    // zero ambiguity about "where am I?" at any zoom level. 2x size and a
    // thick contrasting border make it dominate perceptually; the rest of
    // the graph reads as "neighbors of this thing."
    {
      selector: 'node[?isSeed]',
      style: {
        'width': (ele: any) => (ele.data('size') || 60) * 2,
        'height': (ele: any) => (ele.data('size') || 60) * 2,
        'background-color': accent,
        'border-width': 6,
        'border-color': isDark ? '#1a1822' : '#ffffff',
        'border-opacity': 1,
        'font-size': '17px',
        'font-weight': 'bold',
        'color': labelColor,
        'z-index': 30,
      }
    },
    // Fade nodes at deeper rings slightly — a gentle depth-of-field cue that
    // reinforces "you're looking out from the seed." Not a glow, just a tint.
    {
      selector: 'node[depth = 2]',
      style: {
        'background-opacity': 0.85,
        'border-opacity': 0.7,
      }
    },
    {
      selector: 'node[depth > 2]',
      style: {
        'background-opacity': 0.7,
        'border-opacity': 0.6,
      }
    },
    // Icon support
    {
      selector: 'node[icon]',
      style: {
        'background-image': 'data(icon)',
        'background-fit': 'contain',
        'background-clip': 'node',
        'background-image-opacity': 0.8,
      }
    },
    // Entity-specific styles for better visual distinction
    {
      selector: 'node[entityType="person"]',
      style: {
        'shape': 'ellipse',
      }
    },
    {
      selector: 'node[entityType="project"]',
      style: {
        'shape': 'round-rectangle',
      }
    },
    {
      selector: 'node[entityType="team"]',
      style: {
        'shape': 'round-octagon',
      }
    },
    {
      selector: 'node[entityType="account"]',
      style: {
        'shape': 'round-diamond',
      }
    },
    {
      selector: 'node[entityType="product"]',
      style: {
        'shape': 'round-hexagon',
      }
    },
    {
      selector: 'node[entityType="signal"]',
      style: {
        'shape': 'star',
      }
    },
    // Edges — the under-emphasized axis in the original styling. Here they're
    // the second-loudest element (after seed) because "graph" = nodes + edges,
    // and edges were previously 1px ghosts. Tinted dark neutral, ~2.5px base,
    // arrow scale 1.2 so directionality is actually readable.
    {
      selector: 'edge',
      style: {
        'width': 2.5,
        'line-color': edgeTone.base,
        'line-opacity': 0.85,
        'target-arrow-color': edgeTone.base,
        'target-arrow-shape': 'triangle',
        'arrow-scale': 1.2,
        'curve-style': 'bezier',
        'control-point-step-size': 28,
        'label': '',                // labels on hover/highlight only — keeps the canvas clean
        'font-size': '10px',
        'font-weight': 'normal',
        'font-family': 'Geist, -apple-system, BlinkMacSystemFont, sans-serif',
        'text-rotation': 'autorotate',
        'text-margin-y': -10,
        'color': isDark ? '#b8b4c4' : '#4a4452',
        'text-background-color': isDark ? '#1a1822' : '#f7f6f9',
        'text-background-opacity': 0.9,
        'text-background-padding': '3px',
        'transition-property': 'line-color, width, line-opacity',
        'transition-duration': '0.2s',
        'z-index': 5
      }
    },
    // Signal edges (mentions, assigned_to) get their own treatment — lighter,
    // shorter dashes, so they read as "supplementary" rather than competing
    // with the structural entity→entity relationships.
    {
      selector: 'edge[relationshipType="mentions"], edge[relationshipType="assigned_to"]',
      style: {
        'line-style': 'dashed',
        'line-dash-pattern': [4, 3],
        'line-opacity': 0.6,
        'width': 1.5,
      }
    },
    // Solid edge style
    {
      selector: 'edge[style="solid"]',
      style: {
        'line-style': 'solid',
      }
    },
    // Dashed edge style
    {
      selector: 'edge[style="dashed"]',
      style: {
        'line-style': 'dashed',
        'line-dash-pattern': [6, 3],
      }
    },
    // Dotted edge style
    {
      selector: 'edge[style="dotted"]',
      style: {
        'line-style': 'dotted',
        'line-dash-pattern': [2, 2],
      }
    },
    // Influence-map edges. `edgeKind` is only set in influence view mode, so
    // these rules are inert elsewhere. Placed after the style="*" rules so they
    // win on line-style. Formal = the org chart (reports_to); informal = power
    // lines that don't follow the org chart (influences).
    {
      selector: 'edge[edgeKind="formal"]',
      style: {
        'line-style': 'solid',
        'line-color': edgeTone.base,
        'target-arrow-color': edgeTone.base,
        'line-opacity': 0.9,
        'width': 2.5,
      }
    },
    {
      selector: 'edge[edgeKind="informal"]',
      style: {
        'line-style': 'dashed',
        'line-dash-pattern': [6, 4],
        'line-color': accent,
        'target-arrow-color': accent,
        'line-opacity': 0.95,
        'width': 2.5,
      }
    },
    // Context edges (works_at / involved_in) recede so the power structure leads.
    {
      selector: 'edge[edgeKind="context"]',
      style: {
        'line-opacity': 0.4,
        'width': 1.5,
      }
    },
    // Selected state — the accent color reserved for "thing you're acting on."
    // Avoiding the cyan/blue Cytoscape default; this is the brand purple.
    {
      selector: 'node:selected',
      style: {
        'border-width': 4,
        'border-color': accent,
        'overlay-color': accent,
        'overlay-opacity': 0.12,
        'overlay-padding': 6,
        'width': (ele: any) => (ele.data('size') || 60) * 1.12,
        'height': (ele: any) => (ele.data('size') || 60) * 1.12,
        'z-index': 25
      }
    },
    {
      selector: 'edge:selected',
      style: {
        'line-color': accent,
        'target-arrow-color': accent,
        'line-opacity': 1,
        'width': 3.5,
        'label': 'data(label)',
        'text-opacity': 1,
        'font-size': '11px',
        'z-index': 20
      }
    },
    // Highlighted (hover peers) — accent border, no overlay wash. Keeps the
    // node's own fill visible so type is still identifiable.
    {
      selector: '.highlighted',
      style: {
        'border-width': 3,
        'border-color': accent,
        'border-opacity': 1,
        'z-index': 15
      }
    },
    // Highlighted edges — reveal label, lift width, accent color. Together
    // with .dimmed this is how "explore from here" reads.
    {
      selector: '.highlighted-edge',
      style: {
        'line-color': accent,
        'target-arrow-color': accent,
        'line-opacity': 1,
        'width': 3,
        'label': 'data(label)',
        'text-opacity': 1,
        'font-size': '10px',
        'z-index': 10
      }
    },
    // Dimmed nodes — recede without disappearing; seed stays emphasized.
    {
      selector: '.dimmed',
      style: {
        'opacity': 0.25,
        'z-index': 1
      }
    },
    {
      selector: '.dimmed-edge',
      style: {
        'opacity': 0.12,
        'z-index': 1
      }
    }
  ];
}

export const EnhancedCytoscapeGraphV2: React.FC<EnhancedCytoscapeGraphProps> = ({
  domainConfig: _domainConfig,
  displayConfig,
  nodes,
  edges,
  entityTypeFilter,
  relationshipTypeFilter,
  onNodeClick,
  onEdgeClick,
  onNodeHover,
  onNodeContextMenu,
  onEdgeContextMenu,
  onSelectionChange,
  cyRef: externalCyRef,
  onInitialized,
  viewMode,
  seedId,
}) => {
  const containerRef = useRef<HTMLDivElement>(null);
  const internalCyRef = useRef<cytoscape.Core | null>(null);
  const cyRef = externalCyRef || internalCyRef;
  const [isInitialized, setIsInitialized] = useState(false);
  const { resolvedTheme } = useTheme();
  const isDark = resolvedTheme === 'dark';

  // Keep stable references to callbacks so Cytoscape event handlers
  // always invoke the latest version without needing to re-register.
  const callbacksRef = useRef({
    onNodeClick,
    onEdgeClick,
    onNodeHover,
    onNodeContextMenu,
    onEdgeContextMenu,
    onSelectionChange,
  });
  useEffect(() => {
    callbacksRef.current = {
      onNodeClick,
      onEdgeClick,
      onNodeHover,
      onNodeContextMenu,
      onEdgeContextMenu,
      onSelectionChange,
    };
  });

  // ── Effect 1: Initialize Cytoscape ONCE on mount ──────────────────────
  // The cy instance is created here and destroyed ONLY on unmount.
  // This eliminates the "Cannot read properties of null (reading 'isHeadless')"
  // error that occurred when destroy/recreate cycles left stale DOM handlers
  // referencing a dead renderer.
  useEffect(() => {
    if (!containerRef.current) return;

    // Safety: destroy any leftover instance (e.g. from React StrictMode double-mount)
    if (cyRef.current) {
      cyRef.current.destroy();
      cyRef.current = null;
    }

    const cy = cytoscape({
      container: containerRef.current,
      elements: [], // Data sync effect populates elements
      wheelSensitivity: 0.2,
      boxSelectionEnabled: true,
      autounselectify: false,
      autoungrabify: false,
      minZoom: 0.1,
      maxZoom: 5,
      style: buildStylesheet(isDark),
      layout: { name: 'preset' }, // No-op layout; data sync effect runs the real layout
    });

    cyRef.current = cy;
    setIsInitialized(true);
    onInitialized?.();

    // Add data-testid to the cytoscape container
    const cyContainer = containerRef.current.querySelector('.cytoscape_container');
    if (cyContainer) {
      cyContainer.setAttribute('data-testid', 'cytoscape-internal');
    }

    // ── Event handlers ──
    // Use callbacksRef so handlers always call the latest callback
    // without needing the effect to re-run when props change.

    cy.on('tap', 'node', (evt) => {
      const nodeData = evt.target.data();
      callbacksRef.current.onNodeClick?.({
        id: nodeData.id,
        entityType: nodeData.entityType,
        attributes: nodeData.attributes || {},
        degree: nodeData.degree,
        size: nodeData.size,
        icon: nodeData.icon,
        opacity: nodeData.opacity,
        styles: nodeData.styles,
      });
    });

    cy.on('tap', 'edge', (evt) => {
      const edgeData = evt.target.data();
      callbacksRef.current.onEdgeClick?.({
        id: edgeData.id,
        source: edgeData.source,
        target: edgeData.target,
        relationshipType: edgeData.relationshipType || edgeData.relationship_type,
        style: edgeData.style,
        thickness: edgeData.thickness,
        label: edgeData.label,
        labelStyle: edgeData.labelStyle,
        strength: edgeData.strength,
      });
    });

    cy.on('mouseover', 'node', (evt) => {
      const node = evt.target;
      const nodeData = node.data();

      // Highlight connected nodes and edges
      const connectedEdges = node.connectedEdges();
      const connectedNodes = connectedEdges.connectedNodes();
      connectedNodes.addClass('highlighted');
      connectedEdges.addClass('highlighted-edge');

      // Dim everything else
      cy.nodes().not(connectedNodes).not(node).addClass('dimmed');
      cy.edges().not(connectedEdges).addClass('dimmed-edge');

      callbacksRef.current.onNodeHover?.({
        id: nodeData.id,
        entityType: nodeData.entityType,
        attributes: nodeData.attributes || {},
        degree: nodeData.degree,
      });
    });

    cy.on('mouseout', 'node', () => {
      cy.nodes().removeClass('highlighted dimmed');
      cy.edges().removeClass('highlighted-edge dimmed-edge');
      callbacksRef.current.onNodeHover?.(null);
    });

    // Right-click (context menu) on node. Cytoscape's cxttap fires on both
    // mouse right-click and long-press on touch. We translate the raw DOM
    // event into viewport coords so the consumer can position a menu.
    cy.on('cxttap', 'node', (evt) => {
      const origEvt = evt.originalEvent as MouseEvent | TouchEvent | undefined;
      let x = 0;
      let y = 0;
      if (origEvt && 'clientX' in origEvt) {
        x = origEvt.clientX;
        y = origEvt.clientY;
      } else if (origEvt && 'touches' in origEvt && origEvt.touches.length > 0) {
        x = origEvt.touches[0].clientX;
        y = origEvt.touches[0].clientY;
      }
      const nodeData = evt.target.data();
      callbacksRef.current.onNodeContextMenu?.(
        {
          id: nodeData.id,
          entityType: nodeData.entityType,
          attributes: nodeData.attributes || {},
          degree: nodeData.degree,
          size: nodeData.size,
          icon: nodeData.icon,
          opacity: nodeData.opacity,
          styles: nodeData.styles,
        },
        { x, y },
      );
    });

    cy.on('cxttap', 'edge', (evt) => {
      const origEvt = evt.originalEvent as MouseEvent | TouchEvent | undefined;
      let x = 0;
      let y = 0;
      if (origEvt && 'clientX' in origEvt) {
        x = origEvt.clientX;
        y = origEvt.clientY;
      } else if (origEvt && 'touches' in origEvt && origEvt.touches.length > 0) {
        x = origEvt.touches[0].clientX;
        y = origEvt.touches[0].clientY;
      }
      const edgeData = evt.target.data();
      callbacksRef.current.onEdgeContextMenu?.(
        {
          id: edgeData.id,
          source: edgeData.source,
          target: edgeData.target,
          relationshipType:
            edgeData.relationshipType || edgeData.relationship_type,
          style: edgeData.style,
          thickness: edgeData.thickness,
          label: edgeData.label,
          labelStyle: edgeData.labelStyle,
          strength: edgeData.strength,
        },
        { x, y },
      );
    });

    // Multi-select: shift-click and box-select both route through these.
    // Emit the full current selection so the consumer doesn't have to track
    // deltas. boxSelectionEnabled is already true in the cytoscape init.
    const emitSelection = () => {
      const selectedNodes = cy.nodes(':selected').map((n) => {
        const d = n.data();
        return {
          id: d.id,
          entityType: d.entityType,
          attributes: d.attributes || {},
          degree: d.degree,
          size: d.size,
          icon: d.icon,
          opacity: d.opacity,
          styles: d.styles,
        };
      });
      const selectedEdges = cy.edges(':selected').map((e) => {
        const d = e.data();
        return {
          id: d.id,
          source: d.source,
          target: d.target,
          relationshipType: d.relationshipType || d.relationship_type,
          style: d.style,
          thickness: d.thickness,
          label: d.label,
          labelStyle: d.labelStyle,
          strength: d.strength,
        };
      });
      callbacksRef.current.onSelectionChange?.({
        nodes: selectedNodes,
        edges: selectedEdges,
      });
    };
    cy.on('select unselect', 'node, edge', emitSelection);

    console.log('Cytoscape instance created (mount-only lifecycle)');

    return () => {
      console.log('Cytoscape instance destroyed (unmount)');
      cy.destroy();
      cyRef.current = null;
      setIsInitialized(false);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []); // Mount/unmount only — data and theme are synced by separate effects

  // ── Effect 2: Sync elements when data changes ─────────────────────────
  // Instead of destroying and recreating the cy instance (which causes the
  // isHeadless crash), we batch-update elements in the existing instance.
  useEffect(() => {
    if (!cyRef.current) return;
    const cy = cyRef.current;

    const isInfluence = viewMode === 'influence';

    // Build Cytoscape element definitions from props
    const elements: any[] = [];

    nodes.forEach(node => {
      const entityType = node.entityType || (node as any).entity_type;
      const entityTypeLower = entityType?.toLowerCase() || 'default';
      // Influence mode re-encodes stakeholder nodes: stance → fill color,
      // influence → size. Falls back to the entity-type encoding when the
      // attributes are absent (non-stakeholder nodes, or unscored stakeholders).
      const stance = isInfluence ? node.attributes.stance : undefined;
      const influence = isInfluence ? node.attributes.influence : undefined;
      const color = (stance && STANCE_COLORS[stance]) ||
                   node.styles?.['background-color'] ||
                   displayConfig.colors?.[entityType] ||
                   ENTITY_COLORS[entityTypeLower] ||
                   ENTITY_COLORS.default;
      const shape = node.styles?.shape ||
                   displayConfig.shapes?.[entityType] || 'ellipse';
      const label = node.attributes.name || node.attributes.canonical_name || node.id;
      // Always use local NODE_SIZES — backend sends degree-based sizes (70-90)
      // which are far too large for readable graph layout
      const size = (influence && INFLUENCE_SIZES[influence]) ||
                   NODE_SIZES[entityTypeLower] || NODE_SIZES.default;
      const opacity = node.opacity || 1;
      const icon = node.icon || displayConfig.icons?.[entityType];

      elements.push({
        group: 'nodes',
        data: {
          id: node.id,
          label,
          entityType,
          attributes: node.attributes,
          degree: node.degree,
          size,
          opacity,
          icon,
          styles: node.styles,
          color,
          nodeShape: shape,
        }
      });
    });

    edges.forEach(edge => {
      const relType = edge.relationshipType || edge.relationship_type || 'related';
      // Influence mode: drop inverse edges (influenced_by/manages) so each
      // power/reporting pair draws a single, correctly-pointed arrow rather
      // than a double-headed duplicate.
      if (isInfluence && INVERSE_REL_TYPES.has(relType)) return;
      const style = edge.style || 'solid';
      const thickness = edge.thickness || 2;
      const normalizedLabel = relType
        .replace(/_/g, ' ')
        .replace(/-/g, ' ')
        .toLowerCase()
        .replace(/\b\w/g, (l: string) => l.toUpperCase());
      const label = edge.label || normalizedLabel;

      // Classify edges for the influence-map stylesheet. Only set in influence
      // mode so the formal/informal/context edge rules stay inert elsewhere.
      let edgeKind: string | undefined;
      if (isInfluence) {
        if (FORMAL_REL_TYPES.has(relType)) edgeKind = 'formal';
        else if (INFORMAL_REL_TYPES.has(relType)) edgeKind = 'informal';
        else edgeKind = 'context';
      }

      elements.push({
        group: 'edges',
        data: {
          id: edge.id,
          source: edge.source,
          target: edge.target,
          label,
          relationshipType: relType,
          style,
          thickness,
          edgeKind,
          labelStyle: edge.labelStyle,
          strength: edge.strength,
        }
      });
    });

    // Batch update prevents intermediate render states where handlers
    // could fire against partially-populated element collections.
    cy.batch(() => {
      cy.elements().remove();
      cy.add(elements);
    });

    // Neighborhood mode: compute hop distance from seed via BFS (used by the
    // depth-opacity style rules) AND pin the seed at origin so the organic
    // force-directed layout anchors to a consistent visual center. Pinning
    // (.lock()) is respected by Cytoscape's layout engine — the seed stays
    // at (0, 0) while neighbors settle around it based on mutual connectivity.
    if (viewMode === 'neighborhood' && seedId) {
      const seedNode = cy.getElementById(seedId);
      if (seedNode.length > 0) {
        seedNode.data('isSeed', true);
        seedNode.position({ x: 0, y: 0 });
        seedNode.lock();
        cy.elements().bfs({
          roots: seedNode,
          visit: (node: any, _edge: any, _prevNode: any, _idx: number, depth: number) => {
            node.data('depth', depth);
          },
          directed: false,
        });
        cy.nodes().forEach((n: any) => {
          if (n.data('depth') === undefined) n.data('depth', 99);
        });
      }
    }

    // Run the mode-appropriate layout.
    if (elements.length > 0) {
      const layoutConfig = buildLayoutConfig(viewMode, !!(viewMode === 'neighborhood' && seedId));
      cy.layout(layoutConfig).run();
    }

    console.log('Cytoscape data synced:', {
      nodeCount: cy.nodes().length,
      edgeCount: cy.edges().length,
      viewMode,
      seedId,
    });
  }, [nodes, edges, displayConfig, viewMode, seedId]);

  // ── Effect 3: Update stylesheet when theme changes ────────────────────
  // Only the theme-dependent style properties are refreshed — no destroy needed.
  useEffect(() => {
    if (!cyRef.current) return;
    try {
      cyRef.current.style().fromJson(buildStylesheet(isDark)).update();
    } catch (err) {
      console.warn('Failed to update Cytoscape theme styles:', err);
    }
  }, [isDark]);

  // ── Effect 4: Apply visibility filters ────────────────────────────────
  useEffect(() => {
    if (!cyRef.current || !isInitialized) return;
    const cy = cyRef.current;

    // Apply filters
    const filteredNodes = entityTypeFilter.length > 0
      ? nodes.filter(node => {
          const type = node.entityType || (node as any).entity_type;
          return entityTypeFilter.includes(type);
        })
      : nodes;

    const nodeIds = new Set(filteredNodes.map(n => n.id));

    // Show/hide nodes based on filter
    cy.nodes().forEach(node => {
      node.style('display', nodeIds.has(node.id()) ? 'element' : 'none');
    });

    // Show/hide edges based on connected nodes and relationship filter
    cy.edges().forEach(edge => {
      const sourceVisible = nodeIds.has(edge.source().id());
      const targetVisible = nodeIds.has(edge.target().id());
      const relType = edge.data('relationshipType');
      const matchesRelFilter = relationshipTypeFilter.length === 0 ||
        (relType && relationshipTypeFilter.includes(relType));

      edge.style(
        'display',
        sourceVisible && targetVisible && matchesRelFilter ? 'element' : 'none'
      );
    });

    console.log('Filters applied:', {
      entityTypeFilter,
      relationshipTypeFilter,
      visibleNodes: cy.nodes(':visible').length,
      visibleEdges: cy.edges(':visible').length
    });

  }, [entityTypeFilter, relationshipTypeFilter, nodes, isInitialized]);

  // Dot-grid canvas — hints "plotting surface," not blank card. Tiny, tinted
  // purple dots at low opacity. Under 1% chroma keeps them from reading as
  // a pattern; they just quietly register as graph paper.
  const canvasBackground = isDark
    ? 'radial-gradient(circle, rgba(167, 139, 250, 0.08) 1px, transparent 1px)'
    : 'radial-gradient(circle, rgba(124, 58, 237, 0.10) 1px, transparent 1px)';
  const canvasSurface = isDark ? '#1a1822' : '#f7f6f9';

  return (
    <div
      ref={containerRef}
      style={{
        width: '100%',
        height: '100%',
        position: 'relative',
        backgroundColor: canvasSurface,
        backgroundImage: canvasBackground,
        backgroundSize: '24px 24px',
        backgroundPosition: '0 0',
      }}
      data-testid="cytoscape-graph"
      className="cytoscape-container"
    />
  );
};
