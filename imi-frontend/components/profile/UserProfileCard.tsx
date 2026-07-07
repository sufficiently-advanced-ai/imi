'use client';

import { Card } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { UserProfile } from '@/lib/api/users';

interface UserProfileCardProps {
  user: UserProfile;
  onEditProfile?: () => void;
}

export default function UserProfileCard({ user, onEditProfile }: UserProfileCardProps) {
  const displayName = (user.firstName && user.lastName ? `${user.firstName} ${user.lastName}` : user.name) ?? user.email;
  
  const formatDate = (dateString: string) => {
    const date = new Date(dateString);
    if (isNaN(date.getTime())) return '—';
    return date.toLocaleDateString('en-US', { 
      year: 'numeric', 
      month: 'long' 
    });
  };

  const formatLastLogin = (dateString?: string) => {
    if (!dateString) return 'Never';
    const date = new Date(dateString);
    if (isNaN(date.getTime())) return '—';
    const now = new Date();
    const diffMs = now.getTime() - date.getTime();
    if (diffMs <= 0) return 'Just now';
    const diffMins = Math.floor(diffMs / (1000 * 60));
    if (diffMins < 60) return `${diffMins} minute${diffMins === 1 ? '' : 's'} ago`;
    const diffHours = Math.floor(diffMins / 60);
    if (diffHours < 24) return `${diffHours} hour${diffHours === 1 ? '' : 's'} ago`;
    return date.toLocaleDateString('en-US', {
      month: 'short',
      day: 'numeric',
      year: 'numeric',
    });
  };

  const getInitials = (firstName?: string, lastName?: string) => {
    if (firstName && lastName) {
      return `${firstName.charAt(0)}${lastName.charAt(0)}`.toUpperCase();
    }
    if (firstName) return firstName.charAt(0).toUpperCase();
    if (lastName) return lastName.charAt(0).toUpperCase();
    if (user.email) return user.email.charAt(0).toUpperCase();
    return 'U';
  };

  return (
    <Card className="p-6">
      <div className="flex items-start space-x-4">
        {/* Profile Image or Avatar */}
        <div className="flex-shrink-0">
          {user.profilePictureUrl ? (
            <div 
              className="w-16 h-16 rounded-full bg-cover bg-center"
              style={{ backgroundImage: `url(${user.profilePictureUrl})` }}
              role="img"
              aria-label={`Profile picture of ${displayName}`}
            />
          ) : (
            <div className="w-16 h-16 rounded-full bg-slate-100 flex items-center justify-center text-xl font-semibold text-slate-600">
              {getInitials(user.firstName, user.lastName)}
            </div>
          )}
        </div>

        {/* Profile Information */}
        <div className="flex-1">
          <div className="flex justify-between items-start">
            <div>
              <h2 className="text-xl font-semibold">
                {user.firstName && user.lastName 
                  ? `${user.firstName} ${user.lastName}`
                  : user.name || user.email}
              </h2>
              <p className="text-slate-600">{user.email}</p>
            </div>
            
            <Button variant="outline" size="sm" onClick={onEditProfile}>
              Edit Profile
            </Button>
          </div>

          <div className="mt-4 space-y-2">
            <div className="flex items-center text-sm text-slate-600">
              <span className="font-medium">Member since:</span>
              <span className="ml-2">{formatDate(user.createdAt)}</span>
            </div>
            
            <div className="flex items-center text-sm text-slate-600">
              <span className="font-medium">Last login:</span>
              <span className="ml-2">{formatLastLogin(user.lastLoginAt)}</span>
            </div>
          </div>
        </div>
      </div>
    </Card>
  );
}