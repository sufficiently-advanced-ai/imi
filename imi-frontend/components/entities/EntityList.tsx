'use client';

import React, { useState, useEffect } from 'react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Card, CardContent } from '@/components/ui/card';
import { Skeleton } from '@/components/ui/skeleton';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue
} from '@/components/ui/select';
import { EntityTable } from './EntityTable';
import {
  Plus,
  Search,
  SortAsc,
  SortDesc,
  AlertCircle,
  RefreshCw,
  Database
} from 'lucide-react';
import { Entity, DomainSchema } from '@/lib/api/entities';
import { useEntities } from '@/lib/hooks/useEntities';
import { cn } from '@/lib/utils';

interface EntityListProps {
  domainConfig: DomainSchema;
  entities?: Entity[];
  entityType?: string;
  pagination?: {
    page: number;
    size: number;
    total: number;
    pages: number;
  };
  onEntitySelect?: (entityId: string) => void;
  onEntityCreate?: (entityType: string) => void;
  onEntityUpdate?: (entityId: string) => void;
  onEntityDelete?: (entityId: string) => void;
  onEntityTypeFilter?: (entityType: string) => void;
  onSearch?: (query: string) => void;
  loading?: boolean;
  error?: string | null;
  className?: string;
}

// Pagination component
const Pagination: React.FC<{
  pagination: {
    page: number;
    size: number;
    total: number;
    pages: number;
  };
  onPageChange: (page: number) => void;
  loading: boolean;
}> = ({ pagination, onPageChange, loading }) => {
  if (!pagination || pagination.pages <= 1) return null;

  const { page, pages, total } = pagination;

  return (
    <div className="flex items-center justify-between" role="navigation" aria-label="Pagination">
      <div className="text-sm text-muted-foreground">
        Showing {((page - 1) * pagination.size) + 1}-{Math.min(page * pagination.size, total)} of {total} results
      </div>

      <div className="flex items-center gap-2">
        <Button
          variant="outline"
          size="sm"
          onClick={() => onPageChange(page - 1)}
          disabled={loading || page <= 1}
        >
          Previous
        </Button>

        <div className="flex items-center gap-1">
          {[...Array(Math.min(5, pages))].map((_, i) => {
            const pageNum = Math.max(1, Math.min(pages - 4, page - 2)) + i;
            if (pageNum > pages) return null;

            return (
              <Button
                key={pageNum}
                variant={pageNum === page ? "default" : "outline"}
                size="sm"
                onClick={() => onPageChange(pageNum)}
                disabled={loading}
                className="w-8"
              >
                {pageNum}
              </Button>
            );
          })}
        </div>

        <Button
          variant="outline"
          size="sm"
          onClick={() => onPageChange(page + 1)}
          disabled={loading || page >= pages}
          aria-label="Next page"
        >
          Next
        </Button>
      </div>
    </div>
  );
};

// Loading skeleton for entity table
function EntityTableSkeleton() {
  return (
    <Card>
      <CardContent className="p-0">
        <div className="border-b border-border p-4">
          <div className="flex gap-4">
            <Skeleton className="h-4 w-32" />
            <Skeleton className="h-4 w-48" />
            <Skeleton className="h-4 w-24" />
          </div>
        </div>
        {[1, 2, 3, 4, 5].map((i) => (
          <div key={i} className="border-b border-border/50 p-4 last:border-b-0">
            <div className="flex gap-4">
              <Skeleton className="h-5 w-32" />
              <Skeleton className="h-5 w-48" />
              <Skeleton className="h-5 w-24" />
            </div>
          </div>
        ))}
      </CardContent>
    </Card>
  );
}

export const EntityList: React.FC<EntityListProps> = ({
  domainConfig,
  entities: externalEntities,
  entityType: externalEntityType,
  pagination: externalPagination,
  onEntitySelect,
  onEntityCreate,
  onEntityUpdate,
  onEntityDelete,
  onEntityTypeFilter,
  onSearch,
  loading: externalLoading = false,
  error: externalError = null,
  className
}) => {
  // Get available entity types from domain config
  const availableEntityTypes = Object.keys(domainConfig?.entities || {});
  const defaultEntityType = availableEntityTypes[0] || '';

  // Local state for controlled mode or use hooks for uncontrolled mode
  const [currentEntityType, setCurrentEntityType] = useState(
    externalEntityType || defaultEntityType
  );
  const [searchQuery, setSearchQuery] = useState('');
  const [sortField, setSortField] = useState('name');
  const [sortOrder, setSortOrder] = useState<'asc' | 'desc'>('asc');

  // Use the entities hook if not in controlled mode
  const {
    entities: hookEntities,
    loading: hookLoading,
    error: hookError,
    pagination: hookPagination,
    searchEntitiesAction,
    goToPage,
    refresh
  } = useEntities({
    entityType: currentEntityType,
    autoLoad: !externalEntities
  });

  // Determine which data source to use
  const entities = externalEntities || hookEntities;
  const loading = externalLoading || hookLoading;
  const error = externalError || hookError;
  const pagination = externalPagination || hookPagination;

  // Get available entity types and their configs
  const entityTypes = Object.keys(domainConfig?.entities || {});

  // Sync currentEntityType when domainConfig loads or changes
  useEffect(() => {
    // If we don't have a valid entity type selected but have available types, set the first one
    if (entityTypes.length > 0 && !entityTypes.includes(currentEntityType)) {
      const newType = externalEntityType && entityTypes.includes(externalEntityType)
        ? externalEntityType
        : entityTypes[0];
      setCurrentEntityType(newType);
      if (onEntityTypeFilter) {
        onEntityTypeFilter(newType);
      }
    }
  }, [entityTypes, currentEntityType, externalEntityType, onEntityTypeFilter]);

  // Handle entity type change
  const handleEntityTypeChange = (entityType: string) => {
    setCurrentEntityType(entityType);
    if (onEntityTypeFilter) {
      onEntityTypeFilter(entityType);
    }
  };

  // Handle search
  const handleSearch = async (query: string) => {
    setSearchQuery(query);
    if (onSearch) {
      onSearch(query);
    } else if (query.trim()) {
      await searchEntitiesAction(query);
    } else {
      await refresh();
    }
  };

  // Handle sort
  const handleSort = (field: string, direction: 'asc' | 'desc') => {
    setSortField(field);
    setSortOrder(direction);
  };

  // Handle row actions
  const handleRowAction = (action: 'edit' | 'delete' | 'view', entityId: string) => {
    switch (action) {
      case 'view':
      case 'edit':
        if (onEntitySelect) {
          onEntitySelect(entityId);
        } else if (onEntityUpdate) {
          onEntityUpdate(entityId);
        }
        break;
      case 'delete':
        if (onEntityDelete) {
          onEntityDelete(entityId);
        }
        break;
    }
  };

  // Handle pagination
  const handlePageChange = (page: number) => {
    if (goToPage) {
      goToPage(page);
    }
  };

  if (!domainConfig || !domainConfig.entities || entityTypes.length === 0) {
    return (
      <Card className="border-destructive/50">
        <CardContent className="py-12 text-center">
          <AlertCircle className="h-12 w-12 mx-auto text-destructive/60 mb-4" />
          <div className="text-lg font-semibold text-foreground mb-2">
            Configuration Error
          </div>
          <div className="text-sm text-muted-foreground">
            No domain configuration available or no entity types defined.
          </div>
        </CardContent>
      </Card>
    );
  }

  return (
    <div className={cn('space-y-6', className)}>
      {/* Search and filters */}
      <Card>
        <CardContent className="py-4">
          <div className="flex flex-col sm:flex-row gap-4">
            {/* Entity type selector */}
            <Select value={currentEntityType} onValueChange={handleEntityTypeChange}>
              <SelectTrigger className="w-full sm:w-[180px]" aria-label="Entity type">
                <SelectValue placeholder="Select type" />
              </SelectTrigger>
              <SelectContent>
                {entityTypes.map((type) => {
                  const entity = domainConfig?.entities?.[type];
                  const displayName = entity?.plural_label || entity?.label || entity?.name || type;
                  return (
                    <SelectItem key={type} value={type}>
                      {displayName}
                    </SelectItem>
                  );
                })}
              </SelectContent>
            </Select>

            {/* Search input */}
            <div className="relative flex-1">
              <Search className="absolute left-3 top-1/2 transform -translate-y-1/2 h-4 w-4 text-muted-foreground" />
              <Input
                type="search"
                placeholder="Search entities..."
                value={searchQuery}
                onChange={(e) => handleSearch(e.target.value)}
                className="pl-10"
              />
            </div>

            {/* Sort controls */}
            <Select value={sortField} onValueChange={setSortField}>
              <SelectTrigger className="w-full sm:w-[150px]" aria-label="Sort by">
                <SelectValue placeholder="Sort by" />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="name">Name</SelectItem>
                <SelectItem value="created_at">Created</SelectItem>
                <SelectItem value="updated_at">Updated</SelectItem>
              </SelectContent>
            </Select>

            <Button
              variant="outline"
              size="icon"
              onClick={() => setSortOrder(sortOrder === 'asc' ? 'desc' : 'asc')}
              aria-label="Sort order"
            >
              {sortOrder === 'asc' ? <SortAsc className="h-4 w-4" /> : <SortDesc className="h-4 w-4" />}
            </Button>

            {/* Create button */}
            <Button
              onClick={() => onEntityCreate && onEntityCreate(currentEntityType)}
              disabled={loading}
              aria-label={`Create ${domainConfig?.entities?.[currentEntityType]?.label || domainConfig?.entities?.[currentEntityType]?.name || currentEntityType}`}
            >
              <Plus className="h-4 w-4 mr-2" />
              Create
            </Button>
          </div>
        </CardContent>
      </Card>

      {/* Error state */}
      {error && (
        <Card className="border-destructive/50">
          <CardContent className="py-8 text-center">
            <AlertCircle className="h-8 w-8 mx-auto text-destructive/60 mb-3" />
            <div className="text-sm font-medium text-foreground mb-2">
              Failed to load entities
            </div>
            <div className="text-sm text-muted-foreground mb-4">
              {error}
            </div>
            <Button onClick={refresh} variant="outline" size="sm">
              <RefreshCw className="h-4 w-4 mr-2" />
              Retry
            </Button>
          </CardContent>
        </Card>
      )}

      {/* Loading state */}
      {loading && entities.length === 0 && !error && (
        <EntityTableSkeleton />
      )}

      {/* Empty state */}
      {!loading && !error && entities.length === 0 && (
        <Card>
          <CardContent className="py-12 text-center">
            <Database className="h-12 w-12 mx-auto text-muted-foreground/50 mb-4" />
            <div className="text-lg font-semibold text-foreground mb-2">
              No Entities Found
            </div>
            <div className="text-sm text-muted-foreground mb-6">
              {searchQuery
                ? `No entities match "${searchQuery}"`
                : `No ${domainConfig?.entities?.[currentEntityType]?.plural_label || domainConfig?.entities?.[currentEntityType]?.label || domainConfig?.entities?.[currentEntityType]?.name || currentEntityType} exist yet.`}
            </div>
            {!searchQuery && (
              <Button onClick={() => onEntityCreate && onEntityCreate(currentEntityType)}>
                <Plus className="h-4 w-4 mr-2" />
                Create {domainConfig?.entities?.[currentEntityType]?.label || domainConfig?.entities?.[currentEntityType]?.name || currentEntityType}
              </Button>
            )}
          </CardContent>
        </Card>
      )}

      {/* Entity table */}
      {!error && entities.length > 0 && (
        <EntityTable
          domainConfig={domainConfig}
          entities={entities}
          entityType={currentEntityType}
          onSort={handleSort}
          onRowAction={handleRowAction}
          sortField={sortField}
          sortDirection={sortOrder}
          loading={loading}
        />
      )}

      {/* Pagination */}
      {pagination && pagination.pages > 1 && (
        <Pagination
          pagination={pagination}
          onPageChange={handlePageChange}
          loading={loading}
        />
      )}
    </div>
  );
};
