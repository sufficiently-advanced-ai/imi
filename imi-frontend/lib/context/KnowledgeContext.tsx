'use client';

import { createContext, useContext, useState, ReactNode, useCallback, useRef } from 'react';
import { KnowledgeFile, getKnowledgeFiles } from '../api/knowledge';

interface KnowledgeContextType {
  // State
  files: KnowledgeFile[];
  selectedFiles: string[];
  isLoadingFiles: boolean;
  error: string | null;
  totalFiles: number;
  
  // Actions
  loadFiles: () => Promise<void>;
  toggleFileSelection: (path: string) => void;
  isFileSelected: (path: string) => boolean;
  clearSelection: () => void;
}

const KnowledgeContext = createContext<KnowledgeContextType>({
  // Default values
  files: [],
  selectedFiles: [],
  isLoadingFiles: false,
  error: null,
  totalFiles: 0,
  
  loadFiles: async () => {},
  toggleFileSelection: () => {},
  isFileSelected: () => false,
  clearSelection: () => {},
});

// Cache time in milliseconds (5 minutes)
const CACHE_TTL = 5 * 60 * 1000;

export function KnowledgeProvider({ children }: { children: ReactNode }) {
  // State
  const [files, setFiles] = useState<KnowledgeFile[]>([]);
  const [selectedFiles, setSelectedFiles] = useState<string[]>([]);
  const [isLoadingFiles, setIsLoadingFiles] = useState<boolean>(false);
  const [error, setError] = useState<string | null>(null);
  const [totalFiles, setTotalFiles] = useState<number>(0);
  
  // Use a ref for tracking last load time to avoid redundant fetches
  const lastLoadRef = useRef<number>(0);
  
  // Track if initial load has happened
  const initialLoadRef = useRef<boolean>(false);
  
  // Track authentication failures
  const authFailureRef = useRef<number>(0);

  // Load files from the API with caching strategy
  const loadFiles = useCallback(async (forceRefresh = false) => {
    // Check if we've had too many auth failures
    if (authFailureRef.current >= 3) {
      console.log('Too many authentication failures - stopping knowledge load attempts');
      return;
    }
    
    // Check if we should use cached data
    const now = Date.now();
    const timeSinceLastLoad = now - lastLoadRef.current;
    
    // Use cache if it's not a forced refresh and we're within cache TTL
    if (!forceRefresh && lastLoadRef.current && timeSinceLastLoad < CACHE_TTL) {
      console.log(`Using cached files list (${timeSinceLastLoad}ms since last load)`);
      return;
    }
    
    // If we're already loading, don't start another load
    if (isLoadingFiles) {
      return;
    }
    
    setIsLoadingFiles(true);
    setError(null);
    
    // For better UX, keep showing the old files while loading
    // instead of showing an empty state
    
    try {
      console.log('Fetching knowledge files from API');
      const result = await getKnowledgeFiles({ limit: 1000 }); // Load more files initially
      
      // Update last load timestamp
      lastLoadRef.current = now;
      initialLoadRef.current = true;
      
      setFiles(result.files);
      setTotalFiles(result.total);
    } catch (err) {
      console.error('Error loading knowledge files:', err);
      
      // Check if it's an authentication error
      if (err instanceof Error && (err.message.includes('401') || err.message.includes('Unauthorized'))) {
        // Don't set error state for auth errors to prevent loops
        console.log('Authentication required - stopping knowledge load to prevent retry spam');
        // Increment auth failure counter
        authFailureRef.current++;
        // Set a flag to prevent further attempts
        lastLoadRef.current = Date.now() + CACHE_TTL; // Prevent retries for cache duration
      } else {
        setError(err instanceof Error ? err.message : 'Failed to load knowledge files');
      }
    } finally {
      setIsLoadingFiles(false);
    }
  }, [isLoadingFiles]);

  // Toggle file selection
  const toggleFileSelection = useCallback((path: string) => {
    setSelectedFiles(prev => {
      if (prev.includes(path)) {
        return prev.filter(p => p !== path);
      } else {
        return [...prev, path];
      }
    });
  }, []);

  // Check if a file is selected
  const isFileSelected = useCallback((path: string) => {
    return selectedFiles.includes(path);
  }, [selectedFiles]);

  // Clear all selections
  const clearSelection = useCallback(() => {
    setSelectedFiles([]);
  }, []);

  // Simplified initial mount - removed conditional path checking that caused bugs
  // Components can call loadFiles() when they actually need the data
  // This prevents race conditions and "no files" errors on first load

  return (
    <KnowledgeContext.Provider
      value={{
        files,
        selectedFiles,
        isLoadingFiles,
        error,
        totalFiles,
        loadFiles,
        toggleFileSelection,
        isFileSelected,
        clearSelection,
      }}
    >
      {children}
    </KnowledgeContext.Provider>
  );
}

export const useKnowledge = () => useContext(KnowledgeContext);