/**
 * Types for Meeting History feature (Issue #583, #585)
 */

export interface MeetingHistoryStats {
  total_meetings: number;
  total_duration_minutes: number;
  meetings_with_transcripts: number;
  meetings_with_recordings: number;
}

export interface EntityCounts {
  people: number;
  projects: number;
  accounts: number;
  action_items: number;
  decisions: number;
}

export interface MeetingHistoryItem {
  id: string;
  bot_id: string | null;
  calendar_id: string | null;
  calendar_meeting_id: string | null;
  title: string;
  start_time: string;
  end_time: string;
  duration_minutes: number;
  attendee_count: number;
  meeting_type: 'EXTERNAL' | 'INTERNAL';
  platform: string;
  meeting_url: string | null;
  has_transcript: boolean;
  has_recording: boolean;
  extraction_status: 'pending' | 'processing' | 'completed' | 'failed';
  bot_status?: 'none' | 'scheduled' | 'in_call' | 'done';
  is_past?: boolean;
  extraction_step?: 'processing_transcript' | 'extracting_entities';
  entity_counts?: EntityCounts | null;
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
  platform?: string;
  has_transcript?: boolean;
  has_recording?: boolean;
  cursor?: string;
  page_size?: number;
}

export interface ExtractionStatus {
  meeting_id: string;
  status: 'pending' | 'processing' | 'completed' | 'failed';
  extraction_step?: 'processing_transcript' | 'extracting_entities';
  progress?: number;
  entity_counts?: {
    people: number;
    projects: number;
    accounts: number;
    action_items: number;
    decisions: number;
  };
  error?: string | null;
  updated_at: string;
}
