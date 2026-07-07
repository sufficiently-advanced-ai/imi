'use client';

import { useAuth } from '@/lib/hooks/useAuth';
import { Button } from '@/components/ui/button';
export function LoginButton() {
  const { user, loading } = useAuth();

  if (loading) {
    return (
      <Button disabled className="px-4 py-2">
        Loading...
      </Button>
    );
  }

  if (user) {
    return null; // Don't show login if already authenticated
  }

  const handleLogin = () => {
    // Use basePath directly for client-side navigation
    const basePath = process.env.NEXT_PUBLIC_BASE_PATH || '';
    window.location.href = `${basePath}/signin`;
  };

  return (
    <Button 
      onClick={handleLogin} 
      variant="default"
      className="px-4 py-2"
    >
      Sign In
    </Button>
  );
}