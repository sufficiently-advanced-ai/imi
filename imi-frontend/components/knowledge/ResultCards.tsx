'use client';

import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { Users, Calendar, User, Briefcase, Building2, FolderOpen } from "lucide-react";
import type { SearchResult } from "@/lib/api/knowledge-explorer";

interface ResultCardsProps {
  results: SearchResult[];
  isLoading: boolean;
  onResultClick?: (result: SearchResult) => void;
}

function getCategoryIcon(category: string, entityType: string | null) {
  if (category === 'meetings') return Calendar;
  // Entity types
  switch (entityType) {
    case 'person': return User;
    case 'project': return Briefcase;
    case 'organization': return Building2;
    case 'team': return Users;
    case 'account': return Building2;
    default: return FolderOpen;
  }
}

function getCategoryColor(category: string): string {
  switch (category) {
    case 'entities': return 'bg-blue-500/10 text-blue-600 dark:text-blue-400';
    case 'meetings': return 'bg-purple-500/10 text-purple-600 dark:text-purple-400';
    default: return 'bg-muted text-muted-foreground';
  }
}

function getBadgeVariant(category: string): 'blue' | 'secondary' | 'gray' {
  switch (category) {
    case 'entities': return 'blue';
    case 'meetings': return 'secondary';
    default: return 'gray';
  }
}

function formatRelativeDate(dateStr: string | null): string {
  if (!dateStr) return '';
  try {
    const date = new Date(dateStr);
    const now = new Date();
    const diffMs = now.getTime() - date.getTime();
    const diffDays = Math.floor(diffMs / (1000 * 60 * 60 * 24));

    if (diffDays < 0) return 'Today';
    if (diffDays === 0) return 'Today';
    if (diffDays === 1) return 'Yesterday';
    if (diffDays < 7) return `${diffDays} days ago`;
    if (diffDays < 30) return `${Math.floor(diffDays / 7)} weeks ago`;
    if (diffDays < 365) return `${Math.floor(diffDays / 30)} months ago`;
    return `${Math.floor(diffDays / 365)} years ago`;
  } catch {
    return '';
  }
}

function ResultCard({ result, onClick }: { result: SearchResult; onClick?: () => void }) {
  const Icon = getCategoryIcon(result.category, result.entity_type);
  const iconColor = getCategoryColor(result.category);

  return (
    <Card
      className="hover:shadow-md transition-shadow cursor-pointer group"
      onClick={onClick}
    >
      <CardContent className="p-4">
        <div className="flex items-start gap-3">
          <div className={`h-9 w-9 rounded-lg flex items-center justify-center flex-shrink-0 ${iconColor}`}>
            <Icon className="h-4 w-4" />
          </div>
          <div className="flex-1 min-w-0">
            <div className="flex items-center gap-2 mb-1">
              <h3 className="text-sm font-medium truncate group-hover:text-primary transition-colors">
                {result.title}
              </h3>
              <Badge variant={getBadgeVariant(result.category)} className="flex-shrink-0 text-[10px]">
                {result.entity_type || result.category}
              </Badge>
            </div>
            {result.snippet && (
              <p className="text-xs text-muted-foreground line-clamp-2 mb-1.5">
                {result.snippet}
              </p>
            )}
            <div className="flex items-center gap-2 text-[11px] text-muted-foreground">
              {result.date && <span>{formatRelativeDate(result.date)}</span>}
            </div>
          </div>
        </div>
      </CardContent>
    </Card>
  );
}

function LoadingSkeleton() {
  return (
    <div className="space-y-3">
      {[1, 2, 3, 4, 5, 6].map((i) => (
        <Card key={i}>
          <CardContent className="p-4">
            <div className="flex items-start gap-3">
              <Skeleton className="h-9 w-9 rounded-lg" />
              <div className="flex-1 space-y-2">
                <div className="flex items-center gap-2">
                  <Skeleton className="h-4 w-40" />
                  <Skeleton className="h-4 w-16 rounded-full" />
                </div>
                <Skeleton className="h-3 w-full" />
                <Skeleton className="h-3 w-24" />
              </div>
            </div>
          </CardContent>
        </Card>
      ))}
    </div>
  );
}

function EmptyState({ query }: { query: string }) {
  return (
    <div className="flex flex-col items-center justify-center py-12 text-center">
      <div className="h-12 w-12 rounded-full bg-muted flex items-center justify-center mb-3">
        <FolderOpen className="h-6 w-6 text-muted-foreground" />
      </div>
      <h3 className="text-sm font-medium mb-1">No results found</h3>
      <p className="text-xs text-muted-foreground max-w-sm">
        {query
          ? `No items match "${query}". Try a different search term or adjust your filters.`
          : 'No content found in the knowledge base.'}
      </p>
    </div>
  );
}

export function ResultCards({ results, isLoading, onResultClick }: ResultCardsProps) {
  if (isLoading) {
    return <LoadingSkeleton />;
  }

  if (results.length === 0) {
    return <EmptyState query="" />;
  }

  return (
    <div className="space-y-2">
      {results.map((result) => (
        <ResultCard
          key={result.id}
          result={result}
          onClick={() => onResultClick?.(result)}
        />
      ))}
    </div>
  );
}

export { EmptyState, LoadingSkeleton };
