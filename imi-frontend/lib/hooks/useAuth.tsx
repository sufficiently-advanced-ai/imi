'use client';

import { useCallback, useEffect, useRef, useState } from 'react';

interface User {
  id: string;
  email: string;
  name?: string;
  firstName?: string;
  lastName?: string;
  profilePictureUrl?: string;
}

// Poll every 4 minutes (240 000 ms) — safely before the ~5-min JWT expiry
const sessionPollIntervalMs = 4 * 60 * 1000;

/**
 * Client-side auth hook that fetches user data from the API
 * and keeps the session alive by periodically polling /auth/me.
 *
 * When the backend detects an expired JWT it transparently refreshes
 * the session and returns a Set-Cookie header with the new sealed
 * session, so the browser cookie stays up to date.
 */
export function useAuth() {
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<Error | null>(null);
  const intervalRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const abortRef = useRef<AbortController | null>(null);

  const fetchUser = useCallback(async (isInitial = false) => {
    const controller = new AbortController();
    abortRef.current = controller;
    try {
      const apiUrl = '/auth/me';

      if (isInitial && process.env.NODE_ENV === 'development') {
        console.log('[useAuth] Starting auth check', {
          url: apiUrl,
          pathname: window.location.pathname,
        });
      }

      const response = await fetch(apiUrl, {
        credentials: 'include', // Include cookies in the request
        signal: controller.signal,
      });

      if (isInitial && process.env.NODE_ENV === 'development') {
        console.log('[useAuth] Auth response received', {
          status: response.status,
          ok: response.ok,
        });
      }

      if (response.ok) {
        const userData = await response.json();
        if (controller.signal.aborted) {
          return;
        }
        if (isInitial && process.env.NODE_ENV === 'development') {
          console.log('[useAuth] User data received', {
            hasUser: !!userData,
            userId: userData?.id,
          });
        }
        // Transform backend user format to frontend format
        setUser({
          id: userData.id,
          email: userData.email,
          name:
            userData.first_name && userData.last_name
              ? `${userData.first_name} ${userData.last_name}`
              : userData.first_name || userData.last_name || undefined,
          firstName: userData.first_name,
          lastName: userData.last_name,
          profilePictureUrl: userData.profile_picture_url,
        });
        setError(null);
      } else if (response.status === 401) {
        console.log('[useAuth] Auth failed - 401 Unauthorized');
        setUser(null);
        // Stop polling — session is irrecoverably expired
        if (intervalRef.current) {
          clearInterval(intervalRef.current);
          intervalRef.current = null;
        }
        // Redirect to sign-in page
        window.location.href = '/signin';
      } else {
        throw new Error('Failed to fetch user');
      }
    } catch (err) {
      if (controller.signal.aborted) {
        return; // Unmounted mid-flight; don't touch state
      }
      console.error('[useAuth] Error fetching user', err);
      if (isInitial) {
        setError(err as Error);
      }
    } finally {
      if (isInitial && !controller.signal.aborted) {
        setLoading(false);
      }
    }
  }, []);

  useEffect(() => {
    // Initial fetch
    fetchUser(true);

    // Start polling to keep the session alive
    intervalRef.current = setInterval(() => {
      fetchUser(false);
    }, sessionPollIntervalMs);

    return () => {
      if (intervalRef.current) {
        clearInterval(intervalRef.current);
        intervalRef.current = null;
      }
      abortRef.current?.abort();
    };
  }, [fetchUser]);

  return {
    user,
    loading,
    error,
  };
}
