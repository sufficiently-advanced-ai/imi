/**
 * File content caching service for better performance
 */
import { KnowledgeFile } from '../api/knowledge';

// Cache TTL (in milliseconds) - 5 minutes
const CACHE_TTL = 5 * 60 * 1000;

// Maximum number of files to cache
const MAX_CACHE_SIZE = 50;

interface CacheEntry {
  file: KnowledgeFile;
  timestamp: number;
  lastAccessed: number;
}

class FileCache {
  private cache: Map<string, CacheEntry> = new Map();

  /**
   * Get a file from the cache
   * @param filePath Path to the file
   * @returns Cached file content or null if not cached/expired
   */
  getFile(filePath: string): KnowledgeFile | null {
    const entry = this.cache.get(filePath);
    
    if (!entry) {
      return null;
    }
    
    const now = Date.now();
    
    // Check if cache entry is expired
    if (now - entry.timestamp > CACHE_TTL) {
      this.cache.delete(filePath);
      return null;
    }
    
    // Update last accessed time for LRU eviction
    entry.lastAccessed = now;
    
    return entry.file;
  }

  /**
   * Add a file to the cache
   * @param file File to cache
   */
  addFile(file: KnowledgeFile): void {
    const now = Date.now();
    
    // Add to cache
    this.cache.set(file.path, {
      file,
      timestamp: now,
      lastAccessed: now
    });
    
    // If cache is too large, evict least recently used entries
    if (this.cache.size > MAX_CACHE_SIZE) {
      this.evictLRU();
    }
  }

  /**
   * Clear the entire cache
   */
  clear(): void {
    this.cache.clear();
  }

  /**
   * Remove a file from the cache
   * @param filePath Path to remove
   */
  remove(filePath: string): void {
    this.cache.delete(filePath);
  }

  /**
   * Evict least recently used entries from the cache
   */
  private evictLRU(): void {
    // Convert to array for sorting
    const entries = Array.from(this.cache.entries());
    
    // Sort by last accessed time (oldest first)
    entries.sort((a, b) => a[1].lastAccessed - b[1].lastAccessed);
    
    // Remove the oldest 20% of entries
    const entriesToRemove = Math.ceil(this.cache.size * 0.2);
    for (let i = 0; i < entriesToRemove && i < entries.length; i++) {
      this.cache.delete(entries[i][0]);
    }
  }
}

// Export a single instance to share across the application
export const fileCache = new FileCache();