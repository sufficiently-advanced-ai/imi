"use client";

import Link from 'next/link';
import { useEffect, useState } from 'react';

/**
 * Known application routes that appear after the base path. Shared with
 * components/navigation.tsx so base-path detection stays consistent across
 * the Link wrapper and the nav. Keep in sync with the app's top-level routes.
 */
export const KNOWN_ROUTES = [
  '/feed', '/command', '/chat', '/explorer',
  '/domain-graph-enhanced', '/entities', '/signin', '/api', '/_next',
  '/decisions', '/profile', '/overview',
];

/**
 * Detects the base path from the current URL at runtime.
 * This allows the app to work correctly whether served at root (/)
 * or under a subpath (/my-instance, /example-demo, etc.)
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
 * Enhanced Link component that automatically detects and prepends the base path.
 * This eliminates the need for nginx sub_filter URL rewrites.
 */
export function BasePathLink({
  href,
  children,
  ...props
}: React.ComponentPropsWithoutRef<typeof Link>) {
  const [basePath, setBasePath] = useState('');
  
  useEffect(() => {
    // Detect base path once on mount
    const detected = detectBasePath();
    setBasePath(detected);
  }, []);
  
  // Handle different href types
  let fullHref = href;
  
  if (typeof href === 'string') {
    // Only prepend base path to absolute internal URLs
    if (href.startsWith('/') && !href.startsWith('//')) {
      // Check if basePath is already included to avoid duplication
      if (basePath && !href.startsWith(basePath)) {
        fullHref = basePath + href;
      }
    }
    // Leave external URLs, relative URLs, and anchors unchanged
  }
  
  return (
    <Link href={fullHref} {...props}>
      {children}
    </Link>
  );
}

export default BasePathLink;