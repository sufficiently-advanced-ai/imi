/**
 * Judge API client (OB1 absorption Phase 4).
 *
 * Judgment events are runtime telemetry: what action an agent proposed, what
 * the judge decided (ADR-001 gate vocabulary), which memories informed it.
 */

import { fetcher } from "./index";

export type JudgeDecision = "allow" | "block" | "revise" | "escalate";

export interface JudgeDecisionEvent {
  decision_id: string;
  action_id: string;
  risk_class: string;
  decision: JudgeDecision;
  reasoning_summary: string;
  confidence?: string;
  judge?: Record<string, unknown>;
  checks: Record<string, string>;
  memory_used: Array<{ record_id: string; used_as: string }>;
  memory_written: Array<{ id: string; memory_type: string }>;
  recall_request_id?: string | null;
  runtime_name: string | null;
  task_id: string | null;
  created_at: string | null;
}

export interface JudgeDecisionListResponse {
  decisions: JudgeDecisionEvent[];
  total: number;
}

/**
 * Badge variant for a judge decision outcome.
 */
export function judgeDecisionBadgeVariant(
  decision: JudgeDecision,
): "success" | "warning" | "gray" | "destructive" | "outline" {
  switch (decision) {
    case "allow":
      return "success";
    case "revise":
      return "warning";
    case "escalate":
      return "gray";
    case "block":
      return "destructive";
    default:
      return "outline";
  }
}

/**
 * Fetch judgment events, newest first.
 */
export async function fetchJudgeDecisions(
  filters?: { task_id?: string; decision?: JudgeDecision; limit?: number },
  options?: RequestInit,
): Promise<JudgeDecisionListResponse> {
  const params = new URLSearchParams();
  if (filters?.task_id) {
    params.set("task_id", filters.task_id);
  }
  if (filters?.decision) {
    params.set("decision", filters.decision);
  }
  if (filters?.limit) {
    params.set("limit", String(filters.limit));
  }
  const query = params.toString();
  return fetcher(`/judge/decisions${query ? `?${query}` : ""}`, options);
}
