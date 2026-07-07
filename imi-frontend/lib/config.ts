/**
 * Application configuration and environment variables
 */

// Get environment variables with fallbacks
export const config = {
  // Base path for Next.js routing (e.g., /my-kb)
  basePath: process.env.NEXT_PUBLIC_BASE_PATH || '',
  
  // API URL with proper base path handling
  apiUrl: process.env.NEXT_PUBLIC_API_URL || '/api',
};

/**
 * Constructs a full API URL with the correct base path
 * @param path The API endpoint path (e.g., /folders, /knowledge)
 * @returns The full API URL with proper base path
 */
export function getApiUrl(path: string): string {
  // Ensure path starts with a slash
  const normalizedPath = path.startsWith('/') ? path : `/${path}`;
  
  console.log('[getApiUrl] Input path:', path);
  console.log('[getApiUrl] Normalized path:', normalizedPath);
  console.log('[getApiUrl] config.apiUrl:', config.apiUrl);
  console.log('[getApiUrl] config.basePath:', config.basePath);
  
  // If API URL is already absolute, use it directly
  if (config.apiUrl.startsWith('http')) {
    const result = `${config.apiUrl}${normalizedPath}`;
    console.log('[getApiUrl] Absolute URL result:', result);
    return result;
  }
  
  // For dev builds without basePath (monocontainer mode):
  // We're running at root, but nginx adds /feature prefix via sub_filter
  // So we should always return paths starting with /api
  // The nginx sub_filter will rewrite them to /feature/api
  
  // Simply return /api + path
  // This will become /api/entities/list which nginx will rewrite to /feature/api/entities/list
  const result = `${config.apiUrl}${normalizedPath}`;
  console.log('[getApiUrl] Final result:', result);
  return result;
}