/**
 * Corpus sync status client — GET /api/admin/corpus-sync
 * Mirrors app/services/corpus_sync.py::get_corpus_sync (also on /health/ready).
 */

import { fetcher } from "./index";

export type CorpusSyncStatusValue =
  | "pending"
  | "reconciling"
  | "synced"
  | "adopted"
  | "rebuild_required"
  | "unknown";

export interface CorpusSyncStatus {
  status: CorpusSyncStatusValue;
  reason: string | null;
  action: string | null;
  counts: Record<string, number>;
  build_id: string | null;
  baseline_at: string | null;
  error: string | null;
  updated_at: string | null;
  degraded: boolean;
  warnings: string[];
}

/** Statuses that mean the graph may not reflect the corpus on disk. */
export const STALE_CORPUS_STATUSES: ReadonlySet<CorpusSyncStatusValue> = new Set([
  "adopted",
  "rebuild_required",
]);

export async function fetchCorpusSync(): Promise<CorpusSyncStatus> {
  // No leading /api — getApiUrl() prepends it.
  return (await fetcher("/admin/corpus-sync")) as CorpusSyncStatus;
}
