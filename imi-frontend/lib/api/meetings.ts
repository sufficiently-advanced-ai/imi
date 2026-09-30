/**
 * Meetings API client functions (app/routes/meetings.py)
 */

import { fetcher } from "./index";
import {
  MeetingHistoryStats,
  MeetingHistoryListResponse,
  MeetingHistoryFilters,
  EntityCounts,
  MeetingSignal,
} from "@/lib/types/meeting-history";

/**
 * One meeting: its summary (body), transcript and visible signals
 */
export interface MeetingContent {
  bot_id: string;
  meeting_id: string;
  title: string | null;
  /** The meeting summary; "" when the meeting has none */
  body: string;
  purpose: string | null;
  key_points: string[];
  summarized: boolean;
  transcript: string | null;
  updated_at: string;
  /** Seconds */
  duration: number | null;
  participants: string[];
  platform: string | null;
  start_time: string | null;
  time_source: string | null;
  entities_mentioned: Record<string, string[]>;
  entity_counts: EntityCounts;
  signals: MeetingSignal[];
  is_finalized: boolean;
  status: string;
}

/**
 * Fetch meeting corpus statistics
 * @param {RequestInit} options - Optional fetch options (e.g., AbortController signal)
 * @returns {Promise<MeetingHistoryStats>} Meeting statistics
 */
export async function fetchMeetingHistoryStats(
  options?: RequestInit,
): Promise<MeetingHistoryStats> {
  return fetcher('/meetings/history/stats', options);
}

/**
 * Fetch meetings, newest (by when they happened) first
 * @param {MeetingHistoryFilters} filters - Optional filters for the meeting list
 * @param {RequestInit} options - Optional fetch options (e.g., AbortController signal)
 * @returns {Promise<MeetingHistoryListResponse>} Cursor-paginated list of meetings
 */
export async function fetchMeetingHistoryList(
  filters?: MeetingHistoryFilters,
  options?: RequestInit,
): Promise<MeetingHistoryListResponse> {
  const params = new URLSearchParams();

  // Set pagination defaults - cursor-based with 50-item default
  const pageSize = filters?.page_size ?? 50;
  params.append('page_size', pageSize.toString());

  // Add cursor for pagination
  if (filters?.cursor) {
    params.append('cursor', filters.cursor);
  }

  // Add optional filters
  if (filters?.start_date) {
    params.set("start_date", filters.start_date);
  }
  if (filters?.end_date) {
    params.set("end_date", filters.end_date);
  }
  if (filters?.q) {
    params.set("q", filters.q);
  }
  if (filters?.has_transcript !== undefined) {
    params.set("has_transcript", String(filters.has_transcript));
  }

  return fetcher(`/meetings/history/list?${params.toString()}`, options);
}

/**
 * Fetch one meeting's summary, transcript and signals
 * @param {string} botId - The bot ID of the meeting
 * @param {RequestInit} options - Optional fetch options (e.g., AbortController signal)
 * @returns {Promise<MeetingContent>} Complete meeting content with metadata
 */
export async function fetchMeetingContent(
  botId: string,
  options?: RequestInit,
): Promise<MeetingContent> {
  const id = encodeURIComponent(botId);
  return fetcher(`/meetings/${id}/content`, options);
}
