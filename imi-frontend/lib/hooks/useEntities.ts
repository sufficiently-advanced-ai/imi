/**
 * Entity Management Hook
 * 
 * Provides comprehensive entity list management with filtering, sorting, pagination,
 * and CRUD operations for the domain-aware entity system.
 */

import { useState, useEffect, useCallback, useMemo } from 'react';
import { 
  listEntities, 
  searchEntities, 
  createEntity, 
  updateEntity, 
  deleteEntity,
  Entity, 
  EntityListParams, 
  CreateEntityData, 
  UpdateEntityData, 
  DeleteEntityOptions 
} from '@/lib/api/entities';

export interface UseEntitiesOptions {
  entityType?: string;
  initialPage?: number;
  pageSize?: number;
  autoLoad?: boolean;
}

export interface UseEntitiesReturn {
  // State
  entities: Entity[];
  loading: boolean;
  error: string | null;
  pagination: {
    page: number;
    size: number;
    total: number;
    pages: number;
  } | null;
  
  // Search and filters
  searchQuery: string;
  filters: Record<string, any>;
  sortBy: string;
  sortOrder: 'asc' | 'desc';
  
  // Actions
  loadEntities: (params?: EntityListParams) => Promise<void>;
  searchEntitiesAction: (query: string) => Promise<void>;
  createEntityAction: (data: CreateEntityData) => Promise<Entity | null>;
  updateEntityAction: (id: string, data: UpdateEntityData) => Promise<Entity | null>;
  deleteEntityAction: (id: string, options?: DeleteEntityOptions) => Promise<boolean>;
  
  // Filter and sort actions
  setSearchQuery: (query: string) => void;
  setFilters: (filters: Record<string, any>) => void;
  setSortBy: (field: string) => void;
  setSortOrder: (order: 'asc' | 'desc') => void;
  clearFilters: () => void;
  
  // Pagination
  goToPage: (page: number) => Promise<void>;
  nextPage: () => Promise<void>;
  prevPage: () => Promise<void>;
  
  // Refresh
  refresh: () => Promise<void>;
}

export const useEntities = (options: UseEntitiesOptions = {}): UseEntitiesReturn => {
  const {
    entityType,
    initialPage = 1,
    pageSize = 25,
    autoLoad = true
  } = options;

  // State
  const [entities, setEntities] = useState<Entity[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [pagination, setPagination] = useState<{
    page: number;
    size: number;
    total: number;
    pages: number;
  } | null>(null);

  // Search and filter state
  const [searchQuery, setSearchQuery] = useState('');
  const [filters, setFilters] = useState<Record<string, any>>({});
  const [sortBy, setSortBy] = useState('name');
  const [sortOrder, setSortOrder] = useState<'asc' | 'desc'>('asc');

  // Current request parameters
  const currentParams = useMemo(() => ({
    entity_type: entityType,
    page: pagination?.page || initialPage,
    size: pageSize,
    sort_by: sortBy,
    sort_order: sortOrder,
    filters: Object.keys(filters).length > 0 ? filters : undefined
  }), [entityType, pagination?.page, initialPage, pageSize, sortBy, sortOrder, filters]);

  /**
   * Load entities with current parameters
   */
  const loadEntities = useCallback(async (params?: EntityListParams) => {
    try {
      setLoading(true);
      setError(null);

      const finalParams = { ...currentParams, ...params };
      const response = await listEntities(finalParams);
      
      setEntities(response.entities);
      setPagination(response.pagination);
    } catch (err) {
      const errorMessage = err instanceof Error ? err.message : 'Failed to load entities';
      setError(errorMessage);
      console.error('Error loading entities:', err);
    } finally {
      setLoading(false);
    }
  }, [currentParams]);

  /**
   * Search entities
   */
  const searchEntitiesAction = useCallback(async (query: string) => {
    if (!query.trim()) {
      // If query is empty, load normal entities
      setSearchQuery('');
      return loadEntities();
    }

    try {
      setLoading(true);
      setError(null);
      setSearchQuery(query);

      const searchParams = {
        query: query.trim(),
        entity_type: entityType,
        filters,
        sort_by: sortBy,
        page: 1,
        size: pageSize
      };

      const response = await searchEntities(searchParams);
      setEntities(response.entities);
      setPagination(response.pagination);
    } catch (err) {
      const errorMessage = err instanceof Error ? err.message : 'Search failed';
      setError(errorMessage);
      console.error('Error searching entities:', err);
    } finally {
      setLoading(false);
    }
  }, [entityType, filters, sortBy, pageSize, loadEntities]);

  /**
   * Create entity
   */
  const createEntityAction = useCallback(async (data: CreateEntityData): Promise<Entity | null> => {
    try {
      setLoading(true);
      setError(null);

      const newEntity = await createEntity(data);
      
      // Refresh the list to show the new entity
      await refresh();
      
      return newEntity;
    } catch (err) {
      const errorMessage = err instanceof Error ? err.message : 'Failed to create entity';
      setError(errorMessage);
      console.error('Error creating entity:', err);
      return null;
    } finally {
      setLoading(false);
    }
  }, []);

  /**
   * Update entity
   */
  const updateEntityAction = useCallback(async (id: string, data: UpdateEntityData): Promise<Entity | null> => {
    try {
      setLoading(true);
      setError(null);

      const updatedEntity = await updateEntity(id, data);
      
      // Update the entity in the local list
      setEntities(prev => prev.map(entity => 
        entity.id === id ? updatedEntity : entity
      ));
      
      return updatedEntity;
    } catch (err) {
      const errorMessage = err instanceof Error ? err.message : 'Failed to update entity';
      setError(errorMessage);
      console.error('Error updating entity:', err);
      return null;
    } finally {
      setLoading(false);
    }
  }, []);

  /**
   * Delete entity
   */
  const deleteEntityAction = useCallback(async (id: string, options?: DeleteEntityOptions): Promise<boolean> => {
    try {
      setLoading(true);
      setError(null);

      await deleteEntity(id, options);
      
      // Remove the entity from the local list
      setEntities(prev => prev.filter(entity => entity.id !== id));
      
      // Update pagination total
      if (pagination) {
        setPagination(prev => prev ? { ...prev, total: prev.total - 1 } : null);
      }
      
      return true;
    } catch (err) {
      const errorMessage = err instanceof Error ? err.message : 'Failed to delete entity';
      setError(errorMessage);
      console.error('Error deleting entity:', err);
      return false;
    } finally {
      setLoading(false);
    }
  }, [pagination]);

  /**
   * Pagination actions
   */
  const goToPage = useCallback(async (page: number) => {
    if (searchQuery.trim()) {
      await searchEntitiesAction(searchQuery);
    } else {
      await loadEntities({ page });
    }
  }, [searchQuery, searchEntitiesAction, loadEntities]);

  const nextPage = useCallback(async () => {
    if (pagination && pagination.page < pagination.pages) {
      await goToPage(pagination.page + 1);
    }
  }, [pagination, goToPage]);

  const prevPage = useCallback(async () => {
    if (pagination && pagination.page > 1) {
      await goToPage(pagination.page - 1);
    }
  }, [pagination, goToPage]);

  /**
   * Filter actions
   */
  const setFiltersAction = useCallback((newFilters: Record<string, any>) => {
    setFilters(newFilters);
    // Reset to first page when filters change
    if (pagination?.page !== 1) {
      setPagination(prev => prev ? { ...prev, page: 1 } : null);
    }
  }, [pagination]);

  const setSortByAction = useCallback((field: string) => {
    setSortBy(field);
    // Reset to first page when sort changes
    if (pagination?.page !== 1) {
      setPagination(prev => prev ? { ...prev, page: 1 } : null);
    }
  }, [pagination]);

  const setSortOrderAction = useCallback((order: 'asc' | 'desc') => {
    setSortOrder(order);
    // Reset to first page when sort order changes
    if (pagination?.page !== 1) {
      setPagination(prev => prev ? { ...prev, page: 1 } : null);
    }
  }, [pagination]);

  const clearFilters = useCallback(() => {
    setFilters({});
    setSearchQuery('');
    setSortBy('name');
    setSortOrder('asc');
  }, []);

  /**
   * Refresh current view
   */
  const refresh = useCallback(async () => {
    if (searchQuery.trim()) {
      await searchEntitiesAction(searchQuery);
    } else {
      await loadEntities();
    }
  }, [searchQuery, searchEntitiesAction, loadEntities]);

  // Auto-load on mount and when entity type changes
  useEffect(() => {
    if (!autoLoad || !entityType) return;

    // Use AbortController to prevent race conditions
    const abortController = new AbortController();

    const load = async () => {
      try {
        setLoading(true);
        setError(null);

        const params = {
          entity_type: entityType,
          page: initialPage,
          size: pageSize,
          sort_by: sortBy,
          sort_order: sortOrder,
          filters: Object.keys(filters).length > 0 ? filters : undefined
        };

        const response = await listEntities(params);

        // Only update state if not aborted
        if (!abortController.signal.aborted) {
          setEntities(response.entities);
          setPagination(response.pagination);
        }
      } catch (err) {
        if (!abortController.signal.aborted) {
          const errorMessage = err instanceof Error ? err.message : 'Failed to load entities';
          setError(errorMessage);
          console.error('Error loading entities:', err);
        }
      } finally {
        if (!abortController.signal.aborted) {
          setLoading(false);
        }
      }
    };

    load();

    return () => {
      abortController.abort();
    };
  }, [autoLoad, entityType, initialPage, pageSize, sortBy, sortOrder, filters]);

  return {
    // State
    entities,
    loading,
    error,
    pagination,
    
    // Search and filters
    searchQuery,
    filters,
    sortBy,
    sortOrder,
    
    // Actions
    loadEntities,
    searchEntitiesAction,
    createEntityAction,
    updateEntityAction,
    deleteEntityAction,
    
    // Filter and sort actions
    setSearchQuery,
    setFilters: setFiltersAction,
    setSortBy: setSortByAction,
    setSortOrder: setSortOrderAction,
    clearFilters,
    
    // Pagination
    goToPage,
    nextPage,
    prevPage,
    
    // Refresh
    refresh
  };
};