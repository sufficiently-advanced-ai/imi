'use client';

import { useEffect } from "react";

export default function Home() {
  useEffect(() => {
    // Navigate to /overview relative to the current path
    // This works correctly whether we're at root or under a prefix
    window.location.replace('./overview/');
  }, []);

  // Also add a fallback for cases where React doesn't hydrate quickly
  if (typeof window !== 'undefined') {
    // Immediate redirect without waiting for React
    setTimeout(() => {
      window.location.replace('./overview/');
    }, 100);
  }

  return (
    <div className="flex items-center justify-center h-full">
      <div className="text-lg">Redirecting...</div>
    </div>
  );
}