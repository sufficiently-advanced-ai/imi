/**
 * Entity Management API Client
 * 
 * Provides comprehensive API client functions for the domain-aware entity management system.
 * Supports full CRUD operations, search, relationships, and validation with proper error handling and caching.
 */

import { fetcher } from './index';

// Types for API responses and entities
export interface Entity {
  id: string;
  entity_type: string;
  attributes: Record<string, any>;
  relationships?: Record<string, string | string[]>;
  created_at?: string;
  updated_at?: string;
}

export interface EntityListResponse {
  entities: Entity[];
  pagination: {
    page: number;
    size: number;
    total: number;
    pages: number;
  };
}

export interface SearchResponse extends EntityListResponse {
  query_analysis?: {
    terms: string[];
    filters_applied: string[];
    execution_time_ms: number;
  };
}

export interface EntityListParams {
  page?: number;
  size?: number;
  entity_type?: string;
  sort_by?: string;
  sort_order?: 'asc' | 'desc';
  status?: string;
  filters?: Record<string, any>;
}

export interface CreateEntityData {
  entity_type: string;
  attributes: Record<string, any>;
  relationships?: Record<string, string | string[]>;
}

export interface UpdateEntityData {
  attributes?: Record<string, any>;
  relationships?: Record<string, string | string[]>;
}

export interface DeleteEntityOptions {
  reason?: string;
  handle_relationships?: 'preserve' | 'cascade' | 'remove';
}

export interface SearchParams {
  query: string;
  entity_type?: string;
  filters?: Record<string, any>;
  sort_by?: string;
  page?: number;
  size?: number;
}

export interface DomainSchema {
  domain_id: string;
  entities: Record<string, {
    name: string;
    label?: string;
    plural?: string;
    plural_label?: string;
    icon?: string;
    description?: string;
    attributes: Record<string, {
      type: 'string' | 'number' | 'date' | 'datetime' | 'boolean' | 'enum';
      required: boolean;
      values?: string[];
    }>;
    relationships: Record<string, {
      target: string;
      cardinality: 'one-to-one' | 'one-to-many' | 'many-to-one' | 'many-to-many';
    }>;
  }>;
}

export interface ValidationResult {
  valid: boolean;
  errors: Array<{
    field: string;
    message: string;
  }>;
  warnings?: Array<{
    field: string;
    message: string;
  }>;
}

// Entity Profile Types (for the entity profile modal)
export interface EntityStatistics {
  total_mentions: number;
  recent_mentions: number;
  document_count: number;
  activity_count: number;
  relationship_count: number;
  last_activity: string | null;
}

export interface EntityActivity {
  id: string;
  type: 'mention' | 'update' | 'decision' | 'action_item' | 'meeting';
  description: string;
  timestamp: string;
  source?: string;
  document_id?: string;
}

export interface EntityRelationship {
  entity_id: string;
  entity_name: string;
  entity_type: string;
  relationship_type: string;
  strength: number; // 0-1 indicating relationship strength
}

export interface EntityInsight {
  id: string;
  title: string;
  content: string;
  confidence: number;
  category: 'pattern' | 'trend' | 'risk' | 'opportunity';
}

export interface EntityProfileResponse {
  entity: Entity;
  statistics: EntityStatistics;
  recent_activity: EntityActivity[];
  top_relationships: EntityRelationship[];
  insights: EntityInsight[];
  narrative_profile?: string;
}

// Cache for domain schema
let schemaCache: { [domain: string]: DomainSchema } = {};
const RETRY_COUNT = 3;
const RETRY_DELAY = 1000;

/**
 * Generic retry logic for API calls
 */
const retryWithDelay = async <T>(
  fn: () => Promise<T>,
  retries: number = RETRY_COUNT,
  delay: number = RETRY_DELAY
): Promise<T> => {
  try {
    return await fn();
  } catch (error) {
    if (retries > 0 && !(error as any).status) {
      // Only retry network errors, not HTTP errors
      await new Promise(resolve => setTimeout(resolve, delay));
      return retryWithDelay(fn, retries - 1, delay);
    }
    throw error;
  }
};

/**
 * Build query string from parameters
 */
const buildQueryString = (params: Record<string, any>): string => {
  const searchParams = new URLSearchParams();
  
  Object.entries(params).forEach(([key, value]) => {
    if (value !== undefined && value !== null && value !== '') {
      if (key === 'filters' && typeof value === 'object') {
        // Flatten filters into query parameters
        Object.entries(value).forEach(([filterKey, filterValue]) => {
          if (filterValue !== undefined && filterValue !== null && filterValue !== '') {
            searchParams.append(filterKey, String(filterValue));
          }
        });
      } else {
        searchParams.append(key, String(value));
      }
    }
  });
  
  return searchParams.toString();
};

/**
 * List entities with pagination and filtering
 */
export const listEntities = async (params: EntityListParams = {}): Promise<EntityListResponse> => {
  const queryParams = {
    page: params.page ?? 1,
    size: params.size ?? 25,
    ...params
  };
  
  const queryString = buildQueryString(queryParams);
  
  return retryWithDelay(async () => {
    const response = await fetcher(`/entities/list?${queryString}`, {
      method: 'GET',
      headers: {
        'Content-Type': 'application/json'
      }
    });
    
    if (!response.entities || !response.pagination) {
      throw new Error('Invalid response format');
    }
    
    return response;
  });
};

/**
 * Create a new entity
 */
export const createEntity = async (entityData: CreateEntityData): Promise<Entity> => {
  try {
    const response = await fetcher('/entities/create', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json'
      },
      body: JSON.stringify(entityData)
    });
    
    return response;
  } catch (error: any) {
    if (error.status === 400) {
      throw new Error(error.data?.error || 'Validation failed');
    }
    throw error;
  }
};

/**
 * Get a single entity by ID
 */
export const getEntity = async (entityId: string): Promise<Entity> => {
  try {
    const response = await fetcher(`/entities/${entityId}`, {
      method: 'GET',
      headers: {
        'Content-Type': 'application/json'
      }
    });
    
    return response;
  } catch (error: any) {
    if (error.status === 404) {
      throw new Error('Entity not found');
    }
    throw error;
  }
};

/**
 * Update an entity
 */
export const updateEntity = async (entityId: string, updateData: UpdateEntityData): Promise<Entity> => {
  try {
    const response = await fetcher(`/entities/${entityId}`, {
      method: 'PUT',
      headers: {
        'Content-Type': 'application/json'
      },
      body: JSON.stringify(updateData)
    });
    
    return response;
  } catch (error: any) {
    if (error.status === 400) {
      throw new Error('Update failed');
    }
    throw error;
  }
};

/**
 * Delete an entity
 */
export const deleteEntity = async (entityId: string, options?: DeleteEntityOptions): Promise<{ success: boolean }> => {
  try {
    const requestOptions: RequestInit = {
      method: 'DELETE',
      headers: {
        'Content-Type': 'application/json'
      }
    };
    
    if (options) {
      requestOptions.body = JSON.stringify(options);
    }
    
    const response = await fetcher(`/entities/${entityId}`, requestOptions);
    return response;
  } catch (error: any) {
    if (error.status === 409) {
      throw new Error(error.data?.error || 'Cannot delete entity with dependencies');
    }
    throw error;
  }
};

/**
 * Search entities with full-text search
 */
export const searchEntities = async (params: SearchParams): Promise<SearchResponse> => {
  const queryString = buildQueryString(params);
  
  try {
    const response = await fetcher(`/entities/search?${queryString}`, {
      method: 'GET',
      headers: {
        'Content-Type': 'application/json'
      }
    });
    
    return response;
  } catch {
    throw new Error('Failed to search entities');
  }
};

/**
 * Add a relationship between entities
 */
export const addRelationship = async (
  entityId: string,
  relationshipType: string,
  targetEntityId: string
): Promise<{ success: boolean }> => {
  try {
    const response = await fetcher(`/entities/${entityId}/relationships`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json'
      },
      body: JSON.stringify({
        relationship_type: relationshipType,
        target_entity_id: targetEntityId
      })
    });
    
    return response;
  } catch (error: any) {
    if (error.status === 400) {
      throw new Error(error.data?.error || 'Cardinality constraint violation');
    }
    throw error;
  }
};

/**
 * Remove a relationship between entities
 */
export const removeRelationship = async (
  entityId: string,
  relationshipType: string,
  targetEntityId: string
): Promise<{ success: boolean }> => {
  const response = await fetcher(`/entities/${entityId}/relationships/${relationshipType}/${targetEntityId}`, {
    method: 'DELETE',
    headers: {
      'Content-Type': 'application/json'
    }
  });
  
  return response;
};

/**
 * Get domain schema with caching
 */
export const getDomainSchema = async (domainId?: string): Promise<DomainSchema> => {
  const cacheKey = domainId || 'current';
  
  // Return cached schema if available
  if (schemaCache[cacheKey]) {
    return schemaCache[cacheKey];
  }
  
  const response = await fetcher('/entities/schema', {
    method: 'GET',
    headers: {
      'Content-Type': 'application/json'
    }
  });
  
  // Cache the schema
  schemaCache[cacheKey] = response;
  
  return response;
};

/**
 * Validate entity data against schema
 */
export const validateEntity = async (entityData: CreateEntityData | UpdateEntityData): Promise<ValidationResult> => {
  const response = await fetcher('/entities/validate', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json'
    },
    body: JSON.stringify(entityData)
  });
  
  return response;
};

/**
 * Clear schema cache (useful when domain changes)
 */
export const clearSchemaCache = () => {
  schemaCache = {};
};

/**
 * Get entity profile with statistics, relationships, and activity
 * Used by the entity profile modal
 */
export const getEntityProfile = async (entityId: string): Promise<EntityProfileResponse> => {
  try {
    // Use retryWithDelay for resilience to transient network failures
    const response = await retryWithDelay(async () => {
      return fetcher(`/entities/${entityId}/profile`, {
        method: 'GET',
        headers: {
          'Content-Type': 'application/json'
        }
      });
    });

    return response;
  } catch (error: any) {
    if (error.status === 404) {
      throw new Error('Entity not found');
    }
    throw error;
  }
};