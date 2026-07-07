/**
 * Entities Layout
 *
 * Provides shared layout for entity management pages.
 * DomainProvider is inherited from the parent (protected) layout.
 */

'use client';

import React from 'react';

interface EntitiesLayoutProps {
  children: React.ReactNode;
}

export default function EntitiesLayout({ children }: EntitiesLayoutProps) {
  return (
    <div className="min-h-screen bg-background">
      {children}
    </div>
  );
}
