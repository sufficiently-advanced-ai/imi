/**
 * useReviewCounts — returns the total number of items awaiting human review.
 *
 * Count definition (locked by product decision):
 *   pending supersession candidates
 *   + pending conflict candidates
 *   + candidate decisions (decisions in "candidate" state)
 *
 * This mirrors the loadReviewCount logic in app/(protected)/decisions/page.tsx.
 */

import { useCallback, useEffect, useState } from "react";
import {
  fetchDecisions,
  fetchConflictCandidates,
  fetchSupersessionCandidates,
} from "@/lib/api/decisions";

export interface UseReviewCountsResult {
  /** Total pending-review items; null until the first successful load. */
  count: number | null;
  /** Manually re-fetch immediately (resolves after the fetch completes). */
  refresh: () => Promise<void>;
}

const REFETCH_INTERVAL_MS = 60_000;

export function useReviewCounts(): UseReviewCountsResult {
  const [count, setCount] = useState<number | null>(null);

  const refresh = useCallback(async (): Promise<void> => {
    try {
      const [supersessions, conflicts, candidateDecisions] = await Promise.all([
        fetchSupersessionCandidates(),
        fetchConflictCandidates(),
        fetchDecisions({ state: "candidate", limit: 1 }),
      ]);
      setCount(supersessions.length + conflicts.length + candidateDecisions.total);
    } catch {
      // Badge count is best-effort — keep the last known value, fail silently.
    }
  }, []);

  // Fetch on mount
  useEffect(() => {
    refresh();
  }, [refresh]);

  // Refetch every 60 seconds
  useEffect(() => {
    const interval = setInterval(refresh, REFETCH_INTERVAL_MS);
    return () => clearInterval(interval);
  }, [refresh]);

  // Refetch when the tab becomes visible again
  useEffect(() => {
    function handleVisibilityChange() {
      if (document.visibilityState === "visible") {
        refresh();
      }
    }
    document.addEventListener("visibilitychange", handleVisibilityChange);
    return () => document.removeEventListener("visibilitychange", handleVisibilityChange);
  }, [refresh]);

  return { count, refresh };
}
