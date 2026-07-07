/**
 * Knowledge Explorer API Client
 *
 * Provides search, stats, and entity summary for the unified knowledge explorer.
 */

import { fetcher } from './index';

// ──────────────────────────────────────────────
// Types
// ──────────────────────────────────────────────

export interface SearchResult {
  id: string;
  title: string;
  category: 'entities' | 'meetings';
  entity_type: string | null;
  snippet: string | null;
  date: string | null;
  relevance_score: number;
  metadata: Record<string, string | number | boolean>;
  source_path: string | null;
}

export interface SearchTotals {
  entities: number;
  meetings: number;
}

export interface Pagination {
  page: number;
  page_size: number;
  total_results: number;
  total_pages: number;
  has_more: boolean;
}

export interface SearchResponse {
  results: SearchResult[];
  totals: SearchTotals;
  pagination: Pagination;
  query: string;
  search_time_ms: number;
}

export interface SearchParams {
  query?: string;
  categories?: string;
  entity_types?: string;
  date_from?: string;
  date_to?: string;
  sort_by?: 'relevance' | 'date';
  page?: number;
  page_size?: number;
}

export interface KnowledgeStats {
  total_entities: number;
  total_meetings: number;
  entity_type_counts: Record<string, number>;
  date_range: {
    earliest: string | null;
    latest: string | null;
  };
}

export interface RelatedEntity {
  id: string;
  name: string;
  entity_type: string;
  relationship_type: string | null;
}

export interface EntitySummary {
  name: string;
  entity_type: string;
  relationship_count: number;
  last_activity: string | null;
  top_related: RelatedEntity[];
  snippet: string | null;
}

// ──────────────────────────────────────────────
// API Functions
// ──────────────────────────────────────────────

const buildQueryString = (params: Record<string, string | number | boolean | undefined | null>): string => {
  const searchParams = new URLSearchParams();
  Object.entries(params).forEach(([key, value]) => {
    if (value !== undefined && value !== null && value !== '') {
      searchParams.append(key, String(value));
    }
  });
  return searchParams.toString();
};

/**
 * Search across all knowledge: entities, meetings, documents
 */
export const searchKnowledge = async (params: SearchParams = {}): Promise<SearchResponse> => {
  const qs = buildQueryString(params);
  const url = `/knowledge-explorer/search${qs ? `?${qs}` : ''}`;
  return fetcher(url, { method: 'GET' });
};

/**
 * Fetch aggregate stats for the knowledge base
 */
export const fetchKnowledgeStats = async (): Promise<KnowledgeStats> => {
  return fetcher('/knowledge-explorer/stats', { method: 'GET' });
};

/**
 * Fetch lightweight entity summary for the detail sheet
 */
export const fetchEntitySummary = async (entityId: string): Promise<EntitySummary> => {
  return fetcher(`/knowledge-explorer/entity/${encodeURIComponent(entityId)}/summary`, { method: 'GET' });
};

export interface FileContent {
  title: string;
  body: string;
  metadata: Record<string, string | number | boolean>;
}

/**
 * Fetch full file content for the detail sheet
 */
export const fetchFileContent = async (filePath: string): Promise<FileContent> => {
  return fetcher(`/knowledge-explorer/file/${encodeURIComponent(filePath)}`, { method: 'GET' });
};

/**
 * Export search results to JSON or CSV (client-side)
 */
export const exportSearchResults = (results: SearchResult[], format: 'json' | 'csv'): void => {
  let content: string;
  let mimeType: string;
  let filename: string;

  if (format === 'json') {
    // eslint-disable-next-line @typescript-eslint/no-unused-vars
    const cleanedResults = results.map(({ id, source_path, relevance_score, ...rest }) => rest);
    content = JSON.stringify(cleanedResults, null, 2);
    mimeType = 'application/json';
    filename = `knowledge-export-${new Date().toISOString().slice(0, 10)}.json`;
  } else {
    // CSV
    const headers: (keyof SearchResult)[] = ['title', 'category', 'entity_type', 'date', 'snippet'];
    const rows = results.map(r =>
      headers.map(h => {
        const val = r[h];
        if (val === null || val === undefined) return '';
        const str = String(val).replace(/"/g, '""');
        return `"${str}"`;
      }).join(',')
    );
    content = [headers.join(','), ...rows].join('\n');
    mimeType = 'text/csv';
    filename = `knowledge-export-${new Date().toISOString().slice(0, 10)}.csv`;
  }

  const blob = new Blob([content], { type: mimeType });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
};
