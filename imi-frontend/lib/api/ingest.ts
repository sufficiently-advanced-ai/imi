/**
 * Ingest API client — /api/ingest
 * Mirrors the backend IngestRequest / IngestResponse / IngestJobStatus models.
 */

import { fetcher } from "./index";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export type IngestSource =
  | "transcript"
  | "document"
  | "email"
  | "note"
  | "unknown";

export interface IngestRequest {
  content: string;
  title?: string;
  source?: IngestSource;
  source_id?: string;
  participants?: string[];
  metadata?: Record<string, unknown>;
}

export interface IngestResponse {
  job_id: string;
  status: "accepted" | "duplicate";
  poll_url: string;
}

export interface IngestJobStatus {
  job_id: string;
  status: "pending" | "running" | "completed" | "failed";
  content_type: string | null;
  phases_completed: string[];
  current_phase: string | null;
  result: Record<string, unknown> | null;
  error: string | null;
}

// ---------------------------------------------------------------------------
// SSE event types
// ---------------------------------------------------------------------------

export interface IngestPhaseEvent {
  type: "ingest_phase";
  phase: string;
  status: "started" | "completed";
  phases_completed: string[];
  timestamp: string;
  execution_id?: string;
}

export interface DeltaReportReadyEvent {
  type: "delta_report_ready";
  summary: Record<string, number>;
  timestamp: string;
}

export interface IngestCompleteEvent {
  type: "ingest_complete";
  result: Record<string, number | string>;
  timestamp: string;
}

export interface IngestFailedEvent {
  type: "ingest_failed";
  error: string;
  timestamp: string;
}

export interface IngestKeepaliveEvent {
  type: "keepalive";
  timestamp: string;
}

export interface IngestConnectedEvent {
  type: "connected";
  execution_id: string | null;
  timestamp: string;
}

export type IngestSSEEvent =
  | IngestPhaseEvent
  | DeltaReportReadyEvent
  | IngestCompleteEvent
  | IngestFailedEvent
  | IngestKeepaliveEvent
  | IngestConnectedEvent;

// ---------------------------------------------------------------------------
// Delta Report types (mirrors app/services/delta_report.py)
// ---------------------------------------------------------------------------

export interface DeltaItem {
  signal_id: string;
  content: string;
  entities: string[];
  owner: string | null;
  due_date: string | null;
}

export interface SupersessionProposal {
  new_signal_id: string;
  old_signal_id: string;
  old_content: string;
  reason: string;
  confidence: number;
  status: "pending" | "confirmed" | "dismissed";
}

export interface ConflictCandidate {
  new_signal_id: string;
  other_signal_id: string;
  other_content: string;
  rationale: string;
  confidence: number;
  status: string;
}

export interface DeltaReport {
  job_id: string;
  bot_id: string;
  meeting_title: string | null;
  generated_at: string;
  new_decisions: DeltaItem[];
  proposed_supersessions: SupersessionProposal[];
  potential_conflicts: ConflictCandidate[];
  commitments_opened: DeltaItem[];
  commitments_closed: DeltaItem[];
  entities_touched: Array<{ id: string; name: string; type: string }>;
  counts: Record<string, number>;
}

// ---------------------------------------------------------------------------
// Candidate action types (mirrors supersession.py)
// ---------------------------------------------------------------------------

export interface CandidateRow {
  new_signal_id: string;
  new_content: string;
  old_signal_id: string;
  old_content: string;
  matched_entities: string[];
  reason: string;
  confidence: number;
  proposed_at: string;
}

// ---------------------------------------------------------------------------
// API functions
// ---------------------------------------------------------------------------

/**
 * A loosely-typed row returned by GET /api/ingest/jobs.
 * The job_store schema is not fully stable, so unknown keys are allowed.
 */
export interface IngestJobRecord {
  job_id: string;
  status: IngestJobStatus["status"];
  created_at?: string;
  title?: string | null;
  [key: string]: unknown;
}

/** Fetch all ingest jobs, sorted by created_at desc. */
export async function fetchIngestJobs(): Promise<IngestJobRecord[]> {
  const data = await fetcher("/ingest/jobs");
  return data as IngestJobRecord[];
}

/** Submit content for ingestion. Returns 202 (new) or 200 (duplicate). */
export async function submitIngest(payload: IngestRequest): Promise<IngestResponse> {
  const data = await fetcher("/ingest", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  return data as IngestResponse;
}

/** Poll the current status of an ingestion job. */
export async function fetchIngestStatus(jobId: string): Promise<IngestJobStatus> {
  const data = await fetcher(`/ingest/${jobId}/status`);
  return data as IngestJobStatus;
}

/** Fetch the delta report for a completed job (404 until DELTA_REPORT phase done). */
export async function fetchDelta(jobId: string): Promise<DeltaReport> {
  const data = await fetcher(`/ingest/${jobId}/delta`);
  return data as DeltaReport;
}

/**
 * Open an SSE connection for a job's pipeline events.
 */
export function createIngestEventSource(
  jobId: string,
  onEvent: (data: IngestSSEEvent) => void,
  onError?: (error: Event) => void,
): EventSource {
  const sseUrl = `/api/ingest/${jobId}/stream`;
  const eventSource = new EventSource(sseUrl);

  eventSource.addEventListener("message", (event) => {
    try {
      const data: IngestSSEEvent = JSON.parse(event.data);
      if (data.type !== "keepalive") {
        onEvent(data);
      }
    } catch (err) {
      console.error("[IngestSSE] Failed to parse SSE message:", err);
    }
  });

  eventSource.addEventListener("error", (error) => {
    console.error("[IngestSSE] SSE error:", error);
    onError?.(error);
    if (eventSource.readyState === EventSource.CLOSED) {
      console.log("[IngestSSE] Connection closed, EventSource will reconnect");
    }
  });

  return eventSource;
}

/** Confirm a supersession candidate (applies governance transition). */
export async function confirmCandidate(params: {
  new_signal_id: string;
  old_signal_id: string;
  actor?: string;
}): Promise<{ confirmed: boolean; new_signal_id: string; old_signal_id: string }> {
  const data = await fetcher("/supersession/candidates/confirm", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(params),
  });
  return data as { confirmed: boolean; new_signal_id: string; old_signal_id: string };
}

/** Dismiss a supersession candidate (no governance applied). */
export async function dismissCandidate(params: {
  new_signal_id: string;
  old_signal_id: string;
}): Promise<{ dismissed: boolean; new_signal_id: string; old_signal_id: string }> {
  const data = await fetcher("/supersession/candidates/dismiss", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(params),
  });
  return data as { dismissed: boolean; new_signal_id: string; old_signal_id: string };
}
