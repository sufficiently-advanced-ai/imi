'use client';

import { useState, useEffect } from 'react';
import { useAuth } from '@/lib/hooks/useAuth';
import { getUserProfile, getUserSessions, updateUserPreferences, revokeSession } from '@/lib/api/users';
import type { UserProfile, UserSession, UserPreferences } from '@/lib/api/users';
import UserProfileCard from '@/components/profile/UserProfileCard';
import PreferencesForm from '@/components/profile/PreferencesForm';
import SessionsList from '@/components/profile/SessionsList';
import ThemeSwitcher from '@/components/profile/ThemeSwitcher';

export default function ProfilePage() {
  const { user, loading: authLoading } = useAuth();
  const [profile, setProfile] = useState<UserProfile | null>(null);
  const [sessions, setSessions] = useState<UserSession[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (authLoading) return;
    
    if (!user) {
      // User not authenticated, redirect handled by ProtectedLayout
      return;
    }

    loadProfileData();
  }, [user, authLoading]);

  const loadProfileData = async () => {
    try {
      setLoading(true);
      setError(null);
      
      const [profileData, sessionsData] = await Promise.all([
        getUserProfile(),
        getUserSessions(),
      ]);
      
      setProfile(profileData);
      setSessions(sessionsData);
    } catch (err) {
      console.error('Failed to load profile data:', err);
      setError('Error loading profile data');
    } finally {
      setLoading(false);
    }
  };

  const handleSavePreferences = async (preferences: UserPreferences) => {
    await updateUserPreferences(preferences);
    
    // Update local profile state
    if (profile) {
      setProfile(prev => prev ? { ...prev, preferences } : null);
    }
  };

  const handleRevokeSession = async (sessionId: string) => {
    await revokeSession(sessionId);
    
    // Remove session from local state
    setSessions(prev => prev.filter(session => session.id !== sessionId));
  };

  if (authLoading || loading) {
    return (
      <div className="max-w-4xl mx-auto p-6">
        <div className="text-center">Loading...</div>
      </div>
    );
  }

  if (!user) {
    // This shouldn't happen due to ProtectedLayout, but just in case
    return null;
  }

  if (error) {
    return (
      <div className="max-w-4xl mx-auto p-6">
        <div className="text-center text-red-600">Error loading profile</div>
      </div>
    );
  }

  if (!profile) {
    return (
      <div className="max-w-4xl mx-auto p-6">
        <div className="text-center">Profile data not available</div>
      </div>
    );
  }

  return (
    <div className="max-w-4xl mx-auto p-6 space-y-8">
      {/* Page Header */}
      <div className="flex items-center justify-between">
        <h1 className="text-3xl font-bold">Profile</h1>
        <ThemeSwitcher currentTheme={profile.preferences.theme} />
      </div>

      {/* Account Information Section */}
      <section>
        <h2 className="text-xl font-semibold mb-4">Account Information</h2>
        <UserProfileCard user={profile} />
      </section>

      {/* Preferences Section */}
      <section>
        <h2 className="text-xl font-semibold mb-4">Preferences</h2>
        <PreferencesForm 
          preferences={profile.preferences}
          onSave={handleSavePreferences}
        />
      </section>

      {/* Active Sessions Section */}
      <section>
        <h2 className="text-xl font-semibold mb-4">Active Sessions</h2>
        <SessionsList 
          sessions={sessions}
          onRevokeSession={handleRevokeSession}
        />
      </section>
    </div>
  );
}