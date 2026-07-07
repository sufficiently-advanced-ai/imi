/**
 * Meeting History API client functions (Issue #583)
 */

import { fetcher } from "./index";
import {
  MeetingHistoryStats,
  MeetingHistoryListResponse,
  MeetingHistoryFilters,
  ExtractionStatus,
  EntityCounts,
} from "@/lib/types/meeting-history";

/**
 * Meeting content response (Issue #594)
 */
export interface MeetingContent {
  bot_id: string;
  meeting_id: string;
  title: string | null;
  body: string;
  transcript: string | null;
  updated_at: string;
  duration: number | null;
  participants: string[];
  platform: string | null;
  start_time: string | null;
  entities_mentioned: Record<string, string[]>;
  entity_counts: EntityCounts;
  is_finalized: boolean;
  status: string;
}

/**
 * Fetch meeting history statistics
 * @param {RequestInit} options - Optional fetch options (e.g., AbortController signal)
 * @returns {Promise<MeetingHistoryStats>} Meeting statistics
 */
export async function fetchMeetingHistoryStats(
  options?: RequestInit,
): Promise<MeetingHistoryStats> {
  return fetcher('/meetings/history/stats', options);
}

/**
 * Fetch meeting history list with optional filters
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
  if (filters?.platform) {
    params.set("platform", filters.platform);
  }
  if (filters?.has_transcript !== undefined) {
    params.set("has_transcript", String(filters.has_transcript));
  }
  if (filters?.has_recording !== undefined) {
    params.set("has_recording", String(filters.has_recording));
  }

  return fetcher(`/meetings/history/list?${params.toString()}`, options);
}

/**
 * Fetch extraction status for a specific meeting
 * @param {string} meetingId - The ID of the meeting
 * @param {RequestInit} options - Optional fetch options (e.g., AbortController signal)
 * @returns {Promise<ExtractionStatus>} Extraction status information
 */
export async function fetchExtractionStatus(
  meetingId: string,
  options?: RequestInit,
): Promise<ExtractionStatus> {
  const id = encodeURIComponent(meetingId);
  return fetcher(`/meetings/${id}/extraction-status`, options);
}

/**
 * Fetch complete meeting content including body/summary (Issue #594)
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
