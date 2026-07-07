'use client';

import { Users, Calendar } from "lucide-react";
import { Skeleton } from "@/components/ui/skeleton";
import type { KnowledgeStats } from "@/lib/api/knowledge-explorer";

interface StatsBarProps {
  stats: KnowledgeStats | null;
  isLoading: boolean;
}

export function StatsBar({ stats, isLoading }: StatsBarProps) {
  if (isLoading) {
    return (
      <div className="flex items-center gap-4">
        <Skeleton className="h-4 w-40" />
      </div>
    );
  }

  if (!stats) return null;

  return (
    <div className="flex items-center gap-4 text-sm text-muted-foreground">
      <span className="inline-flex items-center gap-1.5">
        <Users className="h-3.5 w-3.5" />
        <span className="font-medium text-foreground">{stats.total_entities}</span> entities
      </span>
      <span className="text-border">|</span>
      <span className="inline-flex items-center gap-1.5">
        <Calendar className="h-3.5 w-3.5" />
        <span className="font-medium text-foreground">{stats.total_meetings}</span> meetings
      </span>
    </div>
  );
}
