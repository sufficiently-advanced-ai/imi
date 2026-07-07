/**
 * Signal Feed API client
 */

import { fetcher } from "./index";

/** Structured entity reference (used in persisted signal format) */
export interface EntityRef {
  id: string;
  type: string;
  name: string;
}

export interface Signal {
  id: string;
  type: "decision" | "action_item" | "key_point" | "insight";
  content: string;
  source_meeting_id: string;
  source_meeting_title: string | null;
  source_timestamp: string;
  participants: string[];
  entities: Record<string, string[]>;
  confidence: number;
  status: string | null;
  owner: string | null;
  position: number;
  metadata: Record<string, unknown>;
}

export interface DayGroup {
  date: string;
  label: string;
  signals: Signal[];
}

export interface SignalFeedResponse {
  days: DayGroup[];
  total_signals: number;
  total_meetings: number;
}

export interface SignalFeedFilters {
  entityId?: string;
  dateFrom?: string;
  dateTo?: string;
}

/**
 * Fetch the signal feed with optional filters.
 */
export async function fetchSignalFeed(
  signalType?: string,
  limit: number = 100,
  filters?: SignalFeedFilters,
  options?: RequestInit,
): Promise<SignalFeedResponse> {
  const params = new URLSearchParams();
  if (signalType) {
    params.set("signal_type", signalType);
  }
  if (filters?.entityId) {
    params.set("entity_id", filters.entityId);
  }
  if (filters?.dateFrom) {
    params.set("date_from", filters.dateFrom);
  }
  if (filters?.dateTo) {
    params.set("date_to", filters.dateTo);
  }
  params.set("limit", limit.toString());

  return fetcher(`/signals/feed?${params.toString()}`, options);
}

/**
 * Fetch a single signal by ID.
 */
export async function fetchSignalById(
  signalId: string,
  options?: RequestInit,
): Promise<Signal> {
  return fetcher(`/signals/${signalId}`, options);
}
