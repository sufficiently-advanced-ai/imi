/**
 * Entity Profile Hook
 *
 * Provides entity profile data fetching with loading and error states.
 * Used by the entity profile modal for drill-down navigation.
 */

import { useState, useEffect, useCallback, useRef } from 'react';
import {
  getEntityProfile,
  EntityProfileResponse,
} from '@/lib/api/entities';

export interface UseEntityProfileReturn {
  profile: EntityProfileResponse | null;
  loading: boolean;
  error: string | null;
  refetch: () => Promise<void>;
}

export const useEntityProfile = (entityId: string | null): UseEntityProfileReturn => {
  const [profile, setProfile] = useState<EntityProfileResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Track the current entity ID to prevent stale updates
  const currentEntityIdRef = useRef<string | null>(null);

  const fetchProfile = useCallback(async (id: string) => {
    if (!id) return;

    // Update the ref to track which entity we're fetching
    currentEntityIdRef.current = id;

    try {
      setLoading(true);
      setError(null);

      const data = await getEntityProfile(id);

      // Only update state if this is still the current entity
      if (currentEntityIdRef.current === id) {
        setProfile(data);
      }
    } catch (err) {
      // Only update error if this is still the current entity
      if (currentEntityIdRef.current === id) {
        const errorMessage = err instanceof Error ? err.message : 'Failed to load entity profile';
        setError(errorMessage);
        setProfile(null);
        console.error('Error loading entity profile:', err);
      }
    } finally {
      // Only update loading if this is still the current entity
      if (currentEntityIdRef.current === id) {
        setLoading(false);
      }
    }
  }, []);

  const refetch = useCallback(async () => {
    if (entityId) {
      await fetchProfile(entityId);
    }
  }, [entityId, fetchProfile]);

  // Fetch profile when entityId changes
  useEffect(() => {
    if (entityId) {
      // Clear stale profile when switching entities
      if (currentEntityIdRef.current !== entityId) {
        setProfile(null);
      }
      fetchProfile(entityId);
    } else {
      // Clear profile when entityId is null
      setProfile(null);
      setError(null);
      setLoading(false);
      currentEntityIdRef.current = null;
    }

    // Cleanup function to prevent stale updates
    return () => {
      currentEntityIdRef.current = null;
    };
  }, [entityId, fetchProfile]);

  return {
    profile,
    loading,
    error,
    refetch,
  };
};
