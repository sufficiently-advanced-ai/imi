/**
 * Captures API client — the G4 capture loop surface (OB1 absorption Phase 1).
 *
 * Captures enter the governance ladder as imported, evidence-grade memory;
 * review is the only governance entry point (ADR-002).
 */

import { fetcher } from "./index";

export type CaptureReviewAction =
  | "confirm"
  | "reject"
  | "evidence_only"
  | "dispute"
  | "supersede";

export interface CaptureEnrichment {
  type?: string;
  topics?: string[];
  people?: string[];
  action_items?: string[];
  dates_mentioned?: string[];
}

export interface Capture {
  id: string;
  content: string;
  source: string;
  source_id: string | null;
  summary: string | null;
  tags: string[];
  enrichment: CaptureEnrichment;
  related_record_ids: string[];
  provenance_status: string;
  review_status: string;
  can_use_as_evidence: boolean;
  can_use_as_instruction: boolean;
  superseded_by: string | null;
  valid_from: string | null;
  valid_to: string | null;
  tenant_id: string | null;
  created_at: string;
}

export interface CaptureListResponse {
  captures: Capture[];
  total: number;
}

export interface CaptureCreateResult {
  success: boolean;
  id: string;
  deduped: boolean;
  enrichment: CaptureEnrichment;
  vector_indexed: boolean;
  committed: boolean;
  capture: Capture;
}

export interface CaptureReviewResult {
  success: boolean;
  review_applied: boolean;
  audit_row_id?: string;
  gate_response?: string;
  committed?: boolean;
  capture?: Capture;
}

export interface CaptureFilters {
  review_status?: string;
  source?: string;
  limit?: number;
}

/**
 * Badge variant for a capture's governance position.
 */
export function captureReviewBadgeVariant(
  reviewStatus: string,
): "success" | "warning" | "gray" | "destructive" | "outline" {
  switch (reviewStatus) {
    case "confirmed":
      return "success";
    case "pending":
      return "warning";
    case "evidence_only":
      return "outline";
    case "merged":
      return "gray";
    case "rejected":
      return "destructive";
    default:
      return "outline";
  }
}

/**
 * Fetch captures list, newest first, with optional filters.
 */
export async function fetchCaptures(
  filters?: CaptureFilters,
  options?: RequestInit,
): Promise<CaptureListResponse> {
  const params = new URLSearchParams();
  if (filters?.review_status) {
    params.set("review_status", filters.review_status);
  }
  if (filters?.source) {
    params.set("source", filters.source);
  }
  if (filters?.limit) {
    params.set("limit", String(filters.limit));
  }
  const query = params.toString();
  return fetcher(`/captures${query ? `?${query}` : ""}`, options);
}

/**
 * Fetch a single capture by id.
 */
export async function fetchCaptureById(
  id: string,
  options?: RequestInit,
): Promise<Capture> {
  return fetcher(`/captures/${encodeURIComponent(id)}`, options);
}

/**
 * Capture a thought (persist-first; enrichment/indexing/commit are
 * best-effort server-side). Duplicate content returns deduped=true.
 */
export async function createCapture(params: {
  content: string;
  source?: string;
  source_id?: string;
  tags?: string[];
  source_date?: string;
  actor?: string;
}): Promise<CaptureCreateResult> {
  return fetcher("/captures", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(params),
  });
}

/**
 * Apply an audited governance transition to a capture.
 */
export async function reviewCapture(
  id: string,
  action: CaptureReviewAction,
  actor?: string,
  supersededBy?: string,
): Promise<CaptureReviewResult> {
  const body: Record<string, unknown> = { action };
  if (actor !== undefined) {
    body.actor = actor;
  }
  if (supersededBy !== undefined) {
    body.superseded_by = supersededBy;
  }
  return fetcher(`/captures/${encodeURIComponent(id)}/review`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}
