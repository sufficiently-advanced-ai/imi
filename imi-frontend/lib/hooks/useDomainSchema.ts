/**
 * Domain Schema Hook
 * 
 * Provides access to domain schema data with caching and loading states.
 * Integrates with the DomainContext for domain switching.
 */

import { useState, useEffect, useCallback } from 'react';
import { getDomainSchema, clearSchemaCache, DomainSchema } from '@/lib/api/entities';
import { useDomain } from '@/contexts/DomainContext';

export interface UseDomainSchemaReturn {
  // Schema data
  schema: DomainSchema | null;
  
  // Loading states
  loading: boolean;
  error: string | null;
  
  // Actions
  refreshSchema: () => Promise<void>;
  clearCache: () => void;
  
  // Computed properties
  entityTypes: string[];
  getEntitySchema: (entityType: string) => any;
  getEntityAttributes: (entityType: string) => Record<string, any>;
  getEntityRelationships: (entityType: string) => Record<string, any>;
}

export const useDomainSchema = (): UseDomainSchemaReturn => {
  const { currentDomain, domainConfig } = useDomain();
  const [schema, setSchema] = useState<DomainSchema | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  /**
   * Load schema from API
   */
  const loadSchema = useCallback(async () => {
    if (!currentDomain) return;

    try {
      setLoading(true);
      setError(null);

      const schemaData = await getDomainSchema(currentDomain);
      setSchema(schemaData);
    } catch (err) {
      const errorMessage = err instanceof Error ? err.message : 'Failed to load domain schema';
      setError(errorMessage);
      console.error('Error loading domain schema:', err);
    } finally {
      setLoading(false);
    }
  }, [currentDomain]);

  /**
   * Refresh schema (bypass cache)
   */
  const refreshSchema = useCallback(async () => {
    clearSchemaCache();
    await loadSchema();
  }, [loadSchema]);

  /**
   * Clear schema cache
   */
  const clearCache = useCallback(() => {
    clearSchemaCache();
  }, []);

  // Load schema when domain changes
  useEffect(() => {
    if (currentDomain) {
      // If we have domainConfig from context, use it
      if (domainConfig) {
        setSchema({
          domain_id: domainConfig.id,
          entities: domainConfig.entities || {}
        });
      } else {
        // Otherwise load from API
        loadSchema();
      }
    } else {
      setSchema(null);
    }
  }, [currentDomain, domainConfig, loadSchema]);

  // Computed properties
  const entityTypes = schema?.entities ? Object.keys(schema.entities) : [];

  const getEntitySchema = useCallback((entityType: string) => {
    return schema?.entities?.[entityType] || null;
  }, [schema]);

  const getEntityAttributes = useCallback((entityType: string): Record<string, any> => {
    return schema?.entities?.[entityType]?.attributes || {};
  }, [schema]);

  const getEntityRelationships = useCallback((entityType: string): Record<string, any> => {
    return schema?.entities?.[entityType]?.relationships || {};
  }, [schema]);

  return {
    // Schema data
    schema,
    
    // Loading states
    loading,
    error,
    
    // Actions
    refreshSchema,
    clearCache,
    
    // Computed properties
    entityTypes,
    getEntitySchema,
    getEntityAttributes,
    getEntityRelationships
  };
};