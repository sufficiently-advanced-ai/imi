// Domain API client module

import { getApiUrl } from '@/lib/config';

export interface DomainGraphNode {
  id: string;
  entityType: string;
  attributes: Record<string, any>;
}

export interface DomainGraphEdge {
  id: string;
  source: string;
  target: string;
  relationshipType?: string;
  relationship_type?: string;
}

export interface DomainGraphData {
  nodes: DomainGraphNode[];
  edges: DomainGraphEdge[];
  statistics: {
    total_nodes: number;
    total_edges: number;
    by_entity_type?: Record<string, number>;
    relationships?: Record<string, number>;
    // Added with Tier 1 context-first loading:
    truncated?: boolean;
    total_available_nodes?: number;
    limit?: number | null;
    include_signals?: boolean;
  };
}

export interface DomainDisplayConfig {
  colors?: Record<string, string>;
  shapes?: Record<string, string>;
  [key: string]: any;
}

interface FetchDomainGraphOptions {
  domain: string;
  entityTypes?: string[];
  relationshipTypes?: string[];
  snapshot?: string;
  includeSignals?: boolean;
  limit?: number;
}

/**
 * Fetch domain graph data from the API
 */
export async function fetchDomainGraphData(options: FetchDomainGraphOptions): Promise<DomainGraphData> {
  const params = new URLSearchParams();
  params.append('domain', options.domain);

  if (options.entityTypes && options.entityTypes.length > 0) {
    params.append('entity_types', options.entityTypes.join(','));
  }

  if (options.relationshipTypes && options.relationshipTypes.length > 0) {
    params.append('relationship_types', options.relationshipTypes.join(','));
  }

  if (options.snapshot) {
    params.append('snapshot', options.snapshot);
  }

  if (options.includeSignals !== undefined) {
    params.append('include_signals', String(options.includeSignals));
  }

  if (options.limit !== undefined) {
    params.append('limit', String(options.limit));
  }

  const response = await fetch(getApiUrl(`/domain-graph?${params.toString()}`), {
    credentials: 'include' as RequestCredentials,
  });

  if (!response.ok) {
    throw new Error(`Failed to fetch domain graph data: ${response.statusText}`);
  }

  return response.json();
}

interface FetchNeighborhoodOptions {
  seed: string;
  depth?: number;          // 1-3; server caps
  includeSignals?: boolean;
  limit?: number;          // Max entity nodes; signals add on top
  domain?: string;
}

/**
 * Fetch a k-hop subgraph centered on `seed`. Cheap compared to
 * fetchDomainGraphData — server-side query is bounded by node set, not
 * by total graph size. Use this for the context-graph UX.
 */
export async function fetchNeighborhood(options: FetchNeighborhoodOptions): Promise<DomainGraphData> {
  const params = new URLSearchParams();
  params.append('seed', options.seed);
  if (options.depth !== undefined) params.append('depth', String(options.depth));
  if (options.includeSignals !== undefined) params.append('include_signals', String(options.includeSignals));
  if (options.limit !== undefined) params.append('limit', String(options.limit));
  if (options.domain) params.append('domain', options.domain);

  const response = await fetch(getApiUrl(`/domain-graph/neighborhood?${params.toString()}`), {
    credentials: 'include' as RequestCredentials,
  });

  if (!response.ok) {
    throw new Error(`Failed to fetch neighborhood: ${response.statusText}`);
  }

  return response.json();
}

interface SearchEntitiesOptions {
  query: string;
  maxResults?: number;
  domain?: string;
}

export interface EntitySearchResult {
  id: string;
  name: string;
  // Backend search endpoint uses `type`, not `entity_type`
  type: string;
  score?: number;
  attributes?: Record<string, unknown>;
  file_path?: string;
}

/**
 * Full-text search over entity names. Used to pick a seed node for the
 * neighborhood view — users type a name, we show matches, they click one.
 */
export async function searchEntities(options: SearchEntitiesOptions): Promise<EntitySearchResult[]> {
  const params = new URLSearchParams();
  // Backend uses 'query', not 'q'
  params.append('query', options.query);
  if (options.maxResults !== undefined) params.append('max_results', String(options.maxResults));
  if (options.domain) params.append('domain', options.domain);

  const response = await fetch(getApiUrl(`/domain-graph/search?${params.toString()}`), {
    credentials: 'include' as RequestCredentials,
  });

  if (!response.ok) {
    throw new Error(`Search failed: ${response.statusText}`);
  }

  const data = await response.json();
  // Backend returns { results: [...] } or a bare list; handle both.
  const rows: unknown[] = Array.isArray(data) ? data : (data.results || []);
  // Each hit has the shape
  //   { path, score, title, snippet, entity: { id, name, type, attributes, file_path } }
  // i.e. the entity fields are nested under `entity`. Flatten to the
  // EntitySearchResult contract (flat id/name/type) so callers — the
  // relationship target picker and the seed picker — get a usable id/name.
  // Tolerate rows that are already flat (no `entity`) and drop hits with no id.
  return rows
    .map((row): EntitySearchResult => {
      const r = (row ?? {}) as Record<string, any>;
      const e: Record<string, any> =
        r.entity && typeof r.entity === 'object' ? r.entity : r;
      return {
        id: e.id ?? '',
        name: e.name ?? r.title ?? e.id ?? '',
        type: e.type ?? e.entity_type ?? '',
        score: r.score ?? e.score,
        attributes: e.attributes,
        file_path: e.file_path ?? r.path,
      };
    })
    .filter((r) => r.id);
}

export interface TopEntity {
  id: string;
  name: string;
  type: string;
  degree: number;
}

/**
 * Fetch the most-connected entities, ranked by degree. Used to populate
 * the default seed picker on the graph page so users can start exploring
 * without waiting for a full graph build.
 */
export async function fetchTopEntities(
  options: { limit?: number; entityTypes?: string[]; domain?: string } = {}
): Promise<TopEntity[]> {
  const params = new URLSearchParams();
  if (options.limit !== undefined) params.append('limit', String(options.limit));
  if (options.entityTypes && options.entityTypes.length > 0) {
    params.append('entity_types', options.entityTypes.join(','));
  }
  // Must be passed for multi-domain correctness — otherwise the picker can
  // suggest seeds the active domain's /neighborhood won't resolve.
  if (options.domain) params.append('domain', options.domain);
  const response = await fetch(getApiUrl(`/domain-graph/top-entities?${params.toString()}`), {
    credentials: 'include' as RequestCredentials,
  });
  if (!response.ok) {
    throw new Error(`Failed to fetch top entities: ${response.statusText}`);
  }
  const data = await response.json();
  return data.entities || [];
}

/**
 * Fetch domain display configuration
 */
export async function fetchDomainDisplayConfig(domain?: string): Promise<DomainDisplayConfig> {
  const params = domain ? `?domain=${encodeURIComponent(domain)}` : '';
  const response = await fetch(getApiUrl(`/domain-graph/display-config${params}`), {
    credentials: 'include' as RequestCredentials,
  });
  
  if (!response.ok) {
    // Return default config if endpoint doesn't exist
    return {
      colors: {
        person: '#4a90e2',
        project: '#7ed321',
        team: '#f5a623',
        organization: '#bd10e0',
        default: '#666666'
      },
      shapes: {
        person: 'ellipse',
        project: 'round-rectangle',
        team: 'diamond',
        organization: 'hexagon',
        default: 'ellipse'
      }
    };
  }
  
  const raw = await response.json();

  // Transform { entityType: { color, icon } } → { colors: {...}, shapes: {...} }
  const iconToShape: Record<string, string> = {
    user: 'ellipse',
    users: 'diamond',
    building: 'hexagon',
    folder: 'round-rectangle',
    target: 'star',
    briefcase: 'round-rectangle',
    star: 'star',
    clipboard: 'round-rectangle',
    calendar: 'round-rectangle',
    circle: 'ellipse',
  };

  const colors: Record<string, string> = {};
  const shapes: Record<string, string> = {};

  for (const [entityType, config] of Object.entries(raw)) {
    const cfg = config as { color?: string; icon?: string };
    if (cfg.color) colors[entityType] = cfg.color;
    shapes[entityType] = iconToShape[cfg.icon || ''] || 'ellipse';
  }

  return { colors, shapes };
}

interface ExportDomainGraphOptions {
  format: 'json' | 'csv' | 'graphml';
  domain?: string;
}

/**
 * Export domain graph in specified format
 */
export async function exportDomainGraph(options: ExportDomainGraphOptions): Promise<Blob> {
  const params = new URLSearchParams();
  params.append('format', options.format);
  
  if (options.domain) {
    params.append('domain', options.domain);
  }

  const response = await fetch(getApiUrl(`/domain-graph/export?${params.toString()}`), {
    credentials: 'include' as RequestCredentials,
  });
  
  if (!response.ok) {
    throw new Error(`Failed to export domain graph: ${response.statusText}`);
  }
  
  return response.blob();
}