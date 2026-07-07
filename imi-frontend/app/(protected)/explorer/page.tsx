'use client';

import { useState, useEffect, useCallback, useRef, useMemo, Suspense } from 'react';
import { useSearchParams } from 'next/navigation';
import { Card, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { RefreshCw, User, Briefcase, Building2, Users, Calendar, FolderOpen } from "lucide-react";
import { Tabs, TabsList, TabsTrigger, TabsContent } from "@/components/ui/tabs";
import {
  Sheet,
  SheetContent,
  SheetTitle,
  SheetDescription,
} from "@/components/ui/sheet";
import { PageHeader } from '@/components/ui/page-header';
import { StatsBar } from '@/components/knowledge/StatsBar';
import { SearchAndFilters } from '@/components/knowledge/SearchAndFilters';
import { ResultCards } from '@/components/knowledge/ResultCards';
import { ExportButton } from '@/components/knowledge/ExportButton';
import { useDomain } from '@/contexts/DomainContext';
import {
  searchKnowledge,
  fetchKnowledgeStats,
  fetchEntitySummary,
  fetchFileContent,
  type SearchResult,
  type SearchTotals,
  type Pagination,
  type KnowledgeStats,
  type EntitySummary,
  type FileContent,
} from '@/lib/api/knowledge-explorer';
import dynamic from 'next/dynamic';
import { PageContainer } from "@/components/ui/page-container";

const MarkdownViewer = dynamic(() => import('@/components/MarkdownViewer'), { ssr: false });

export default function KnowledgeExplorer() {
  return (
    <Suspense>
      <KnowledgeExplorerInner />
    </Suspense>
  );
}

function KnowledgeExplorerInner() {
  const searchParams = useSearchParams();
  const { getEntityDisplayName } = useDomain();
  const initialType = searchParams.get('type');

  // Data state
  const [stats, setStats] = useState<KnowledgeStats | null>(null);
  const [results, setResults] = useState<SearchResult[]>([]);
  const [searchTotals, setSearchTotals] = useState<SearchTotals>({ entities: 0, meetings: 0 });
  const [pagination, setPagination] = useState<Pagination>({ page: 1, page_size: 25, total_results: 0, total_pages: 0, has_more: false });
  const [isLoadingStats, setIsLoadingStats] = useState(true);
  const [isLoadingResults, setIsLoadingResults] = useState(true);
  const [isLoadingMore, setIsLoadingMore] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Search & filter state
  const [searchQuery, setSearchQuery] = useState('');
  const [activeTab, setActiveTab] = useState(initialType ? 'entities' : 'all');
  const [selectedEntityTypes, setSelectedEntityTypes] = useState<Set<string>>(
    initialType ? new Set([initialType]) : new Set()
  );

  // Detail sheet
  const [selectedResult, setSelectedResult] = useState<SearchResult | null>(null);
  const [entitySummary, setEntitySummary] = useState<EntitySummary | null>(null);
  const [fileContent, setFileContent] = useState<FileContent | null>(null);
  const [isLoadingDetail, setIsLoadingDetail] = useState(false);

  // Debounce timer ref
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  // ──────────────────────────────────────────────
  // Data loading
  // ──────────────────────────────────────────────

  const loadStats = useCallback(async () => {
    setIsLoadingStats(true);
    try {
      const data = await fetchKnowledgeStats();
      setStats(data);
    } catch (err) {
      console.error('Failed to load stats:', err);
    } finally {
      setIsLoadingStats(false);
    }
  }, []);

  const PAGE_SIZE = 25;

  const fetchPage = useCallback(async (page: number, append: boolean) => {
    if (append) {
      setIsLoadingMore(true);
    } else {
      setIsLoadingResults(true);
    }
    setError(null);
    try {
      const categories = activeTab === 'all' ? undefined : activeTab;
      const entity_types = selectedEntityTypes.size > 0 ? Array.from(selectedEntityTypes).join(',') : undefined;

      const data = await searchKnowledge({
        query: searchQuery,
        categories,
        entity_types,
        page,
        page_size: PAGE_SIZE,
      });

      setResults((prev) => append ? [...prev, ...data.results] : data.results);
      setSearchTotals(data.totals);
      setPagination(data.pagination);
    } catch (err) {
      console.error('Search failed:', err);
      setError(err instanceof Error ? err.message : 'Search failed');
      if (!append) setResults([]);
    } finally {
      setIsLoadingResults(false);
      setIsLoadingMore(false);
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [searchQuery, activeTab, selectedEntityTypes]);

  const loadResults = useCallback(() => fetchPage(1, false), [fetchPage]);

  const loadMore = useCallback(() => {
    if (pagination.has_more && !isLoadingMore) {
      fetchPage(pagination.page + 1, true);
    }
  }, [fetchPage, pagination, isLoadingMore]);

  // Initial load
  useEffect(() => {
    loadStats();
    loadResults();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Sync from URL ?type= param (when user clicks a different entity type in nav)
  const lastAppliedType = useRef<string | null>(initialType);
  useEffect(() => {
    const typeParam = searchParams.get('type');
    if (typeParam !== lastAppliedType.current) {
      lastAppliedType.current = typeParam;
      if (typeParam) {
        setActiveTab('entities');
        setSelectedEntityTypes(new Set([typeParam]));
      } else {
        setActiveTab('all');
        setSelectedEntityTypes(new Set());
      }
    }
  }, [searchParams]);

  // Dynamic page title based on URL type param
  const pageTitle = useMemo(() => {
    const typeParam = searchParams.get('type');
    if (typeParam && selectedEntityTypes.size === 1 && selectedEntityTypes.has(typeParam)) {
      return getEntityDisplayName(typeParam, true);
    }
    return "Knowledge Explorer";
  }, [searchParams, selectedEntityTypes, getEntityDisplayName]);

  // Debounced search on query change
  useEffect(() => {
    if (debounceRef.current) clearTimeout(debounceRef.current);
    debounceRef.current = setTimeout(() => {
      loadResults();
    }, 300);
    return () => {
      if (debounceRef.current) clearTimeout(debounceRef.current);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [searchQuery]);

  // Clear entity type filters when leaving entities tab, reload on tab change
  useEffect(() => {
    if (activeTab !== 'entities') {
      setSelectedEntityTypes(new Set());
    }
    loadResults();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeTab]);

  // Reload when entity type filters change (including when cleared)
  useEffect(() => {
    loadResults();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedEntityTypes]);

  const handleRefresh = () => {
    loadStats();
    loadResults();
  };

  // ──────────────────────────────────────────────
  // Detail sheet
  // ──────────────────────────────────────────────

  const handleResultClick = async (result: SearchResult) => {
    setSelectedResult(result);
    setEntitySummary(null);
    setFileContent(null);
    setIsLoadingDetail(true);
    try {
      const [content, summary] = await Promise.all([
        fetchFileContent(result.id),
        result.category === 'entities'
          ? fetchEntitySummary(result.id)
          : Promise.resolve(null),
      ]);
      setFileContent(content);
      if (summary) setEntitySummary(summary);
    } catch (err) {
      console.error('Failed to load detail:', err);
    } finally {
      setIsLoadingDetail(false);
    }
  };

  // ──────────────────────────────────────────────
  // Derived data
  // ──────────────────────────────────────────────

  // Tab counts from search totals (reflects current query, across all categories)
  const tabCounts = {
    all: searchTotals.entities + searchTotals.meetings,
    entities: searchTotals.entities,
    meetings: searchTotals.meetings,
  };

  // ──────────────────────────────────────────────
  // Render
  // ──────────────────────────────────────────────

  return (
    <PageContainer className="space-y-6">
      <PageHeader
        title={pageTitle}
        actions={
          <div className="flex items-center gap-2">
            <ExportButton results={results} disabled={isLoadingResults} />
            <Button
              variant="outline"
              size="icon"
              onClick={handleRefresh}
              disabled={isLoadingStats || isLoadingResults}
            >
              <RefreshCw className={`h-4 w-4 ${(isLoadingStats || isLoadingResults) ? 'animate-spin' : ''}`} />
            </Button>
          </div>
        }
      />

      {/* Error */}
      {error && (
        <Card className="border-destructive/50 bg-destructive/10">
          <CardContent className="p-4">
            <p className="text-destructive text-sm">{error}</p>
            <Button variant="outline" size="sm" onClick={handleRefresh} className="mt-2">
              Try Again
            </Button>
          </CardContent>
        </Card>
      )}

      {/* Stats Bar */}
      <StatsBar stats={stats} isLoading={isLoadingStats} />

      {/* Search */}
      <SearchAndFilters
        searchQuery={searchQuery}
        onSearchChange={setSearchQuery}
      />

      {/* Results with Tabs */}
      <Tabs value={activeTab} onValueChange={setActiveTab}>
        <div className="flex items-center justify-between">
          <TabsList>
            <TabsTrigger value="all" className="gap-1.5">
              All <Badge variant="gray" className="ml-1 text-[10px]">{tabCounts.all}</Badge>
            </TabsTrigger>
            <TabsTrigger value="entities" className="gap-1.5">
              Entities <Badge variant="gray" className="ml-1 text-[10px]">{tabCounts.entities}</Badge>
            </TabsTrigger>
            <TabsTrigger value="meetings" className="gap-1.5">
              Meetings <Badge variant="gray" className="ml-1 text-[10px]">{tabCounts.meetings}</Badge>
            </TabsTrigger>
          </TabsList>
          {!isLoadingResults && (
            <span className="text-xs text-muted-foreground">
              {pagination.total_results} results
              {searchQuery && ` for "${searchQuery}"`}
            </span>
          )}
        </div>

        {/* Entity type filter chips */}
        {activeTab === 'entities' && stats?.entity_type_counts && Object.keys(stats.entity_type_counts).length > 0 && (
          <div className="flex flex-wrap items-center gap-2 pt-2">
            {Object.entries(stats.entity_type_counts)
              .sort(([, a], [, b]) => b - a)
              .map(([type, count]) => {
                const isSelected = selectedEntityTypes.has(type);
                return (
                  <button
                    key={type}
                    onClick={() => {
                      setSelectedEntityTypes(prev => {
                        const next = new Set(prev);
                        if (next.has(type)) {
                          next.delete(type);
                        } else {
                          next.add(type);
                        }
                        return next;
                      });
                    }}
                    className={`inline-flex items-center gap-1.5 px-3 py-1.5 rounded-full text-xs font-medium transition-colors ${
                      isSelected
                        ? 'bg-blue-500 text-white'
                        : 'bg-muted text-muted-foreground hover:bg-muted/80'
                    }`}
                  >
                    <span className="capitalize">{
                      type === 'person' ? 'People'
                        : type === 'organization' ? 'Organizations'
                        : type.charAt(0).toUpperCase() + type.slice(1) + 's'
                    }</span>
                    <span className={`text-[10px] ${isSelected ? 'text-blue-100' : 'text-muted-foreground/60'}`}>
                      {count}
                    </span>
                  </button>
                );
              })}
            {selectedEntityTypes.size > 0 && (
              <button
                onClick={() => setSelectedEntityTypes(new Set())}
                className="text-xs text-muted-foreground hover:text-foreground transition-colors px-2 py-1.5"
              >
                Clear
              </button>
            )}
          </div>
        )}

        {['all', 'entities', 'meetings'].map((tab) => (
          <TabsContent key={tab} value={tab}>
            <ResultCards
              results={results}
              isLoading={isLoadingResults}
              onResultClick={handleResultClick}
            />
          </TabsContent>
        ))}

        {/* Pagination: Load more + progress */}
        {!isLoadingResults && results.length > 0 && (
          <div className="flex items-center justify-between pt-4">
            <span className="text-xs text-muted-foreground">
              Showing {results.length} of {pagination.total_results} results
            </span>
            {pagination.has_more && (
              <Button
                variant="outline"
                size="sm"
                onClick={loadMore}
                disabled={isLoadingMore}
              >
                {isLoadingMore ? 'Loading...' : `Load more (page ${pagination.page + 1} of ${pagination.total_pages})`}
              </Button>
            )}
          </div>
        )}
      </Tabs>

      {/* Detail Sheet */}
      <Sheet open={!!selectedResult} onOpenChange={(open) => { if (!open) setSelectedResult(null); }}>
        <SheetContent side="right" className="w-full sm:max-w-md p-0 overflow-y-auto">
          {/* Colored header band */}
          {(() => {
            const cat = selectedResult?.category;
            const etype = selectedResult?.entity_type;
            const headerColor = cat === 'entities'
              ? 'bg-blue-500/10 border-b border-blue-500/20'
              : 'bg-purple-500/10 border-b border-purple-500/20';
            const iconColor = cat === 'entities'
              ? 'bg-blue-500/20 text-blue-600 dark:text-blue-400'
              : 'bg-purple-500/20 text-purple-600 dark:text-purple-400';
            const EntityIcon = cat === 'meetings' ? Calendar
              : etype === 'person' ? User
              : etype === 'project' ? Briefcase
              : etype === 'organization' ? Building2
              : etype === 'team' ? Users
              : FolderOpen;

            return (
              <div className={`px-6 pt-6 pb-5 ${headerColor}`}>
                <div className="flex items-start gap-4 pr-6">
                  <div className={`h-11 w-11 rounded-xl flex items-center justify-center flex-shrink-0 ${iconColor}`}>
                    <EntityIcon className="h-5 w-5" />
                  </div>
                  <div className="min-w-0 flex-1">
                    <SheetTitle className="text-base leading-tight mb-1.5">{selectedResult?.title || 'Details'}</SheetTitle>
                    <SheetDescription className="flex items-center gap-2">
                      <Badge
                        variant={cat === 'entities' ? 'blue' : 'secondary'}
                        className="capitalize text-[10px]"
                      >
                        {etype || cat}
                      </Badge>
                      {selectedResult?.date && (
                        <span className="text-xs text-muted-foreground">
                          {new Date(selectedResult.date).toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' })}
                        </span>
                      )}
                    </SheetDescription>
                  </div>
                </div>
              </div>
            );
          })()}

          {/* Body content */}
          <div className="px-6 py-5 space-y-5">
            {isLoadingDetail ? (
              <div className="space-y-3">
                {[1, 2, 3, 4].map((i) => (
                  <div key={i} className="h-4 bg-muted rounded animate-pulse" />
                ))}
              </div>
            ) : (
              <>
                {/* ── Entity stats ── */}
                {selectedResult?.category === 'entities' && entitySummary && (
                  <>
                    <div className="grid grid-cols-2 gap-3">
                      <div className="p-3 bg-muted/50 rounded-lg text-center">
                        <div className="text-xl font-bold">{entitySummary.relationship_count}</div>
                        <div className="text-[11px] text-muted-foreground mt-0.5">Relationships</div>
                      </div>
                      <div className="p-3 bg-muted/50 rounded-lg text-center">
                        <div className="text-sm font-semibold">{entitySummary.last_activity ? new Date(entitySummary.last_activity).toLocaleDateString(undefined, { month: 'short', day: 'numeric' }) : 'N/A'}</div>
                        <div className="text-[11px] text-muted-foreground mt-0.5">Last Activity</div>
                      </div>
                    </div>

                    {entitySummary.top_related.length > 0 && (
                      <div>
                        <h4 className="text-xs font-medium text-muted-foreground uppercase tracking-wider mb-2">Related</h4>
                        <div className="space-y-1.5">
                          {entitySummary.top_related.map((rel) => (
                            <div key={rel.id} className="flex items-center justify-between p-2.5 bg-muted/40 rounded-lg">
                              <div className="flex items-center gap-2">
                                <span className="text-sm font-medium">{rel.name}</span>
                                <Badge variant="outline" className="text-[10px] capitalize">{rel.entity_type}</Badge>
                              </div>
                              {rel.relationship_type && (
                                <span className="text-[10px] text-muted-foreground capitalize">{rel.relationship_type}</span>
                              )}
                            </div>
                          ))}
                        </div>
                      </div>
                    )}
                  </>
                )}

                {/* ── Meeting metadata ── */}
                {selectedResult?.category === 'meetings' && selectedResult.metadata && Object.keys(selectedResult.metadata).length > 0 && (() => {
                  const DISPLAY_FIELDS: Record<string, string> = {
                    date: 'Date',
                    participants: 'Participants',
                    duration: 'Duration',
                    location: 'Location',
                    subject: 'Subject',
                    name: 'Name',
                    role: 'Role',
                    topic: 'Topic',
                  };
                  const formatValue = (key: string, value: string | number | boolean) => {
                    const str = String(value);
                    // Format ISO dates
                    if (key === 'date' && str.match(/^\d{4}-\d{2}-\d{2}/)) {
                      try {
                        return new Date(str).toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' });
                      } catch { return str; }
                    }
                    if (typeof value === 'boolean') return value ? 'Yes' : 'No';
                    return str;
                  };
                  const entries = Object.entries(selectedResult.metadata)
                    .filter(([key]) => key in DISPLAY_FIELDS)
                    .map(([key, value]) => [DISPLAY_FIELDS[key], formatValue(key, value)] as const);
                  if (entries.length === 0) return null;
                  return (
                    <div>
                      <h4 className="text-xs font-medium text-muted-foreground uppercase tracking-wider mb-2">Meeting Info</h4>
                      <div className="rounded-lg border divide-y">
                        {entries.map(([label, value]) => (
                          <div key={label} className="flex items-start justify-between gap-4 px-3 py-2.5">
                            <span className="text-xs text-muted-foreground whitespace-nowrap">{label}</span>
                            <span className="text-sm text-right break-words">{value}</span>
                          </div>
                        ))}
                      </div>
                    </div>
                  );
                })()}

                {/* ── Full content ── */}
                {fileContent?.body && (
                  <div>
                    <h4 className="text-xs font-medium text-muted-foreground uppercase tracking-wider mb-2">Content</h4>
                    <div className="text-sm">
                      <MarkdownViewer content={fileContent.body} />
                    </div>
                  </div>
                )}
              </>
            )}

          </div>
        </SheetContent>
      </Sheet>
    </PageContainer>
  );
}
