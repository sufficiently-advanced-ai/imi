'use client';

import { useAuth } from '@/lib/hooks/useAuth';
import { useState, useRef, useEffect } from 'react';
import { Button } from '@/components/ui/button';
// Removed withBasePath import - using direct basePath for client-side redirects

export function UserMenu() {
  const { user, loading } = useAuth();
  const [isOpen, setIsOpen] = useState(false);
  const [isLoggingOut, setIsLoggingOut] = useState(false);
  const [logoutError, setLogoutError] = useState<string | null>(null);
  const menuRef = useRef<HTMLDivElement>(null);

  // Close menu when clicking outside
  useEffect(() => {
    const handleClickOutside = (event: MouseEvent) => {
      if (menuRef.current && !menuRef.current.contains(event.target as Node)) {
        setIsOpen(false);
      }
    };

    if (isOpen) {
      document.addEventListener('mousedown', handleClickOutside);
    }

    return () => {
      document.removeEventListener('mousedown', handleClickOutside);
    };
  }, [isOpen]);

  if (loading || !user) {
    return null;
  }

  const handleLogout = async () => {
    try {
      setIsLoggingOut(true);
      // Call backend logout endpoint directly
      const response = await fetch('/api/auth/logout', {
        method: 'GET',
        credentials: 'include',
      });
      
      if (response.ok || response.status === 302) {
        // Redirect to home page after logout
        window.location.href = '/';
      } else {
        console.error('Logout failed');
        setLogoutError('Failed to logout. Please try again.');
      }
    } catch (error) {
      console.error('Logout failed:', error);
      setLogoutError('Network error. Please try again.');
    } finally {
      setIsLoggingOut(false);
    }
  };

  return (
    <div className="relative" ref={menuRef}>
      <Button 
        variant="ghost" 
        onClick={() => setIsOpen(!isOpen)}
        className="relative cursor-pointer"
      >
        {user.firstName && user.lastName 
          ? `${user.firstName} ${user.lastName}` 
          : user.email}
      </Button>
      
      {isOpen && (
        <div className="absolute right-0 mt-2 w-56 bg-white shadow-lg rounded-md border border-gray-200 z-50">
          <div className="py-1">
            <div className="px-4 py-2 border-b">
              {user.firstName && user.lastName && (
                <div className="text-sm font-medium text-gray-900">
                  {user.firstName} {user.lastName}
                </div>
              )}
              <div className="text-sm text-gray-500">
                {user.email}
              </div>
            </div>
            <button
              onClick={handleLogout}
              disabled={isLoggingOut}
              className="w-full text-left px-4 py-2 text-sm hover:bg-gray-100 cursor-pointer disabled:opacity-50"
            >
              {isLoggingOut ? 'Signing out...' : 'Sign Out'}
            </button>
            {logoutError && (
              <div className="px-4 py-2 text-sm text-red-600 border-t">
                {logoutError}
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}