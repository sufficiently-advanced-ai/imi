'use client';

import { useAuth } from '@/lib/hooks/useAuth';
import { useEffect } from 'react';
import { AuthLoading } from './AuthLoading';

export function ProtectedLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  const { user, loading, error } = useAuth();

  useEffect(() => {
    if (!loading && !user) {
      // Use relative URL to preserve any base path
      const redirectUrl = '/signin';
      
      console.log('[ProtectedLayout] Auth check failed, redirecting', {
        currentUrl: window.location.href,
        currentPath: window.location.pathname,
        cookies: document.cookie,
        redirectUrl,
        user,
        loading,
        error: error?.message
      });
      
      // Redirect to login if not authenticated
      // Use href to preserve the base path
      window.location.href = redirectUrl;
    }
  }, [user, loading]);

  if (loading) {
    return <AuthLoading />;
  }

  if (!user) {
    return (
      <div className="flex items-center justify-center min-h-screen">
        <div>Redirecting to login...</div>
      </div>
    );
  }

  return <>{children}</>;
}