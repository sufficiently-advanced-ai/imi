/**
 * Types for the Meetings API (app/routes/meetings.py): meeting documents in
 * the corpus, ordered by when they happened.
 */

export interface MeetingHistoryStats {
  total_meetings: number;
  meetings_with_transcripts: number;
  meetings_summarized: number;
  total_signals: number;
  first_meeting: string | null;
  last_meeting: string | null;
}

export interface EntityCounts {
  people: number;
  projects: number;
  accounts: number;
  action_items: number;
  decisions: number;
}

export interface SignalCounts {
  decision: number;
  action_item: number;
  key_point: number;
  insight: number;
}

export interface MeetingHistoryItem {
  id: string;
  bot_id: string;
  title: string;
  start_time: string | null;
  time_source: string | null;
  participants: string[];
  attendee_count: number;
  purpose: string | null;
  key_points: string[];
  summarized: boolean;
  has_transcript: boolean;
  lane: string;
  signal_counts: SignalCounts;
}

export interface MeetingHistoryListResponse {
  items: MeetingHistoryItem[];
  total: number;
  next_cursor: string | null;
  page_size: number;
}

export interface MeetingHistoryFilters {
  start_date?: string;
  end_date?: string;
  q?: string;
  has_transcript?: boolean;
  cursor?: string;
  page_size?: number;
}

export interface MeetingSignal {
  id: string;
  type: "decision" | "action_item" | "key_point" | "insight" | string;
  content: string;
  owner: string | null;
  status: string | null;
  position: number;
}
