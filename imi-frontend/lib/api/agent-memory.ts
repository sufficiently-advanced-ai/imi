/**
 * Agent-memory API client (OB1 absorption Phase 2).
 *
 * The unified review queue spans record kinds (captures + agent memories);
 * the server resolves the kind on review. This is the human gate that mints
 * instruction-grade memory (ADR-002).
 */

import { fetcher } from "./index";

export type MemoryRecordKind = "capture" | "agent_memory";

export type MemoryReviewAction =
  | "confirm"
  | "reject"
  | "evidence_only"
  | "dispute"
  | "supersede";

export interface ReviewQueueItem {
  id: string;
  record_kind: MemoryRecordKind;
  content: string;
  summary: string | null;
  memory_type: string | null;
  source: string | null;
  runtime_name: string | null;
  task_id: string | null;
  provenance_status: string;
  created_at: string;
}

export interface ReviewQueueResponse {
  items: ReviewQueueItem[];
  total: number;
}

export interface ReviewResult {
  success: boolean;
  review_applied: boolean;
  audit_row_id?: string;
  gate_response?: string;
  committed?: boolean;
}

/**
 * Fetch pending records awaiting human review, newest first.
 */
export async function fetchReviewQueue(
  filters?: { kind?: MemoryRecordKind; limit?: number },
  options?: RequestInit,
): Promise<ReviewQueueResponse> {
  const params = new URLSearchParams();
  if (filters?.kind) {
    params.set("kind", filters.kind);
  }
  if (filters?.limit) {
    params.set("limit", String(filters.limit));
  }
  const query = params.toString();
  return fetcher(`/memories/review${query ? `?${query}` : ""}`, options);
}

export interface InspectorResponse {
  schema_version: string;
  record_id: string;
  record_kind: string;
  record: Record<string, unknown> | null;
  audit_history: Array<{
    action: string;
    gate_response: string | null;
    actor: string | null;
    reasoning: string;
    created_at: string;
  }>;
  usage: {
    times_returned: number;
    times_used: number;
    times_ignored: number;
    last_returned_at: string | null;
  };
  judge_usage: Array<{
    decision_id: string;
    decision: string;
    used_as: string;
    task_id: string | null;
  }>;
  lineage: Array<{ record_id: string; relation: string }>;
  influence: {
    can_use_as_instruction: boolean;
    can_use_as_evidence: boolean;
    position: "instruction" | "evidence" | "blocked";
    superseded_by: string | null;
  };
}

export interface RecallTraceSummary {
  request_id: string;
  query: string;
  authority: string;
  surface: string;
  runtime_name: string | null;
  task_id: string | null;
  created_at: string | null;
  items: RecallTraceItem[] | null;
}

export interface RecallTraceItem {
  record_id: string;
  record_kind: string;
  rank: number;
  similarity: number | null;
  ranking_score: number | null;
  used: boolean | null;
  ignored_reason: string | null;
}

/**
 * Inspector: why a memory exists, its audit history, usage, and influence.
 */
export async function fetchInspector(
  id: string,
  options?: RequestInit,
): Promise<InspectorResponse> {
  return fetcher(`/memories/${encodeURIComponent(id)}/inspector`, options);
}

/**
 * List recent recall traces (without items).
 */
export async function fetchRecallTraces(
  filters?: { task_id?: string; limit?: number },
  options?: RequestInit,
): Promise<{ traces: RecallTraceSummary[]; total: number }> {
  const params = new URLSearchParams();
  if (filters?.task_id) {
    params.set("task_id", filters.task_id);
  }
  if (filters?.limit) {
    params.set("limit", String(filters.limit));
  }
  const query = params.toString();
  return fetcher(`/agent-memory/recall-traces${query ? `?${query}` : ""}`, options);
}

/**
 * Fetch one recall trace with its ranked items and usage marks.
 */
export async function fetchRecallTrace(
  requestId: string,
  options?: RequestInit,
): Promise<RecallTraceSummary> {
  return fetcher(
    `/agent-memory/recall-traces/${encodeURIComponent(requestId)}`,
    options,
  );
}

/**
 * Apply an audited governance transition; the server resolves the kind.
 */
export async function reviewRecord(
  id: string,
  action: MemoryReviewAction,
  actor?: string,
  supersededBy?: string,
): Promise<ReviewResult> {
  const body: Record<string, unknown> = { action };
  if (actor !== undefined) {
    body.actor = actor;
  }
  if (supersededBy !== undefined) {
    body.superseded_by = supersededBy;
  }
  return fetcher(`/memories/${encodeURIComponent(id)}/review`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}
