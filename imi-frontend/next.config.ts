import type { NextConfig } from "next";

// For development builds with demo auth, we don't use basePath
// Production builds can still set NEXT_PUBLIC_BASE_PATH if needed
const basePath = process.env.NEXT_PUBLIC_BASE_PATH || '';
const apiUrl = process.env.NEXT_PUBLIC_API_URL || '/api';
const authMode = process.env.NEXT_PUBLIC_AUTH_MODE || 'none';

const nextConfig: NextConfig = {
  // Only set basePath if explicitly provided (production with subdirectory)
  // Dev builds run at root without basePath
  ...(basePath ? { basePath: basePath, assetPrefix: basePath } : {}),
  reactStrictMode: true,
  trailingSlash: true,
  images: {
    unoptimized: true
  },
  eslint: {
    ignoreDuringBuilds: true,
  },
  typescript: {
    ignoreBuildErrors: true,
  },
  // Generate unique build IDs to prevent caching issues
  generateBuildId: async () => {
    // Use timestamp to ensure unique build IDs
    return Date.now().toString();
  },
  // Make environment variables available to the browser
  env: {
    NEXT_PUBLIC_BASE_PATH: basePath,
    NEXT_PUBLIC_API_URL: apiUrl,
    NEXT_PUBLIC_AUTH_MODE: authMode,
  },
};

export default nextConfig;