'use client';

import React, { useEffect, useState, useCallback, useRef, useMemo } from 'react';
import { useSearchParams } from 'next/navigation';
import type { Core } from 'cytoscape';
import { useDomain } from '@/contexts/DomainContext';
import { EnhancedCytoscapeGraphV2 } from '@/components/EnhancedCytoscapeGraphV2';
import { NodeProfileModal } from '@/components/NodeProfileModal';
import { GraphControls } from '@/components/GraphControls';
import { GraphMinimap } from '@/components/GraphMinimap';
import { InfluenceLegend } from '@/components/graph/InfluenceLegend';
import ErrorBoundary from '@/components/ErrorBoundary';
import { ContextMenu, type ContextMenuItem } from '@/components/graph/ContextMenu';
import { NodeEditDialog } from '@/components/graph/NodeEditDialog';
import { MergeNodesDialog } from '@/components/graph/MergeNodesDialog';
import { RelationshipEditDialog } from '@/components/graph/RelationshipEditDialog';
import { EditProfileDialog } from '@/components/graph/EditProfileDialog';
import { BulkActionBar } from '@/components/graph/BulkActionBar';
import { GraphToolbar } from '@/components/graph/GraphToolbar';
import { GraphInspector } from '@/components/graph/GraphInspector';
import { SeedPickerOverlay } from '@/components/graph/SeedPickerOverlay';
import { GraphStatsChip } from '@/components/graph/GraphStatsChip';
import { deleteNode, removeRelationship } from '@/lib/api/graph-mutations';
import { useToast } from '@/components/ui/use-toast';
import {
  fetchDomainGraphData,
  fetchDomainDisplayConfig,
  fetchNeighborhood,
  fetchTopEntities,
  DomainGraphData,
  DomainDisplayConfig,
  DomainGraphNode,
  DomainGraphEdge,
  TopEntity,
} from '@/lib/api/domain';
import { Button } from '@/components/ui/button';
import { Card, CardContent } from '@/components/ui/card';
import {
  Sheet,
  SheetContent,
  SheetHeader,
  SheetTitle,
} from '@/components/ui/sheet';
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from '@/components/ui/alert-dialog';
import { RefreshCw } from 'lucide-react';
import { PageHeader } from '@/components/ui/page-header';
import { PageContainer } from '@/components/ui/page-container';
import { useMediaQuery } from '@/lib/hooks';
import { nodePassesLayer, type GraphLayers, type ViewMode } from '@/components/graph/layers';
import { useGraphUrlState } from '@/lib/hooks/useGraphUrlState';

// Render limits. The neighborhood limit is the sweet spot for a force-directed
// layout where each node stays readable; the full-graph limit keeps the cold
// load bounded; the picker limit keeps the seed list scannable in one glance.
const NEIGHBORHOOD_NODE_LIMIT = 75;
const FULL_GRAPH_NODE_LIMIT = 500;
const TOP_ENTITIES_LIMIT = 24;

// Shared overlay treatment so floating widgets read as one system rather than
// four independent components stuck on top of the canvas.
const OVERLAY_SURFACE = 'border bg-background/95 shadow-lg backdrop-blur-sm';

function EnhancedDomainGraphContent() {
  const { currentDomain, domainConfig, isLoading: isDomainLoading, error: domainError, uiLabels } = useDomain();
  const searchParams = useSearchParams();
  const snapshot = searchParams.get('snapshot');

  // Shareable URL state (mode/seed/depth/layers). `initial` is parsed once on
  // mount and seeds the view below; `sync` writes back on every state change.
  const { initial: initialUrlState, sync: syncUrlState } = useGraphUrlState();

  const [graphData, setGraphData] = useState<DomainGraphData | null>(null);
  const [displayConfig, setDisplayConfig] = useState<DomainDisplayConfig>({});
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Filters
  const [searchQuery, setSearchQuery] = useState('');
  const [entityTypeFilter, setEntityTypeFilter] = useState<string[]>([]);
  const [relationshipTypeFilter, setRelationshipTypeFilter] = useState<string[]>([]);
  const [hiddenNodeIds, setHiddenNodeIds] = useState<Set<string>>(new Set());
  // Layer selection — replaces the old includeSignals boolean. 'entities' (the
  // default) keeps the canvas navigable; '+ Decisions' and 'All signals'
  // progressively reveal signal nodes once meeting ingestion is running. The
  // fetch maps layers!=='entities' to include_signals; the decisions/signals
  // split is refined client-side in filteredNodes via nodePassesLayer.
  const [layers, setLayers] = useState<GraphLayers>(initialUrlState.layers ?? 'entities');

  // Context-graph mode. Default to 'picker' so the page loads instantly —
  // the user picks a seed from top entities or search instead of waiting
  // for a full-graph build. A shareable URL can preseed mode/seed (applied
  // on mount below).
  const [viewMode, setViewMode] = useState<ViewMode>(
    initialUrlState.seed &&
      (initialUrlState.mode === 'neighborhood' || initialUrlState.mode === 'influence')
      ? initialUrlState.mode
      : initialUrlState.mode === 'full'
        ? 'full'
        : 'picker',
  );
  const [seedId, setSeedId] = useState<string | null>(
    initialUrlState.seed &&
      (initialUrlState.mode === 'neighborhood' || initialUrlState.mode === 'influence')
      ? initialUrlState.seed
      : null,
  );
  // Default depth 1: a 1-hop neighborhood is almost always legible as a
  // recognizable node/edge graph. Depth 2 for high-degree seeds (500+ direct
  // neighbors) produces a webbed clump — users can opt into it via the slider.
  const [depth, setDepth] = useState<number>(initialUrlState.depth ?? 1);
  const [topEntities, setTopEntities] = useState<TopEntity[]>([]);

  // Selected node/edge
  const [selectedNode, setSelectedNode] = useState<DomainGraphNode | null>(null);
  const [selectedEdge, setSelectedEdge] = useState<DomainGraphEdge | null>(null);
  const [hoveredNode, setHoveredNode] = useState<DomainGraphNode | null>(null);

  // Modal state
  const [isNodeModalOpen, setIsNodeModalOpen] = useState(false);

  // Graph controls
  const [currentLayout, setCurrentLayout] = useState('force-directed');
  const [viewport, setViewport] = useState({ x: 0, y: 0, zoom: 1 });
  const [isInitialized, setIsInitialized] = useState(false);
  const [nodePositions, setNodePositions] = useState<Array<{ id: string; x: number; y: number; entityType: string }>>([]);

  // Overlays-hidden state. Replaces the old focus mode: instead of a separate
  // full-screen render tree, the canvas is always full-bleed and `F` simply
  // unmounts the floating chrome (toolbar/inspector/stats/picker/minimap/
  // legend) for a clean look at the graph.
  const [overlaysHidden, setOverlaysHidden] = useState(false);

  // Desktop vs. mobile inspector treatment. ≥1024px → right-side overlay that
  // never resizes the canvas; below → a Sheet. SSR-safe (false until mounted).
  const isDesktop = useMediaQuery('(min-width: 1024px)');

  // Editor state (Issue #877). contextMenu holds the open-menu target +
  // screen position; editingNode drives the NodeEditDialog; selectedNodes
  // tracks the current multi-selection so the BulkActionBar can act on it.
  const [contextMenu, setContextMenu] = useState<{
    kind: 'node';
    node: DomainGraphNode;
    position: { x: number; y: number };
  } | {
    kind: 'edge';
    edge: DomainGraphEdge;
    position: { x: number; y: number };
  } | null>(null);
  const [editingNode, setEditingNode] = useState<DomainGraphNode | null>(null);
  const [selectedNodes, setSelectedNodes] = useState<DomainGraphNode[]>([]);
  // Curation overlays: merge "pick the other node" mode + the resolved pair,
  // the relationship add/redirect target, and the profile being edited.
  const [mergePickSource, setMergePickSource] = useState<DomainGraphNode | null>(null);
  const [mergePair, setMergePair] = useState<{ a: DomainGraphNode; b: DomainGraphNode } | null>(null);
  const [relEdit, setRelEdit] = useState<{
    source: DomainGraphNode;
    existingEdge: { relationshipType: string; target: string } | null;
  } | null>(null);
  const [editingProfileNode, setEditingProfileNode] = useState<DomainGraphNode | null>(null);
  // Pending delete drives the destructive AlertDialog. Both node and edge
  // deletions funnel through the same confirmation surface so the design
  // system stays consistent (no native window.confirm).
  const [pendingDelete, setPendingDelete] = useState<
    | { kind: 'node'; nodeId: string; label: string }
    | { kind: 'edge'; edge: DomainGraphEdge }
    | null
  >(null);
  const [isDeleting, setIsDeleting] = useState(false);
  const { toast } = useToast();

  // Refs for Cytoscape interaction
  const cyRef = useRef<Core | null>(null);
  // The full-bleed canvas wrapper. The ResizeObserver below watches it so the
  // renderer re-fits whenever the shell area changes size (sidebar collapse,
  // window resize) — the renderer itself does no resize handling.
  const canvasWrapperRef = useRef<HTMLDivElement | null>(null);

  // Overlays keyboard handler (repurposed from the old focus-mode handler):
  // `F` toggles the floating chrome, `Escape` reveals it when hidden.
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      // Don't trigger if user is typing in an input
      if (
        e.target instanceof HTMLInputElement ||
        e.target instanceof HTMLTextAreaElement ||
        e.target instanceof HTMLSelectElement
      ) {
        return;
      }

      // Toggle the floating overlays with 'F'
      if (e.key === 'f' || e.key === 'F') {
        e.preventDefault();
        setOverlaysHidden(prev => !prev);
      }

      // Escape reveals the overlays again when they're hidden
      if (e.key === 'Escape' && overlaysHidden) {
        e.preventDefault();
        setOverlaysHidden(false);
      }
    };

    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [overlaysHidden]);

  // Toggle overlays handler for the button
  const handleToggleOverlays = useCallback(() => {
    setOverlaysHidden(prev => !prev);
  }, []);

  // ResizeObserver — the canvas is full-bleed, so it must re-fit whenever its
  // wrapper changes size (the renderer does no resize handling internally).
  useEffect(() => {
    const el = canvasWrapperRef.current;
    if (!el) return;
    const ro = new ResizeObserver(() => cyRef.current?.resize());
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  // Pan-nudge — when a node is selected and the desktop inspector overlay is
  // open (right ~340px band + gutters), the freshly-selected node can sit under
  // the panel. If its rendered x falls in that right band, pan left so it stays
  // visible beside the inspector. Guarded on cy + the node existing in cy.
  useEffect(() => {
    // Only nudge for the desktop overlay (the mobile Sheet covers the canvas
    // anyway) and only when overlays are visible.
    if (!isDesktop || overlaysHidden || !selectedNode) return;
    const cy = cyRef.current;
    if (!cy) return;
    const node = cy.getElementById(selectedNode.id);
    if (!node || node.length === 0) return;
    const INSPECTOR_BAND = 364; // 340px panel + 24px of right gutter
    const renderedX = node.renderedPosition().x;
    const overlapsBand = renderedX > cy.width() - INSPECTOR_BAND;
    if (!overlapsBand) return;
    const shift = renderedX - (cy.width() - INSPECTOR_BAND) + 40;
    cy.animate({ panBy: { x: -shift, y: 0 }, duration: 250 });
  }, [selectedNode, isDesktop, overlaysHidden]);

  // Load graph data when domain, snapshot, or entity/relationship filters change.
  // Also re-runs when the view mode or seed changes — a seed switch triggers a
  // neighborhood fetch, a mode switch to 'full' triggers a full-graph fetch.
  useEffect(() => {
    if (viewMode === 'picker') {
      // Picker mode: no canvas data — user hasn't chosen a seed yet
      return;
    }
    if (currentDomain || snapshot) {
      loadGraphData();
    }
  }, [currentDomain, snapshot, entityTypeFilter, relationshipTypeFilter, layers, viewMode, seedId, depth]);

  // Load top entities once per domain so the seed picker is populated instantly.
  // This is a cheap Cypher degree query that does not trigger build_graph().
  //
  // The endpoint is domain-aware, so we must pass `currentDomain`. Without
  // it the picker can suggest seeds from a different graph than the one
  // /neighborhood will expand, and clicking a suggestion 404s.
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const entities = await fetchTopEntities({
          limit: TOP_ENTITIES_LIMIT,
          domain: currentDomain || undefined,
        });
        if (!cancelled) setTopEntities(entities);
      } catch (err) {
        console.warn('Failed to load top entities for seed picker:', err);
      }
    })();
    return () => { cancelled = true; };
  }, [currentDomain]);

  // Keep the URL in sync with the shareable view state (mode/seed/depth/layers)
  // so a neighborhood/influence view can be copied and reopened. Defaults are
  // omitted and an existing ?snapshot is preserved (handled inside the hook).
  useEffect(() => {
    syncUrlState({ mode: viewMode, seed: seedId, depth, layers });
  }, [viewMode, seedId, depth, layers, syncUrlState]);

  // Right-click used to auto-re-root the neighborhood view. It now opens a
  // context menu (see ContextMenu state + onNodeContextMenu below) where
  // "Re-center here" is one of several actions. The Cytoscape wrapper emits
  // the cxttap event via its onNodeContextMenu/onEdgeContextMenu callbacks.

  // Sync viewport state with Cytoscape's viewport
  useEffect(() => {
    if (!cyRef.current || !isInitialized) return;

    const cy = cyRef.current;

    // Function to update viewport state from Cytoscape
    const updateViewport = () => {
      const pan = cy.pan();
      const zoom = cy.zoom();
      setViewport({
        x: pan.x,
        y: pan.y,
        zoom: zoom
      });
    };

    // Function to update node positions for minimap
    const updateNodePositions = () => {
      const positions = cy.nodes().map((node: any) => ({
        id: node.id(),
        x: node.position().x,
        y: node.position().y,
        entityType: node.data('entityType')
      }));
      setNodePositions(positions);
    };

    // Subscribe to Cytoscape viewport events
    cy.on('pan', updateViewport);
    cy.on('zoom', updateViewport);
    cy.on('viewport', updateViewport);

    // Subscribe to layout events to update node positions
    cy.on('layoutstop', updateNodePositions);
    cy.on('position', updateNodePositions);

    // Initial sync
    updateViewport();
    updateNodePositions();

    // Cleanup event listeners
    return () => {
      cy.off('pan', updateViewport);
      cy.off('zoom', updateViewport);
      cy.off('viewport', updateViewport);
      cy.off('layoutstop', updateNodePositions);
      cy.off('position', updateNodePositions);
    };
  }, [isInitialized]); // Re-run when graph is initialized

  const loadGraphData = useCallback(async () => {
    // Allow loading with snapshot even without domain (for demo mode)
    if (!currentDomain && !snapshot) return;

    try {
      setIsLoading(true);
      setError(null);

      // Dispatch the data fetch based on view mode. Neighborhood mode hits a
      // bounded Cypher query that returns in hundreds of ms; full mode hits
      // the cached full-graph endpoint which may cold-start slowly. Influence
      // mode is a client-seeded neighborhood: depth 1 captures the client's
      // stakeholders (all 1 hop via works_at) and the endpoint returns the
      // stakeholder↔stakeholder reports_to/influences edges among them.
      const dataPromise = (viewMode === 'neighborhood' || viewMode === 'influence') && seedId
        ? fetchNeighborhood({
            seed: seedId,
            depth: viewMode === 'influence' ? 1 : depth,
            includeSignals: layers !== 'entities',
            limit: NEIGHBORHOOD_NODE_LIMIT,
            domain: currentDomain || undefined,
          })
        : fetchDomainGraphData({
            domain: currentDomain || 'demo',
            entityTypes: entityTypeFilter.length > 0 ? entityTypeFilter : undefined,
            relationshipTypes: relationshipTypeFilter.length > 0 ? relationshipTypeFilter : undefined,
            snapshot: snapshot || undefined,
            includeSignals: layers !== 'entities',
          });

      // Fetch display config and graph data in parallel
      const [displayConfResult, graphDataResult] = await Promise.allSettled([
        fetchDomainDisplayConfig(currentDomain || undefined),
        dataPromise,
      ]);

      // Handle display config errors
      let displayConf: DomainDisplayConfig = { colors: {}, shapes: {} };
      if (displayConfResult.status === 'rejected') {
        console.error('Failed to load display config:', displayConfResult.reason);
      } else {
        displayConf = displayConfResult.value;
      }

      // Handle graph data errors
      if (graphDataResult.status === 'rejected') {
        throw new Error(`Failed to load graph data: ${graphDataResult.reason}`);
      }

      // Transform the data to match the component's expected format
      const graphData = graphDataResult.value;

      // Decision signals get a synthetic 'decision' entity type so they read as
      // a distinct, branded node class without touching the renderer. A node is
      // a decision when its attributes carry signal_type === 'decision'.
      // Raw API nodes carry either `entity_type` (snake) or `entityType`.
      type RawNode = {
        entity_type?: string;
        entityType?: string;
        attributes?: { signal_type?: string; signalType?: string } & Record<string, unknown>;
      };
      const isDecisionNode = (n: RawNode): boolean =>
        (n.attributes?.signal_type ?? n.attributes?.signalType) === 'decision';

      // Ensure display config covers all entity types in the graph data
      // (after the decision remap, so 'decision' is in the type set).
      const nodeTypes = new Set(
        (graphData.nodes as RawNode[])
          .map((n) => (isDecisionNode(n) ? 'decision' : n.entity_type || n.entityType))
          .filter(Boolean) as string[]
      );
      const palette = ['#4ecdc4', '#45b7d1', '#96ceb4', '#feca57', '#ff6b6b', '#a29bfe', '#fd79a8', '#6c5ce7'];
      const shapeList = ['ellipse', 'diamond', 'star', 'round-rectangle', 'hexagon'];
      let idx = 0;
      const mergedColors = { ...displayConf.colors };
      const mergedShapes = { ...displayConf.shapes };
      // Seed the synthetic decision encoding first so it always wins over the
      // generated palette: brand-purple diamond. Hex only — Cytoscape's own
      // color parser rejects space-separated hsl() (verified: it warns
      // "style property background-color: hsl(262 60% 55%) is invalid" and
      // drops the color, leaving gray nodes).
      if (nodeTypes.has('decision')) {
        mergedColors['decision'] = '#7c4dcc'; // ≈ hsl(262, 60%, 55%), brand purple
        mergedShapes['decision'] = 'diamond';
      }
      nodeTypes.forEach((t: string) => {
        const needsColor = !mergedColors[t];
        const needsShape = !mergedShapes[t];
        if (needsColor) mergedColors[t] = palette[idx % palette.length];
        if (needsShape) mergedShapes[t] = shapeList[idx % shapeList.length];
        if (needsColor || needsShape) idx++;
      });
      setDisplayConfig({ ...displayConf, colors: mergedColors, shapes: mergedShapes });
      const transformedData = {
        ...graphData,
        nodes: graphData.nodes.map((node: any) => ({
          ...node,
          // Remap decision signals to the synthetic 'decision' type so the
          // renderer picks up the seeded purple-diamond encoding above.
          entityType: isDecisionNode(node) ? 'decision' : node.entity_type || node.entityType,
          attributes: node.attributes || {}
        })),
        edges: graphData.edges.map((edge: any) => ({
          ...edge,
          relationshipType: edge.relationship_type || edge.relationshipType
        }))
      };

      setGraphData(transformedData);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load graph data');
    } finally {
      setIsLoading(false);
    }
  }, [currentDomain, entityTypeFilter, relationshipTypeFilter, snapshot, layers, viewMode, seedId, depth]);

  // Seed-selection handlers — centralize transitions between picker/neighborhood/full modes
  const handleSelectSeed = useCallback((entityId: string) => {
    setSeedId(entityId);
    setViewMode('neighborhood');
  }, []);

  const handleBackToPicker = useCallback(() => {
    // Reset the Cytoscape init flag AND drop the stale cy ref. Otherwise
    // `setIsInitialized(true)` on the next mount is a no-op (it's already
    // true), the pan/zoom/layout effect doesn't rerun, and the right-click
    // / minimap / viewport sync handlers never attach to the new instance.
    setIsInitialized(false);
    cyRef.current = null;
    setViewMode('picker');
    setSeedId(null);
    setGraphData(null);
    // Clear any influence-mode filter preset so the picker/full views aren't
    // silently scoped to stakeholders.
    setEntityTypeFilter([]);
    setRelationshipTypeFilter([]);
  }, []);

  const handleShowFullGraph = useCallback(() => {
    setIsInitialized(false);
    cyRef.current = null;
    setViewMode('full');
    setSeedId(null);
  }, []);

  // Influence map for a single client: a client-seeded neighborhood (depth 1,
  // see the fetch effect). Scoping to the client + its stakeholders + their
  // power/reporting edges happens client-side (filteredNodes/filteredEdges,
  // gated on viewMode==='influence') since the neighborhood endpoint doesn't
  // take type filters. Stakeholder nodes then re-encode by stance/influence and
  // formal vs informal edges are drawn distinctly in the graph component.
  const handleShowInfluenceMap = useCallback((clientId: string) => {
    setIsInitialized(false);
    cyRef.current = null;
    // Clear any carried-over neighborhood filters so the influence-specific
    // curation isn't further narrowed by a stale dropdown selection.
    setEntityTypeFilter([]);
    setRelationshipTypeFilter([]);
    setSeedId(clientId);
    setViewMode('influence');
  }, []);

  // Return from an influence map to the same client's plain neighborhood.
  const handleBackToNeighborhood = useCallback(() => {
    setIsInitialized(false);
    cyRef.current = null;
    setViewMode('neighborhood');
  }, []);

  // Handle node click
  const handleNodeClick = (node: DomainGraphNode) => {
    // In merge "pick the duplicate" mode, the next node click resolves the
    // pair and opens the confirm dialog instead of the profile modal.
    if (mergePickSource) {
      if (node.id !== mergePickSource.id) {
        setMergePair({ a: mergePickSource, b: node });
      }
      setMergePickSource(null);
      return;
    }
    setSelectedNode(node);
    setIsNodeModalOpen(true);
  };

  // Handle edge click
  const handleEdgeClick = (edge: DomainGraphEdge) => {
    setSelectedEdge(edge);
  };

  // Handle node hover
  const handleNodeHover = (node: DomainGraphNode | null) => {
    setHoveredNode(node);
  };

  // Right-click a node → open the context menu at the cursor.
  const handleNodeContextMenu = useCallback(
    (node: DomainGraphNode, position: { x: number; y: number }) => {
      setContextMenu({ kind: 'node', node, position });
    },
    [],
  );

  const handleEdgeContextMenu = useCallback(
    (edge: DomainGraphEdge, position: { x: number; y: number }) => {
      setContextMenu({ kind: 'edge', edge, position });
    },
    [],
  );

  // Multi-select tracking from the Cytoscape wrapper. We only surface nodes
  // to the BulkActionBar (edges are not actionable in bulk for this slice).
  const handleSelectionChange = useCallback(
    (sel: { nodes: DomainGraphNode[]; edges: DomainGraphEdge[] }) => {
      setSelectedNodes(sel.nodes);
    },
    [],
  );

  // Open the destructive confirmation. Actual API call lives in
  // confirmPendingDelete below so both node and edge deletions share one
  // AlertDialog surface and one error/success path.
  const requestDeleteNode = useCallback(
    (node: DomainGraphNode) => {
      const label = String(node.attributes?.name ?? node.id);
      setPendingDelete({ kind: 'node', nodeId: node.id, label });
    },
    [],
  );

  const requestDeleteEdge = useCallback(
    (edge: DomainGraphEdge) => {
      const relType = edge.relationshipType || edge.relationship_type;
      if (!relType) return;
      setPendingDelete({ kind: 'edge', edge });
    },
    [],
  );

  const confirmPendingDelete = useCallback(async () => {
    if (!pendingDelete) return;
    setIsDeleting(true);
    try {
      if (pendingDelete.kind === 'node') {
        await deleteNode(pendingDelete.nodeId);
        toast({ title: 'Node deleted', description: pendingDelete.label });
      } else {
        const e = pendingDelete.edge;
        const relType = e.relationshipType || e.relationship_type;
        if (relType) {
          await removeRelationship(e.source, relType, e.target);
          toast({ title: 'Relationship deleted' });
        }
      }
      setPendingDelete(null);
      await loadGraphData();
    } catch (err) {
      toast({
        title: 'Delete failed',
        description: err instanceof Error ? err.message : String(err),
        variant: 'destructive',
      });
    } finally {
      setIsDeleting(false);
    }
  }, [pendingDelete, toast, loadGraphData]);

  // Relationship types available from a given entity type, derived defensively
  // from the domain config (relationships may be an array of {type} or a
  // keyed record depending on the config source).
  const relationshipTypesFor = (entityType: string): string[] => {
    const rels = (domainConfig?.entities as Record<string, any> | undefined)?.[entityType]
      ?.relationships;
    if (Array.isArray(rels)) {
      return rels.map((r) => r?.type).filter((t): t is string => Boolean(t));
    }
    if (rels && typeof rels === 'object') return Object.keys(rels);
    return [];
  };

  const findNodeById = (id: string): DomainGraphNode | null =>
    graphData?.nodes.find((n) => n.id === id) ?? null;

  // Menu items are computed from the current contextMenu target. Kept as a
  // function (not memo) since it only runs when the menu is open.
  const buildNodeMenuItems = (node: DomainGraphNode): ContextMenuItem[] => [
    {
      label: 'Edit…',
      onSelect: () => setEditingNode(node),
    },
    {
      label: 'Edit profile…',
      onSelect: () => setEditingProfileNode(node),
    },
    {
      label: 'Add relationship…',
      onSelect: () => setRelEdit({ source: node, existingEdge: null }),
    },
    {
      label: 'Merge into…',
      onSelect: () => {
        setMergePickSource(node);
        toast({
          title: 'Select the duplicate',
          description: `Click another node to merge it into ${String(
            node.attributes?.name ?? node.id,
          )}`,
        });
      },
    },
    {
      label: 'Re-center here',
      onSelect: () => {
        setSeedId(node.id);
        setViewMode('neighborhood');
      },
    },
    {
      label: 'View profile',
      onSelect: () => {
        setSelectedNode(node);
        setIsNodeModalOpen(true);
      },
    },
    {
      label: 'Delete',
      destructive: true,
      onSelect: () => requestDeleteNode(node),
    },
  ];

  const buildEdgeMenuItems = (edge: DomainGraphEdge): ContextMenuItem[] => [
    {
      label: 'Edit / redirect…',
      onSelect: () => {
        const src = findNodeById(edge.source);
        const relType = edge.relationshipType || edge.relationship_type;
        if (src && relType) {
          setRelEdit({
            source: src,
            existingEdge: { relationshipType: relType, target: edge.target },
          });
        }
      },
    },
    {
      label: 'Delete relationship',
      destructive: true,
      onSelect: () => requestDeleteEdge(edge),
    },
  ];

  const contextMenuItems: ContextMenuItem[] = contextMenu
    ? contextMenu.kind === 'node'
      ? buildNodeMenuItems(contextMenu.node)
      : buildEdgeMenuItems(contextMenu.edge)
    : [];

  // Handle node actions
  const handleNodeAction = (action: string, nodeId: string) => {
    switch (action) {
      case 'filter_by_node':
        // Filter graph to show only this node and connected nodes
        if (graphData) {
          // Find all edges connected to this node
          const connectedEdges = graphData.edges.filter(edge =>
            edge.source === nodeId || edge.target === nodeId
          );

          // Find all connected node IDs
          const connectedNodeIds = new Set<string>([nodeId]);
          connectedEdges.forEach(edge => {
            connectedNodeIds.add(edge.source);
            connectedNodeIds.add(edge.target);
          });

          // Set entity type filter to include only the types of connected nodes
          const connectedNodes = graphData.nodes.filter(node =>
            connectedNodeIds.has(node.id)
          );
          const entityTypes = [...new Set(connectedNodes.map(node =>
            node.entityType || node.entity_type
          ))];

          setEntityTypeFilter(entityTypes);
          // Clear search to show all connected nodes
          setSearchQuery('');

          // Close the modal
          setIsNodeModalOpen(false);
        }
        break;
      case 'hide_node':
        // Hide this node from the graph
        setHiddenNodeIds(prev => {
          const newSet = new Set(prev);
          newSet.add(nodeId);
          return newSet;
        });
        // Close the modal
        setIsNodeModalOpen(false);
        break;
      case 'expand_connections':
        // Show all connections for this node - clear all filters
        setEntityTypeFilter([]);
        setRelationshipTypeFilter([]);
        setSearchQuery('');
        setHiddenNodeIds(new Set());
        // Close the modal
        setIsNodeModalOpen(false);
        break;
      case 'focus_node':
        // Center and zoom on this node
        if (cyRef.current) {
          const cy = cyRef.current;
          const node = cy.getElementById(nodeId);
          if (node && node.length > 0) {
            // Animate to center on the node with appropriate zoom
            cy.animate({
              center: { eles: node },
              zoom: 2,
              duration: 500
            });
          }
        }
        // Close the modal
        setIsNodeModalOpen(false);
        break;
      case 'navigate_to_node': {
        // Navigate to a different node
        const targetNode = graphData?.nodes.find(n => n.id === nodeId);
        if (targetNode) {
          setSelectedNode(targetNode);
        }
        break;
      }
    }
  };

  // Graph control handlers
  const handleZoomIn = () => {
    if (cyRef.current) {
      const cy = cyRef.current;
      cy.zoom({
        level: cy.zoom() * 1.2,
        renderedPosition: { x: cy.width() / 2, y: cy.height() / 2 }
      });
    }
  };

  const handleZoomOut = () => {
    if (cyRef.current) {
      const cy = cyRef.current;
      cy.zoom({
        level: cy.zoom() * 0.8,
        renderedPosition: { x: cy.width() / 2, y: cy.height() / 2 }
      });
    }
  };

  const handleFitToScreen = () => {
    if (cyRef.current) {
      cyRef.current.fit();
    }
  };

  const handleLayoutChange = (layout: string) => {
    setCurrentLayout(layout);
    // Trigger relayout in Cytoscape
    if (cyRef.current) {
      const cy = cyRef.current;

      let layoutOptions: any = {
        name: 'cose',
        animate: true,
        animationDuration: 1000,
        fit: true,
        padding: 50
      };

      switch (layout) {
        case 'hierarchical':
          layoutOptions = {
            name: 'breadthfirst',
            directed: true,
            spacingFactor: 1.5,
            animate: true,
            animationDuration: 1000,
            fit: true,
            padding: 50
          };
          break;
        case 'circular':
          layoutOptions = {
            name: 'circle',
            animate: true,
            animationDuration: 1000,
            fit: true,
            padding: 50
          };
          break;
        case 'grid':
          layoutOptions = {
            name: 'grid',
            animate: true,
            animationDuration: 1000,
            fit: true,
            padding: 50,
            condense: true
          };
          break;
        case 'force-directed':
        default:
          // Use cose layout (already set as default)
          break;
      }

      cy.layout(layoutOptions).run();
    }
  };

  const handleExport = async (format: 'png' | 'svg') => {
    if (!cyRef.current) {
      console.error('Cytoscape instance not available');
      return;
    }

    try {
      const cy = cyRef.current;

      // Get background color from CSS variable
      const bgColor = getComputedStyle(document.documentElement)
        .getPropertyValue('--background')
        .trim();
      const exportBg = bgColor ? `hsl(${bgColor})` : '#1a1a1a';

      if (format === 'png') {
        // Export as PNG using Cytoscape's built-in functionality
        const pngData = cy.png({
          output: 'blob',
          bg: exportBg,
          full: true,
          scale: 2
        });

        const url = URL.createObjectURL(pngData);
        const a = document.createElement('a');
        a.href = url;
        a.download = `graph-${currentDomain}-${new Date().toISOString().split('T')[0]}.png`;
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        URL.revokeObjectURL(url);
      } else if (format === 'svg') {
        // For SVG, we need to use a different approach
        // Create SVG data from the current graph view
        const svgContent = cy.svg({
          full: true,
          scale: 1,
          bg: exportBg
        });

        const blob = new Blob([svgContent], { type: 'image/svg+xml' });
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = `graph-${currentDomain}-${new Date().toISOString().split('T')[0]}.svg`;
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        URL.revokeObjectURL(url);
      }
    } catch (err) {
      console.error('Export failed:', err);
      // Show error to user
      setError('Failed to export graph: ' + (err as Error).message);
    }
  };

  // Relationship types that make up a client's influence map: the works_at /
  // has_stakeholders spokes to the client hub plus the stakeholder↔stakeholder
  // reporting and power edges. Everything else (co_occurrence, involved_in,
  // has_participants, …) is dropped in influence mode.
  const INFLUENCE_REL_TYPES = useMemo(
    () => new Set(['works_at', 'has_stakeholders', 'reports_to', 'manages', 'influences', 'influenced_by']),
    []
  );

  // In influence mode, the set of nodes to keep: the seed client plus the
  // stakeholders directly attached to it (via works_at / has_stakeholders).
  // This drops the client's engagements/consultant and any co-occurrence
  // neighbours (e.g. stakeholders from other clients) the depth-1 query pulls in.
  const influenceKeepIds = useMemo(() => {
    if (viewMode !== 'influence' || !seedId || !graphData) return null;
    const keep = new Set<string>([seedId]);
    for (const edge of graphData.edges) {
      const rel = String((edge.relationshipType ?? edge.relationship_type ?? '')).toLowerCase();
      if (rel !== 'works_at' && rel !== 'has_stakeholders') continue;
      if (edge.source === seedId) keep.add(edge.target);
      if (edge.target === seedId) keep.add(edge.source);
    }
    return keep;
  }, [viewMode, seedId, graphData]);

  // Filter nodes based on search, hidden nodes, and (in influence mode) client scope
  const filteredNodes = useMemo(() => graphData?.nodes.filter(node => {
    // Exclude hidden nodes
    if (hiddenNodeIds.has(node.id)) return false;

    // Layer gate: entity nodes always pass; signal nodes pass per active layer.
    if (!nodePassesLayer(node, layers)) return false;

    // Influence mode: keep only the seed client + its stakeholders
    if (influenceKeepIds && !influenceKeepIds.has(node.id)) return false;

    // Apply search filter
    if (!searchQuery) return true;
    const searchLower = searchQuery.toLowerCase();
    return (
      node.id.toLowerCase().includes(searchLower) ||
      node.attributes.name?.toLowerCase().includes(searchLower) ||
      node.attributes.canonical_name?.toLowerCase().includes(searchLower)
    );
  }) || [], [graphData, searchQuery, hiddenNodeIds, influenceKeepIds, layers]);

  // Create a set of filtered node IDs for efficient edge filtering
  const filteredNodeIds = useMemo(() => new Set(filteredNodes.map(node => node.id)), [filteredNodes]);

  // Filter edges to those whose endpoints survive node filtering, and — in
  // influence mode — to the power/reporting relationship set only.
  const filteredEdges = useMemo(() => graphData?.edges.filter(edge => {
    if (!filteredNodeIds.has(edge.source) || !filteredNodeIds.has(edge.target)) return false;
    if (viewMode === 'influence') {
      const rel = String((edge.relationshipType ?? edge.relationship_type ?? '')).toLowerCase();
      if (!INFLUENCE_REL_TYPES.has(rel)) return false;
    }
    return true;
  }) || [], [graphData, filteredNodeIds, viewMode, INFLUENCE_REL_TYPES]);

  // Calculate statistics (must be before entityTypes which reads nodesByType)
  const stats = {
    totalNodes: graphData?.nodes.length || 0,
    totalEdges: graphData?.edges.length || 0,
    nodesByType: graphData?.statistics?.nodes_by_type || {},
    edgesByType: graphData?.statistics?.edges_by_type || {}
  };

  // Get unique entity types from domain config + graph statistics (signal, document)
  const domainEntityTypes = domainConfig?.entities ? Object.keys(domainConfig.entities).filter(type => type !== '') : [];
  const graphOnlyTypes = Object.keys(stats.nodesByType).filter(
    type => type !== '' && !domainEntityTypes.includes(type)
  );
  // Exclude the synthetic 'decision' type — it's a layer-driven encoding, not a
  // real entity type, so it must not appear as a manual filter option.
  const entityTypes = [...domainEntityTypes, ...graphOnlyTypes].filter(type => type !== 'decision');
  const relationshipTypes = graphData?.edges
    ? [...new Set(graphData.edges.map((e: any) => e.relationshipType || e.relationship_type).filter(Boolean))]
    : [];

  // Resolve the seed ID to a human-readable name when possible. Falls back to
  // the ID so the user always knows what's loaded, even before topEntities or
  // graphData has populated.
  const seedDisplayName = useMemo(() => {
    if (!seedId) return null;
    const fromGraph = graphData?.nodes.find((n) => n.id === seedId);
    if (fromGraph) return String(fromGraph.attributes?.name ?? fromGraph.id);
    const fromPicker = topEntities.find((e) => e.id === seedId);
    if (fromPicker) return fromPicker.name;
    return seedId;
  }, [seedId, graphData, topEntities]);

  // Whether the current seed is a client — gates the "View as influence map"
  // affordance, since an influence map is a per-client view. Falls back to the
  // picker's entity type when the graph hasn't loaded yet.
  const seedIsClient = useMemo(() => {
    if (!seedId) return false;
    const fromGraph = graphData?.nodes.find((n) => n.id === seedId);
    const type = (fromGraph?.entityType ?? (fromGraph as { entity_type?: string } | undefined)?.entity_type)
      ?? topEntities.find((e) => e.id === seedId)?.type;
    return type === 'client';
  }, [seedId, graphData, topEntities]);

  if (isDomainLoading) {
    return (
      <PageContainer className="space-y-6">
        <PageHeader title={uiLabels?.graph_label ?? "Domain Graph"} />
        <Card>
          <CardContent className="py-12 text-center">
            <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-primary mx-auto mb-4"></div>
            <p className="text-muted-foreground">Loading domains...</p>
          </CardContent>
        </Card>
      </PageContainer>
    );
  }

  if (domainError) {
    return (
      <PageContainer className="space-y-6">
        <PageHeader title={uiLabels?.graph_label ?? "Domain Graph"} />
        <Card className="border-destructive/50">
          <CardContent className="py-12 text-center">
            <p className="text-destructive">Error: {domainError}</p>
          </CardContent>
        </Card>
      </PageContainer>
    );
  }

  // Whether the inspector should render — only when a node or edge is selected.
  const hasSelection = Boolean(selectedNode || selectedEdge);

  // The inspector content, shared between the desktop overlay and the mobile
  // Sheet. The page owns positioning; this is the position-agnostic body.
  const inspectorBody = (
    <GraphInspector
      selectedNode={selectedNode}
      selectedEdge={selectedEdge}
      onClose={() => {
        setSelectedNode(null);
        setSelectedEdge(null);
      }}
      onShowDetails={() => setIsNodeModalOpen(true)}
      onEdit={(node) => setEditingNode(node)}
      onRecenter={(nodeId) => {
        setSeedId(nodeId);
        setViewMode('neighborhood');
      }}
      onDeleteNode={requestDeleteNode}
      onDeleteEdge={requestDeleteEdge}
    />
  );

  // Normal mode: full-bleed graph workspace. The page owns positioning for all
  // floating chrome; the canvas wrapper is `absolute inset-0` so the canvas
  // never resizes when a panel opens (the inspector floats over it).
  return (
    <div className="relative h-full w-full overflow-hidden">
      {/* Full-bleed canvas. Always mounted (when there's data) so the
          ResizeObserver target is stable; loading/error/empty states render as
          centered overlays on top. */}
      <div ref={canvasWrapperRef} className="absolute inset-0">
        {graphData && (
          <EnhancedCytoscapeGraphV2
            domainConfig={domainConfig}
            displayConfig={displayConfig}
            nodes={filteredNodes}
            edges={filteredEdges}
            entityTypeFilter={entityTypeFilter}
            relationshipTypeFilter={relationshipTypeFilter}
            onNodeClick={handleNodeClick}
            onEdgeClick={handleEdgeClick}
            onNodeHover={handleNodeHover}
            onNodeContextMenu={handleNodeContextMenu}
            onEdgeContextMenu={handleEdgeContextMenu}
            onSelectionChange={handleSelectionChange}
            cyRef={cyRef}
            onInitialized={() => setIsInitialized(true)}
            viewMode={viewMode}
            seedId={seedId}
          />
        )}
      </div>

      {/* Floating chrome. All of it unmounts when overlays are hidden, except
          GraphControls + the show-overlays hint. */}
      {!overlaysHidden && (
        <>
          {/* Toolbar strip — top, full width with side gutters. Hidden in
              picker mode (the picker card stands alone) and while loading/
              erroring, since there are no nodes to filter yet. */}
          {graphData && viewMode !== 'picker' && (
            <div className={`absolute inset-x-3 top-3 z-10 rounded-lg ${OVERLAY_SURFACE}`}>
              <GraphToolbar
                viewMode={viewMode}
                seedDisplayName={seedDisplayName}
                seedId={seedId}
                seedIsClient={seedIsClient}
                fullGraphNodeLimit={FULL_GRAPH_NODE_LIMIT}
                depth={depth}
                onDepthChange={(d) => setDepth(Math.max(1, Math.min(3, d)))}
                onShowInfluenceMap={() => seedId && handleShowInfluenceMap(seedId)}
                onBackToPicker={handleBackToPicker}
                onShowFullGraph={handleShowFullGraph}
                onBackToNeighborhood={handleBackToNeighborhood}
                searchQuery={searchQuery}
                onSearchQueryChange={setSearchQuery}
                entityTypes={entityTypes}
                entityTypeFilter={entityTypeFilter}
                onEntityTypeFilterChange={setEntityTypeFilter}
                relationshipTypes={relationshipTypes}
                relationshipTypeFilter={relationshipTypeFilter}
                onRelationshipTypeFilterChange={setRelationshipTypeFilter}
                layers={layers}
                onLayersChange={setLayers}
              />
            </div>
          )}

          {/* Refresh — small floating action, top-left (clear of the toolbar
              strip below it in non-picker modes; sits at top in picker mode). */}
          {graphData && (
            <Button
              onClick={loadGraphData}
              variant="outline"
              size="icon"
              disabled={isLoading}
              aria-label={isLoading ? 'Refreshing graph' : 'Refresh graph'}
              title={isLoading ? 'Refreshing graph' : 'Refresh graph'}
              className={`absolute left-3 z-10 ${OVERLAY_SURFACE} ${viewMode === 'picker' ? 'top-3' : 'top-16'}`}
            >
              <RefreshCw className={`h-4 w-4 ${isLoading ? 'animate-spin' : ''}`} />
            </Button>
          )}

          {/* Graph controls — top-right, below the toolbar strip (component owns
              its own absolute top-16/right-3 position). */}
          {graphData && (
            <GraphControls
              onZoomIn={handleZoomIn}
              onZoomOut={handleZoomOut}
              onFitToScreen={handleFitToScreen}
              onLayoutChange={handleLayoutChange}
              onExport={handleExport}
              currentLayout={currentLayout}
              overlaysHidden={overlaysHidden}
              onToggleOverlays={handleToggleOverlays}
            />
          )}

          {/* Minimap (component owns its corner position) */}
          {graphData && (
            <GraphMinimap
              nodes={nodePositions}
              edges={filteredEdges}
              viewport={viewport}
              onViewportChange={(newViewport) => {
                setViewport(newViewport);
                if (cyRef.current) {
                  cyRef.current.viewport({
                    zoom: newViewport.zoom,
                    pan: { x: newViewport.x, y: newViewport.y },
                  });
                }
              }}
            />
          )}

          {/* Influence-map legend (bottom-right) */}
          {graphData && viewMode === 'influence' && (
            <div className="absolute bottom-3 right-3 z-10">
              <InfluenceLegend />
            </div>
          )}

          {/* Ambient stats pill (bottom-left) */}
          {graphData && (
            <div className="absolute bottom-3 left-3 z-10">
              <GraphStatsChip
                totalNodes={stats.totalNodes}
                totalEdges={stats.totalEdges}
                nodesByType={stats.nodesByType}
                edgesByType={stats.edgesByType}
                truncated={graphData?.statistics?.truncated}
                totalAvailableNodes={graphData?.statistics?.total_available_nodes}
              />
            </div>
          )}

          {/* Hover tooltip */}
          {graphData && hoveredNode && (
            <div className={`absolute left-3 top-28 z-10 max-w-xs rounded-lg p-3 ${OVERLAY_SURFACE}`}>
              <h4 className="font-semibold text-foreground">{hoveredNode.attributes.name || hoveredNode.id}</h4>
              <p className="text-sm text-muted-foreground">{hoveredNode.entityType}</p>
              <div className="mt-2 text-xs text-muted-foreground">
                <p>Connections: {(hoveredNode as { degree?: number }).degree || 0}</p>
                {hoveredNode.attributes.department && (
                  <p>Department: {hoveredNode.attributes.department}</p>
                )}
              </div>
            </div>
          )}

          {/* Desktop inspector — right-side overlay. Never resizes the canvas
              (it floats above it). Mobile uses the Sheet below. */}
          {isDesktop && hasSelection && (
            <div className="absolute bottom-3 right-3 top-16 z-30 w-[340px] overflow-y-auto">
              {inspectorBody}
            </div>
          )}

          {/* Seed picker — centered overlay covering the whole canvas. */}
          {viewMode === 'picker' && !isLoading && !error && (
            <div className="absolute inset-0 z-20 grid place-items-center p-4">
              <SeedPickerOverlay
                topEntities={topEntities}
                onSelectSeed={handleSelectSeed}
                onShowFullGraph={handleShowFullGraph}
                domain={currentDomain}
                fullGraphNodeLimit={FULL_GRAPH_NODE_LIMIT}
              />
            </div>
          )}

          {/* Loading overlay */}
          {viewMode !== 'picker' && isLoading && (
            <div className="absolute inset-0 z-20 grid place-items-center">
              <div className="text-center text-muted-foreground">
                <div className="mx-auto mb-4 h-12 w-12 animate-spin rounded-full border-b-2 border-primary"></div>
                <p>
                  {viewMode === 'neighborhood'
                    ? `Building the neighborhood around ${seedDisplayName ?? seedId}`
                    : 'Loading the full graph'}
                </p>
              </div>
            </div>
          )}

          {/* Error overlay */}
          {viewMode !== 'picker' && !isLoading && error && (
            <div className="absolute inset-0 z-20 grid place-items-center p-4">
              <Card className="max-w-sm">
                <CardContent className="py-6 text-center">
                  <p className="mb-2 font-medium text-destructive">Couldn’t load the graph</p>
                  <p className="mb-4 text-sm text-muted-foreground">{error}</p>
                  <Button onClick={loadGraphData} variant="outline" size="sm">
                    <RefreshCw className="mr-1 h-4 w-4" />
                    Try again
                  </Button>
                </CardContent>
              </Card>
            </div>
          )}

          {/* No-domain empty state */}
          {viewMode !== 'picker' && !isLoading && !error && !graphData && (
            <div className="absolute inset-0 z-20 grid place-items-center text-muted-foreground">
              Select a domain to view the graph
            </div>
          )}

          {/* Filters-hide-everything overlay. Data exists but the active filter
              set masks every node. */}
          {graphData && filteredNodes.length === 0 && graphData.nodes.length > 0 && (
            <div className="pointer-events-none absolute inset-0 z-20 grid animate-in fade-in place-items-center duration-150">
              <div className={`pointer-events-auto max-w-sm rounded-lg px-5 py-4 text-center ${OVERLAY_SURFACE}`}>
                <p className="text-sm font-medium text-foreground">No nodes match the current filters</p>
                <p className="mt-1 text-xs text-muted-foreground">
                  {graphData.nodes.length} {graphData.nodes.length === 1 ? 'node is' : 'nodes are'} hidden by the search or type filters above.
                </p>
                <Button
                  variant="outline"
                  size="sm"
                  className="mt-3"
                  onClick={() => {
                    setSearchQuery('');
                    setEntityTypeFilter([]);
                    setRelationshipTypeFilter([]);
                    setHiddenNodeIds(new Set());
                  }}
                >
                  Clear filters
                </Button>
              </div>
            </div>
          )}
        </>
      )}

      {/* When overlays are hidden, keep GraphControls + a hint pill. */}
      {overlaysHidden && (
        <>
          {graphData && (
            <GraphControls
              onZoomIn={handleZoomIn}
              onZoomOut={handleZoomOut}
              onFitToScreen={handleFitToScreen}
              onLayoutChange={handleLayoutChange}
              onExport={handleExport}
              currentLayout={currentLayout}
              overlaysHidden={overlaysHidden}
              onToggleOverlays={handleToggleOverlays}
            />
          )}
          <div className={`absolute bottom-3 left-3 z-10 rounded-lg px-3 py-1.5 text-xs text-muted-foreground ${OVERLAY_SURFACE}`}>
            <kbd className="rounded bg-muted px-1.5 py-0.5 font-mono">F</kbd> · show overlays
          </div>
        </>
      )}

      {/* Mobile inspector — Sheet (below lg). Rendered outside the
          overlays-hidden gate so a controlled close still works, but only opens
          when something is selected and overlays are visible. */}
      <Sheet
        open={!isDesktop && hasSelection && !overlaysHidden}
        onOpenChange={(open) => {
          if (!open) {
            setSelectedNode(null);
            setSelectedEdge(null);
          }
        }}
      >
        <SheetContent side="right" className="w-[340px] overflow-y-auto sm:max-w-sm">
          <SheetHeader className="sr-only">
            <SheetTitle>Inspector</SheetTitle>
          </SheetHeader>
          {inspectorBody}
        </SheetContent>
      </Sheet>

      {/* Node Profile Modal */}
      <NodeProfileModal
        isOpen={isNodeModalOpen}
        onClose={() => setIsNodeModalOpen(false)}
        nodeData={selectedNode}
        onAction={handleNodeAction}
      />

      {/* Graph editor overlays (Issue #877) */}
      <ContextMenu
        position={contextMenu ? contextMenu.position : null}
        items={contextMenuItems}
        onDismiss={() => setContextMenu(null)}
      />
      {editingNode && (
        <NodeEditDialog
          open={!!editingNode}
          onOpenChange={(o) => !o && setEditingNode(null)}
          nodeId={editingNode.id}
          entityType={editingNode.entityType}
          initialAttributes={editingNode.attributes}
          entityDef={
            domainConfig?.entities?.[editingNode.entityType] ?? null
          }
          onSaved={() => loadGraphData()}
        />
      )}
      {mergePair && (
        <MergeNodesDialog
          open={!!mergePair}
          onOpenChange={(o) => !o && setMergePair(null)}
          nodeA={mergePair.a}
          nodeB={mergePair.b}
          onMerged={() => {
            setMergePair(null);
            loadGraphData();
          }}
        />
      )}
      {relEdit && (
        <RelationshipEditDialog
          open={!!relEdit}
          onOpenChange={(o) => !o && setRelEdit(null)}
          sourceNode={relEdit.source}
          relationshipTypes={relationshipTypesFor(relEdit.source.entityType)}
          domain={currentDomain ?? undefined}
          existingEdge={relEdit.existingEdge}
          onSaved={() => {
            setRelEdit(null);
            loadGraphData();
          }}
        />
      )}
      {editingProfileNode && (
        <EditProfileDialog
          open={!!editingProfileNode}
          onOpenChange={(o) => !o && setEditingProfileNode(null)}
          nodeId={editingProfileNode.id}
          onSaved={() => loadGraphData()}
        />
      )}
      <BulkActionBar
        selectedNodes={selectedNodes}
        onClear={() => {
          setSelectedNodes([]);
          cyRef.current?.elements(':selected').unselect();
        }}
        onAction={() => loadGraphData()}
      />

      <AlertDialog
        open={pendingDelete !== null}
        onOpenChange={(open) => {
          if (!open && !isDeleting) setPendingDelete(null);
        }}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>
              {pendingDelete?.kind === 'node'
                ? 'Delete this node?'
                : 'Delete this relationship?'}
            </AlertDialogTitle>
            <AlertDialogDescription>
              {pendingDelete?.kind === 'node' && (
                <>
                  Removes <span className="font-medium text-foreground">{pendingDelete.label}</span>{' '}
                  and all of its relationships from the graph. This cannot be undone.
                </>
              )}
              {pendingDelete?.kind === 'edge' && (
                <>
                  Removes the{' '}
                  <span className="font-mono text-xs text-foreground">
                    {pendingDelete.edge.relationshipType || pendingDelete.edge.relationship_type}
                  </span>{' '}
                  edge between{' '}
                  <span className="font-medium text-foreground">{pendingDelete.edge.source}</span>{' '}
                  and{' '}
                  <span className="font-medium text-foreground">{pendingDelete.edge.target}</span>.
                  This cannot be undone.
                </>
              )}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={isDeleting}>Cancel</AlertDialogCancel>
            <AlertDialogAction
              onClick={(e) => {
                // Prevent default close so we can await the async delete and
                // keep the dialog open (with disabled buttons) on failure.
                e.preventDefault();
                void confirmPendingDelete();
              }}
              disabled={isDeleting}
              className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
            >
              {isDeleting ? 'Deleting…' : 'Delete'}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}

export default function EnhancedDomainGraphPage() {
  return (
    <ErrorBoundary>
      <EnhancedDomainGraphContent />
    </ErrorBoundary>
  );
}
