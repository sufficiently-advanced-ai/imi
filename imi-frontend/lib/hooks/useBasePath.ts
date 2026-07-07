"use client";

// Known app routes that are not instance prefixes
const APP_ROUTES = ['meetings', 'command', 'chat', 'explorer', 'signin', 'api', '_next', 'domain-graph', 'domain-graph-enhanced', 'entities'];

/**
 * Detects the instance base path from the current URL
 * For example, /my-instance/meetings -> /my-instance
 * Returns empty string if no instance prefix is detected
 */
export function detectInstancePath(): string {
  if (typeof window === 'undefined') {
    return '';
  }
  
  const pathSegments = window.location.pathname.split('/').filter(Boolean);
  
  // If we have path segments and the first one isn't a known app route,
  // it's likely the instance prefix (e.g., my-instance, example-demo)
  if (pathSegments.length > 0 && !APP_ROUTES.includes(pathSegments[0])) {
    return `/${pathSegments[0]}`;
  }
  
  return '';
}

/**
 * Hook to consistently retrieve the base path across the application
 * Handles both client-side and server-side rendering
 */
export function useBasePath(): string {
  // For dev builds, always return empty string (no basePath)
  // The app runs at root and nginx handles path stripping
  return process.env.NEXT_PUBLIC_BASE_PATH || '';
}

/**
 * Utility function to prepend base path to a URL or path
 * For dev builds without basePath, this returns the path unchanged
 */
export function withBasePath(path: string): string {
  // If path is external URL or absolute with domain, don't modify
  if (path.startsWith('http') || path.startsWith('//')) {
    return path;
  }
  
  const basePath = process.env.NEXT_PUBLIC_BASE_PATH || '';
  
  // If no base path (dev builds), return path as-is
  if (!basePath) {
    return path;
  }
  
  // For production builds with basePath
  if (path.startsWith(basePath + '/') || path === basePath) {
    return path;
  }
  
  // Ensure path starts with / for proper joining
  const normalizedPath = path.startsWith('/') ? path : `/${path}`;
  return `${basePath}${normalizedPath}`;
}

export default useBasePath;