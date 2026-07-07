import { type ClassValue, clsx } from "clsx";
import { twMerge } from "tailwind-merge";

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

/**
 * Creates a debounced function that delays invoking the provided function
 * until after the specified wait time has elapsed since the last time it was invoked.
 * 
 * @param func The function to debounce
 * @param wait The number of milliseconds to delay
 * @returns A debounced version of the original function
 */
export function debounce<T extends (...args: any[]) => any>(
  func: T,
  wait: number
): (...args: Parameters<T>) => void {
  let timeout: NodeJS.Timeout | null = null;
  
  return function(...args: Parameters<T>): void {
    const later = () => {
      timeout = null;
      func(...args);
    };
    
    if (timeout !== null) {
      clearTimeout(timeout);
    }
    
    timeout = setTimeout(later, wait);
  };
}

/**
 * Throttles a function to ensure it's not called more frequently than the specified limit
 * 
 * @param func The function to throttle
 * @param limit The minimum time between function calls in milliseconds
 * @returns A throttled version of the original function
 */
export function throttle<T extends (...args: any[]) => any>(
  func: T,
  limit: number
): (...args: Parameters<T>) => void {
  let inThrottle = false;
  let lastArgs: Parameters<T> | null = null;
  
  return function(...args: Parameters<T>): void {
    if (!inThrottle) {
      func(...args);
      inThrottle = true;
      
      setTimeout(() => {
        inThrottle = false;
        if (lastArgs) {
          func(...lastArgs);
          lastArgs = null;
        }
      }, limit);
    } else {
      lastArgs = args;
    }
  };
}

/**
 * Formats a file path into a human-readable display string
 * Handles entity paths, meeting paths, and document paths
 *
 * Examples:
 * - "entities/person/john-doe-abc123.md" → "Person"
 * - "meetings/2024-01-15-team-standup.md" → "Meetings"
 * - "documents/project-proposal.md" → "Documents"
 *
 * @param path The file path to format
 * @returns A human-readable category/folder label
 */
export function formatPathCategory(path: string): string {
  if (!path) return '';

  // Remove leading/trailing slashes and normalize
  const normalizedPath = path.replace(/^\/+|\/+$/g, '');
  const parts = normalizedPath.split('/');

  if (parts.length === 0) return '';

  // Get the first meaningful folder
  const folder = parts[0];

  // Special handling for entities - show the entity type
  if (folder === 'entities' && parts.length > 1) {
    return formatLabel(parts[1]);
  }

  return formatLabel(folder);
}

/**
 * Converts a slug/filename into a readable label
 *
 * Examples:
 * - "project-proposal" → "Project Proposal"
 * - "team_standup" → "Team Standup"
 * - "john-doe-abc123" → "John Doe Abc123" (UUIDs get stripped elsewhere)
 *
 * @param slug The slug or filename to format
 * @returns A human-readable label
 */
export function formatLabel(slug: string): string {
  if (!slug) return '';

  return slug
    // Remove file extensions
    .replace(/\.[^/.]+$/, '')
    // Replace dashes and underscores with spaces
    .replace(/[-_]/g, ' ')
    // Capitalize first letter of each word
    .replace(/\b\w/g, char => char.toUpperCase())
    .trim();
}

/**
 * Strips UUID-like patterns from a string
 * Useful for cleaning up filenames that have IDs appended
 *
 * Examples:
 * - "john-doe-abc12345" → "john-doe"
 * - "meeting-notes-f47ac10b-58cc" → "meeting-notes"
 *
 * @param str The string to clean
 * @returns String with UUID patterns removed
 */
export function stripUuidSuffix(str: string): string {
  if (!str) return '';

  // Remove common UUID patterns (full UUID, partial UUID, or short ID suffixes)
  return str
    // Full UUID pattern
    .replace(/-[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$/i, '')
    // Partial UUID at end (8+ hex chars after a dash)
    .replace(/-[a-f0-9]{8,}$/i, '')
    // Short alphanumeric ID suffix (5-12 chars after a dash, only if it looks like an ID)
    .replace(/-[a-z0-9]{5,12}$/i, (match) => {
      // Only strip if it looks like an ID (has numbers mixed with letters)
      if (/\d/.test(match) && /[a-z]/i.test(match)) {
        return '';
      }
      return match;
    })
    .trim();
}