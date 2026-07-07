'use client';

import { ReactNode } from 'react';
import { KnowledgeProvider } from './KnowledgeContext';
import { ChatProvider } from './ChatContext';

export * from './KnowledgeContext';
export * from './ChatContext';

interface AppProviderProps {
  children: ReactNode;
}

// Combined provider that wraps all context providers
export function AppProvider({ children }: AppProviderProps) {
  return (
    <KnowledgeProvider>
      <ChatProvider>{children}</ChatProvider>
    </KnowledgeProvider>
  );
}