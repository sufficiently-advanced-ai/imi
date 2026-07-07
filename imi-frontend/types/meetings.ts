/**
 * Shared types for meeting-related components
 */

export interface Meeting {
  id: string;
  bot_id: string | null;
  calendar_id: string | null;
  calendar_meeting_id: string | null;
  title: string;
  start_time: string;
  duration_minutes: number;
  attendee_count: number;
  meeting_type: 'EXTERNAL' | 'INTERNAL';
  bot_status: 'none' | 'scheduled' | 'in_call' | 'done';
  is_past: boolean;
  platform: string;
  meeting_url: string | null;
  extraction_step?: 'processing_transcript' | 'extracting_entities';
  workflow_id?: string;
  workflow_name?: string;
}
