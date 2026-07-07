"use client";

import { MobileNav } from "@/components/navigation";
import { AddTranscriptDialog } from "@/components/ingest/AddTranscriptDialog";
import { ThemeToggle } from "@/components/theme-toggle";

export function TopBar() {
  return (
    <header className="flex h-14 shrink-0 items-center gap-2 border-b px-4">
      <div className="md:hidden">
        <MobileNav />
      </div>
      <div className="flex-1" />
      <AddTranscriptDialog />
      <ThemeToggle />
    </header>
  );
}
