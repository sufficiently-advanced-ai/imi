import { getApiUrl } from '../config';

export interface MeetingState {
  meeting_id: string;
  bot_id: string;
  updated_at: string;
  entities_mentioned: Record<string, string[]>;
  body: string;
  update_count: number;
  is_finalized: boolean;
  transcript?: string | null;
  end_time?: string | null;
  start_time?: string | null;
  participants: string[];
  key_points: string[];
  title?: string | null;
  status: string;
}

export async function getMeetingState(meetingId: string): Promise<MeetingState> {
  const response = await fetch(getApiUrl(`/meeting-state/${meetingId}`), {
    method: 'GET',
    headers: {
      'Content-Type': 'application/json',
    },
  });

  if (!response.ok) {
    if (response.status === 404) {
      throw new Error('Meeting state not found');
    }
    throw new Error(`Failed to fetch meeting state: ${response.statusText}`);
  }

  return response.json();
}