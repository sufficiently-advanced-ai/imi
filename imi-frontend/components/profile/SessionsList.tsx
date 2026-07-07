'use client';

import { useState } from 'react';
import { Card } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
  AlertDialogTrigger,
} from '@/components/ui/alert-dialog';
import { UserSession } from '@/lib/api/users';

interface SessionsListProps {
  sessions: UserSession[];
  onRevokeSession?: (sessionId: string) => Promise<void>;
}

export default function SessionsList({ sessions, onRevokeSession }: SessionsListProps) {
  const [revokingSessionId, setRevokingSessionId] = useState<string | null>(null);

  const formatLastActive = (dateString: string) => {
    const date = new Date(dateString);
    const now = new Date();
    const diffInMinutes = Math.floor((now.getTime() - date.getTime()) / (1000 * 60));
    
    if (diffInMinutes < 1) return 'Just now';
    if (diffInMinutes < 60) return `${diffInMinutes} minutes ago`;
    
    const diffInHours = Math.floor(diffInMinutes / 60);
    if (diffInHours < 24) return `${diffInHours} hours ago`;
    
    const diffInDays = Math.floor(diffInHours / 24);
    return `${diffInDays} days ago`;
  };

  const handleRevokeSession = async (sessionId: string) => {
    if (!onRevokeSession) return;
    
    setRevokingSessionId(sessionId);
    try {
      await onRevokeSession(sessionId);
    } finally {
      setRevokingSessionId(null);
    }
  };

  if (sessions.length === 0) {
    return (
      <Card className="p-6">
        <p className="text-center text-slate-600">No active sessions found.</p>
      </Card>
    );
  }

  return (
    <Card className="p-6">
      <h3 className="text-lg font-semibold mb-4">Active Sessions</h3>
      
      <div className="space-y-4">
        {sessions.map((session) => (
          <div key={session.id} className="flex items-center justify-between p-4 border rounded-lg">
            <div className="flex-1">
              <div className="flex items-center space-x-2">
                <h4 className="font-medium">{session.deviceName}</h4>
                {session.isCurrent && (
                  <Badge variant="outline" className="text-xs">
                    Current session
                  </Badge>
                )}
              </div>
              
              <div className="text-sm text-slate-600 mt-1">
                <p>{session.location}</p>
                <p>Last active: {formatLastActive(session.lastActive)}</p>
              </div>
            </div>
            
            {!session.isCurrent && (
              <AlertDialog>
                <AlertDialogTrigger asChild>
                  <Button 
                    variant="destructive" 
                    size="sm"
                    disabled={revokingSessionId === session.id}
                  >
                    {revokingSessionId === session.id ? 'Revoking...' : 'Revoke'}
                  </Button>
                </AlertDialogTrigger>
                <AlertDialogContent>
                  <AlertDialogHeader>
                    <AlertDialogTitle>Revoke Session</AlertDialogTitle>
                    <AlertDialogDescription>
                      Are you sure you want to revoke this session? The user will be logged out from {session.deviceName} and will need to sign in again.
                    </AlertDialogDescription>
                  </AlertDialogHeader>
                  <AlertDialogFooter>
                    <AlertDialogCancel>Cancel</AlertDialogCancel>
                    <AlertDialogAction
                      onClick={() => handleRevokeSession(session.id)}
                    >
                      Confirm
                    </AlertDialogAction>
                  </AlertDialogFooter>
                </AlertDialogContent>
              </AlertDialog>
            )}
          </div>
        ))}
      </div>
    </Card>
  );
}