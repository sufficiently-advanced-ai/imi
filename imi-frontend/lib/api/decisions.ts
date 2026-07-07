/**
 * Decisions API client
 */

import { fetcher } from "./index";
import { getApiUrl } from "../config";

export type DecisionState =
  | "candidate"
  | "active"
  | "stale"
  | "superseded"
  | "rejected";

/**
 * Get the badge variant color for a decision state.
 */
export function decisionStateBadgeVariant(
  state: DecisionState,
): "success" | "warning" | "gray" | "destructive" | "outline" {
  switch (state) {
    case "active":
      return "success";
    case "candidate":
      return "outline";
    case "stale":
      return "warning";
    case "superseded":
      return "gray";
    case "rejected":
      return "destructive";
    default:
      return "outline";
  }
}

export interface Decision {
  id: string;
  content: string;
  state: DecisionState;
  state_reason: string | null;
  age_days: number | null;
  review_status: string | null;
  provenance_status: string | null;
  can_use_as_evidence: boolean;
  can_use_as_instruction: boolean;
  owner: string | null;
  owner_id: string | null;
  client_id: string | null;
  source_meeting_id: string | null;
  source_meeting_title: string | null;
  source_timestamp: string | null;
  superseded_by: string | null;
  tenant_id: string | null;
  metadata: Record<string, unknown>;
}

export interface DecisionListResponse {
  decisions: Decision[];
  total: number;
  counts_by_state: Record<string, number>;
}

export interface GovernanceLadder {
  position: "instruction" | "evidence" | "blocked";
  provenance_status: string | null;
  review_status: string | null;
  can_use_as_evidence: boolean;
  can_use_as_instruction: boolean;
}

export interface LineageEntry {
  id: string;
  content: string;
  state: DecisionState;
  source_timestamp: string | null;
  relation: "predecessor" | "self" | "successor";
}

export interface AuditEntry {
  action: string;
  gate_response: string | null;
  actor: string | null;
  reasoning: string | null;
  created_at: string;
}

export interface DecisionDetail extends Decision {
  lineage: LineageEntry[];
  audit_history: AuditEntry[];
  governance_ladder: GovernanceLadder;
}

export interface DecisionStats {
  meetings: number;
  decisions: number;
  counts_by_state: Record<string, number>;
  stale: number;
  superseded: number;
  headline: string;
}

export interface DecisionFilters {
  state?: DecisionState;
  owner_id?: string;
  client_id?: string;
  date_from?: string;
  date_to?: string;
  limit?: number;
}

/** Path for the constitution download (relative to /api) */
export const constitutionDownloadPath = "/api/decisions/constitution";

/**
 * Returns the full URL for downloading the constitution artifact,
 * respecting the app's base path so subpath deployments work correctly.
 */
export function getConstitutionDownloadUrl(): string {
  return getApiUrl("/decisions/constitution");
}

/**
 * Fetch decisions list with optional filters.
 */
export async function fetchDecisions(
  filters?: DecisionFilters,
  options?: RequestInit,
): Promise<DecisionListResponse> {
  const params = new URLSearchParams();
  if (filters?.state) {
    params.set("state", filters.state);
  }
  if (filters?.owner_id) {
    params.set("owner_id", filters.owner_id);
  }
  if (filters?.client_id) {
    params.set("client_id", filters.client_id);
  }
  if (filters?.date_from) {
    params.set("date_from", filters.date_from);
  }
  if (filters?.date_to) {
    params.set("date_to", filters.date_to);
  }
  if (filters?.limit !== undefined) {
    params.set("limit", filters.limit.toString());
  }

  const query = params.toString();
  return fetcher(`/decisions${query ? `?${query}` : ""}`, options);
}

/**
 * Fetch a single decision with lineage, audit history, and governance ladder.
 */
export async function fetchDecisionById(
  id: string,
  options?: RequestInit,
): Promise<DecisionDetail> {
  return fetcher(`/decisions/${encodeURIComponent(id)}`, options);
}

/**
 * Fetch aggregate decision stats.
 */
export async function fetchDecisionStats(
  options?: RequestInit,
): Promise<DecisionStats> {
  return fetcher("/decisions/stats", options);
}

/**
 * Trigger constitution export. Returns the path and commit info.
 */
export async function exportConstitution(
  options?: RequestInit,
): Promise<{ path: string; committed: boolean; counts_by_state: Record<string, number> }> {
  return fetcher("/decisions/constitution/export", {
    ...options,
    method: "POST",
  });
}

// ---------------------------------------------------------------------------
// Review queue (supersession + conflict candidates)
// ---------------------------------------------------------------------------
// Paths are relative to getApiUrl's /api prefix (NEXT_PUBLIC_API_URL) — a
// leading "/api/" here would double-prefix to /api/api/... and 404.

/** Mirrors app/routes/supersession.py CandidateItem. */
export interface SupersessionCandidate {
  new_signal_id: string;
  new_content: string;
  old_signal_id: string;
  old_content: string;
  matched_entities: string[];
  reason: string;
  confidence: number; // 0..1
  proposed_at: string; // ISO timestamp
}

/** Mirrors app/routes/conflicts.py ConflictCandidateItem. */
export interface ConflictQueueCandidate {
  signal_id: string;
  signal_content: string;
  other_signal_id: string;
  other_content: string;
  rationale: string;
  confidence: number;
  speakers: string[];
  proposed_at: string;
}

/** All pending supersession candidates (server filters to pending-only). */
export async function fetchSupersessionCandidates(
  options?: RequestInit,
): Promise<SupersessionCandidate[]> {
  const data = await fetcher("/supersession/candidates", options);
  return data as SupersessionCandidate[];
}

/** All pending conflict candidates (server filters to pending-only). */
export async function fetchConflictCandidates(
  options?: RequestInit,
): Promise<ConflictQueueCandidate[]> {
  const data = await fetcher("/conflicts/candidates", options);
  return data as ConflictQueueCandidate[];
}

/** Confirm a conflict candidate (writes conflicts_with on both signals). */
export async function confirmConflictCandidate(params: {
  signal_id: string;
  other_signal_id: string;
}): Promise<{ confirmed: boolean }> {
  const data = await fetcher("/conflicts/candidates/confirm", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(params),
  });
  return data as { confirmed: boolean };
}

/** Dismiss a conflict candidate (no governance applied). */
export async function dismissConflictCandidate(params: {
  signal_id: string;
  other_signal_id: string;
}): Promise<{ dismissed: boolean }> {
  const data = await fetcher("/conflicts/candidates/dismiss", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(params),
  });
  return data as { dismissed: boolean };
}

/** Apply a human review action to a candidate decision. */
export async function reviewDecision(
  decisionId: string,
  params: { action: "confirm" | "reject" | "evidence_only"; actor?: string },
): Promise<{ reviewed: boolean; new_state: string | null }> {
  const data = await fetcher(
    `/decisions/${encodeURIComponent(decisionId)}/review`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(params),
    },
  );
  return data as { reviewed: boolean; new_state: string | null };
}
