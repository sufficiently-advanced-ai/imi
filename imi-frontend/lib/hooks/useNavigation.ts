"use client";

import { useRouter } from 'next/navigation';
import { useCallback, useEffect, useState } from 'react';
import { KNOWN_ROUTES } from '@/lib/utils/links';

/**
 * Detects the base path from the current URL at runtime.
 * Uses the single shared KNOWN_ROUTES list from lib/utils/links.tsx so the
 * route set stays in one place (importing a plain const array carries no
 * circular-dependency risk).
 */
function detectBasePath(): string {
  if (typeof window === 'undefined') return '';

  const pathname = window.location.pathname;

  // Find the first known route in the pathname
  for (const route of KNOWN_ROUTES) {
    const index = pathname.indexOf(route);
    if (index > 0) {
      // Extract everything before the known route as the base path
      // Remove trailing slash if present
      return pathname.substring(0, index).replace(/\/$/, '');
    }
  }

  // If we're at the root of a subpath (e.g., /my-instance/),
  // and it doesn't match any known routes, check if we have a single segment
  const segments = pathname.split('/').filter(Boolean);
  if (segments.length === 1 && !KNOWN_ROUTES.some(r => r.startsWith('/' + segments[0]))) {
    return '/' + segments[0];
  }

  return '';
}

/**
 * Hook that provides navigation functions with automatic instance path detection.
 * This ensures programmatic navigation works correctly when the app is served
 * under a subpath (e.g., /feature, /my-instance, /example-demo).
 * 
 * @example
 * ```tsx
 * const { navigate, replace } = useNavigation();
 * 
 * // Navigate to a new page
 * navigate('/entities/new'); // Navigates to /feature/entities/new
 * 
 * // Replace current history entry
 * replace('/entities'); // Replaces with /feature/entities
 * ```
 */
export function useNavigation() {
  const router = useRouter();
  const [basePath, setBasePath] = useState('');
  
  useEffect(() => {
    // Detect base path once on mount
    const detected = detectBasePath();
    setBasePath(detected);
  }, []);
  
  /**
   * Navigate to a path with the instance prefix automatically prepended.
   * @param path - The path to navigate to (e.g., '/entities/new')
   */
  const navigate = useCallback((path: string) => {
    // Only prepend base path to absolute internal URLs
    if (path.startsWith('/') && !path.startsWith('//')) {
      // Check if basePath is already included to avoid duplication
      if (basePath && !path.startsWith(basePath)) {
        router.push(basePath + path);
        return;
      }
    }
    // For external URLs, relative URLs, or paths that already have the base path
    router.push(path);
  }, [router, basePath]);
  
  /**
   * Replace current history entry with a new path.
   * @param path - The path to replace with (e.g., '/entities')
   */
  const replace = useCallback((path: string) => {
    // Only prepend base path to absolute internal URLs
    if (path.startsWith('/') && !path.startsWith('//')) {
      // Check if basePath is already included to avoid duplication
      if (basePath && !path.startsWith(basePath)) {
        router.replace(basePath + path);
        return;
      }
    }
    // For external URLs, relative URLs, or paths that already have the base path
    router.replace(path);
  }, [router, basePath]);
  
  /**
   * Navigate back in history.
   */
  const back = useCallback(() => {
    router.back();
  }, [router]);
  
  /**
   * Refresh the current page.
   */
  const refresh = useCallback(() => {
    router.refresh();
  }, [router]);
  
  return {
    navigate,
    replace,
    back,
    refresh,
    basePath
  };
}

export default useNavigation;