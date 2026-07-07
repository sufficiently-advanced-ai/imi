/**
 * User API client for profile management
 */
import { apiClient } from './index';

export interface UserProfile {
  id: string;
  email: string;
  firstName?: string;
  lastName?: string;
  name?: string;
  profilePictureUrl?: string;
  createdAt: string;
  lastLoginAt?: string;
  preferences: UserPreferences;
}

export interface UserPreferences {
  theme: 'light' | 'dark';
  displayDensity: 'compact' | 'comfortable' | 'spacious';
  emailNotifications: boolean;
  pushNotifications: boolean;
}

export interface UserSession {
  id: string;
  deviceName: string;
  location: string;
  lastActive: string;
  isCurrent: boolean;
}

/**
 * Get current user's full profile
 */
export async function getUserProfile(): Promise<UserProfile> {
  return apiClient('/users/profile');
}

/**
 * Update user profile information
 */
export async function updateUserProfile(data: Partial<UserProfile>): Promise<{ success: boolean }> {
  return apiClient('/users/profile', {
    method: 'PUT',
    headers: {
      'Content-Type': 'application/json',
    },
    body: JSON.stringify(data),
  });
}

/**
 * Update user preferences
 */
export async function updateUserPreferences(preferences: Partial<UserPreferences>): Promise<{ success: boolean }> {
  return apiClient('/users/preferences', {
    method: 'PUT',
    headers: {
      'Content-Type': 'application/json',
    },
    body: JSON.stringify(preferences),
  });
}

/**
 * Get user's active sessions
 */
export async function getUserSessions(): Promise<UserSession[]> {
  return apiClient('/users/sessions');
}

/**
 * Revoke a specific session
 */
export async function revokeSession(sessionId: string): Promise<{ success: boolean }> {
  return apiClient(`/users/sessions/${sessionId}`, {
    method: 'DELETE',
  });
}