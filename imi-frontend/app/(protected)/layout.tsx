'use client';

import Navigation from "@/components/navigation";
import { TopBar } from "@/components/top-bar";
import { ProtectedLayout } from '@/components/auth/ProtectedLayout';
import { AppProvider } from "@/lib/context";
import { DomainProvider } from "@/contexts/DomainContext";
import { Toaster } from "@/components/ui/toaster";

export default function ProtectedRouteLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <ProtectedLayout>
      <AppProvider>
        <DomainProvider>
          <div className="flex h-screen">
            <Navigation />
            <div className="flex min-w-0 flex-1 flex-col">
              <TopBar />
              <main className="min-h-0 flex-1 overflow-auto">{children}</main>
            </div>
          </div>
          <Toaster />
        </DomainProvider>
      </AppProvider>
    </ProtectedLayout>
  );
}
