/**
 * Knowledge API client for fetching and manipulating knowledge files
 */
import { getApiUrl } from '@/lib/config';

// Define response types
export interface KnowledgeFile {
  path: string;
  content: string;
  metadata?: {
    title?: string;
    tags?: string[];
    author?: string;
    created?: string;
    updated?: string;
    [key: string]: string | string[] | number | boolean | null | undefined;
  };
  lastModified?: string;
  size?: number;
}

export interface KnowledgeResponse {
  files: KnowledgeFile[];
  total?: number;
  page?: number;
  limit?: number;
}

// Direct fetch helper to ensure relative URLs with base path
const fetchAPI = async (url: string, options?: RequestInit) => {
  console.log(`Fetching from: ${url}`);
  try {
    const response = await fetch(url, {
      ...options,
      credentials: 'include' // Include cookies for authentication
    });
    if (!response.ok) {
      throw new Error(`API request failed: ${response.statusText} (${response.status})`);
    }
    return await response.json();
  } catch (error) {
    console.error(`Error fetching from ${url}:`, error);
    throw error;
  }
};

export interface GetKnowledgeFilesOptions {
  includePaths?: string[];
  includeMetadata?: boolean;
  page?: number;
  limit?: number;
  sortBy?: 'path' | 'modified_at' | 'created_at' | 'size';
  sortOrder?: 'asc' | 'desc';
  includeContent?: boolean;
}

export interface PaginatedKnowledgeResult {
  files: KnowledgeFile[];
  total: number;
  page: number;
  limit: number;
  hasNextPage: boolean;
}

/**
 * Fetch knowledge files from the backend with pagination
 * @param options Fetch options including pagination, sorting and filtering
 */
export async function getKnowledgeFiles(options: GetKnowledgeFilesOptions = {}): Promise<PaginatedKnowledgeResult> {
  try {
    const {
      includePaths,
      page = 0,
      limit = 50,
      sortBy = 'path',
      sortOrder = 'asc',
      includeContent = true
    } = options;
    
    const queryParams = new URLSearchParams();
    
    // Add pagination parameters
    queryParams.append('page', page.toString());
    queryParams.append('limit', limit.toString());
    
    // Add sorting parameters
    queryParams.append('sort_by', sortBy);
    queryParams.append('sort_order', sortOrder);
    
    // Content inclusion flag
    queryParams.append('include_content', includeContent.toString());
    
    // Metadata inclusion flag (always include metadata in this implementation)
    queryParams.append('include_metadata', 'true');
    
    // Add specific paths if provided
    if (includePaths && includePaths.length > 0) {
      includePaths.forEach(path => queryParams.append('paths', path));
    }
    
    // Use getApiUrl to construct proper URL with base path
    const apiUrl = getApiUrl(`/knowledge?${queryParams.toString()}`);
    console.log(`Fetching knowledge files from: ${apiUrl}`);
    
    // Use AbortController for timeout
    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), 15000); // 15 second timeout
    
    try {
      const response = await fetch(apiUrl, {
        signal: controller.signal,
        headers: {
          'Accept': 'application/json',
          'Cache-Control': 'max-age=300' // Suggest caching for 5 minutes
        },
        credentials: 'include' // Include cookies for authentication
      });
      
      clearTimeout(timeoutId);
      
      if (!response.ok) {
        throw new Error(`API request failed: ${response.statusText} (${response.status})`);
      }
      
      const data: KnowledgeResponse = await response.json();
      
      // Format and enhance the files with additional properties
      const formattedFiles = data.files.map(file => ({
        ...file,
        lastModified: file.metadata?.updated || new Date().toISOString(),
        size: file.content ? file.content.length : 0,
      }));
      
      return {
        files: formattedFiles,
        total: data.total || formattedFiles.length,
        page: data.page || 0,
        limit: data.limit || limit,
        hasNextPage: data.total ? (data.page + 1) * data.limit < data.total : false
      };
    } catch (error) {
      clearTimeout(timeoutId);
      throw error;
    }
  } catch (error) {
    console.error('Error fetching knowledge files:', error);
    throw error;
  }
}

/**
 * Fetch a single knowledge file's content with improved caching
 * @param filePath Path to the file to fetch
 */
export async function getFileContent(filePath: string): Promise<KnowledgeFile | null> {
  try {
    // Import the file cache (dynamic import to avoid circular dependencies)
    const { fileCache } = await import('../services/file-cache');
    
    // Check if file is cached first
    const cachedFile = fileCache.getFile(filePath);
    if (cachedFile) {
      console.log(`[getFileContent] Using cached file: ${filePath}`);
      return cachedFile;
    }
    
    console.log(`[getFileContent] Fetching content for: ${filePath}`);

    // Use the optimized paginated knowledge files function
    const result = await getKnowledgeFiles({
      includePaths: [filePath],
      includeMetadata: true,
      includeContent: true,
      limit: 1
    });
    
    if (!result.files || result.files.length === 0) {
      console.warn(`[getFileContent] No files returned for path: ${filePath}`);
      return null;
    }

    // Make sure we're getting the right file by checking the path
    const matchingFile = result.files.find(file => file.path === filePath);

    if (!matchingFile) {
      console.warn(`[getFileContent] File path mismatch. Requested: ${filePath}, Received: ${result.files.map(f => f.path).join(', ')}`);
      // Fall back to first file if no exact match (for debugging)
      return result.files[0];
    }
    
    // Add to cache
    fileCache.addFile(matchingFile);

    console.log(`[getFileContent] Successfully loaded file: ${matchingFile.path}`);
    return matchingFile;
  } catch (error) {
    console.error('[getFileContent] Error fetching file content:', error);
    return null;
  }
}

/**
 * Batch fetch metadata for multiple files
 * @param filePaths Array of file paths to fetch metadata for
 */
export async function getFilesMetadata(filePaths: string[]): Promise<{[path: string]: any}> {
  if (!filePaths || filePaths.length === 0) {
    return {};
  }
  
  try {
    console.log(`[getFilesMetadata] Fetching metadata for ${filePaths.length} files`);
    
    // Use the optimized batch metadata endpoint
    const apiUrl = getApiUrl('/documents/metadata/batch');
    
    const response = await fetch(apiUrl, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Accept': 'application/json'
      },
      body: JSON.stringify({ paths: filePaths }),
      credentials: 'include' // Include cookies for authentication
    });
    
    if (!response.ok) {
      throw new Error(`API request failed: ${response.statusText} (${response.status})`);
    }
    
    const data = await response.json();
    
    // Convert array of metadata to map by path
    const metadataMap: {[path: string]: any} = {};
    if (data.items && Array.isArray(data.items)) {
      data.items.forEach((item: any) => {
        if (item.path && item.metadata) {
          metadataMap[item.path] = item.metadata;
        }
      });
    }
    
    console.log(`[getFilesMetadata] Successfully fetched metadata for ${Object.keys(metadataMap).length} files`);
    return metadataMap;
  } catch (error) {
    console.error('[getFilesMetadata] Error fetching files metadata:', error);
    return {};
  }
}

/**
 * Search knowledge files by content
 * @param query Search query string
 */
export async function searchKnowledgeFiles(query: string): Promise<KnowledgeFile[]> {
  try {
    // Use getApiUrl to construct proper URL with base path
    const apiUrl = getApiUrl(`/knowledge/search?q=${encodeURIComponent(query)}`);
    const data = await fetchAPI(apiUrl);
    return data.results || [];
  } catch (error) {
    console.error('Error searching knowledge files:', error);
    throw error;
  }
}