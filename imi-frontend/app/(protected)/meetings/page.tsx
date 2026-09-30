"use client";

import React, { useState, useEffect, useCallback } from "react";
import { Card, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { PageHeader } from "@/components/ui/page-header";
import { PageContainer } from "@/components/ui/page-container";
import {
  RefreshCw,
  AlertCircle,
  CalendarDays,
  Users,
  Clock,
  ArrowRight,
  Search,
} from "lucide-react";
import {
  fetchMeetingHistoryList,
  fetchMeetingHistoryStats,
} from "@/lib/api/meetings";
import type {
  MeetingHistoryItem,
  MeetingHistoryStats,
  SignalCounts,
} from "@/lib/types/meeting-history";
import MeetingViewer from "@/components/meetings/MeetingViewer";
import { useDomain } from "@/contexts/DomainContext";

const PAGE_SIZE = 50;

// ---- helpers ----

function formatDate(ts: string | null): string {
  if (!ts) return "Undated";
  try {
    return new Date(ts).toLocaleDateString("en-US", {
      weekday: "short",
      month: "short",
      day: "numeric",
      year: "numeric",
    });
  } catch {
    return ts;
  }
}

function describeStats(stats: MeetingHistoryStats | null): string | undefined {
  if (!stats || stats.total_meetings === 0) return undefined;
  const parts = [
    `${stats.total_meetings} meetings`,
    `${stats.meetings_summarized} summarized`,
    `${stats.total_signals} signals`,
  ];
  return parts.join(" · ");
}

const SIGNAL_LABELS: [keyof SignalCounts, string][] = [
  ["decision", "decisions"],
  ["action_item", "actions"],
  ["key_point", "key points"],
  ["insight", "insights"],
];

// ---- sub-components ----

function MeetingRow({
  meeting,
  onClick,
}: {
  meeting: MeetingHistoryItem;
  onClick: () => void;
}) {
  const counts = SIGNAL_LABELS.filter(([k]) => meeting.signal_counts[k] > 0);

  return (
    <div
      className="group py-3 border-b border-border/40 last:border-b-0 hover:bg-accent/20 transition-colors duration-150 -mx-4 px-4 rounded-sm cursor-pointer"
      onClick={onClick}
      role="button"
      tabIndex={0}
      onKeyDown={(e) => {
        if (e.key === "Enter" || e.key === " ") onClick();
      }}
    >
      <div className="flex gap-3">
        <div className="flex-1 min-w-0 space-y-1.5">
          <div className="flex items-start gap-2 flex-wrap">
            <p className="text-sm font-medium text-foreground leading-snug">
              {meeting.title}
            </p>
            {!meeting.summarized && (
              <Badge variant="outline" className="text-[10px] px-1.5 py-0">
                Transcript only
              </Badge>
            )}
            {meeting.lane === "library" && (
              <Badge variant="secondary" className="text-[10px] px-1.5 py-0">
                Library
              </Badge>
            )}
          </div>

          {meeting.purpose && (
            <p className="text-sm text-muted-foreground leading-snug line-clamp-2">
              {meeting.purpose}
            </p>
          )}

          {meeting.key_points.length > 0 && (
            <ul className="text-xs text-muted-foreground list-disc list-inside space-y-0.5">
              {meeting.key_points.slice(0, 3).map((kp, i) => (
                <li key={i} className="truncate">
                  {kp}
                </li>
              ))}
            </ul>
          )}

          <div className="flex items-center gap-3 text-xs text-muted-foreground flex-wrap">
            <span className="flex items-center gap-1">
              <Clock className="h-3 w-3" />
              {formatDate(meeting.start_time)}
            </span>
            {meeting.participants.length > 0 && (
              <span className="flex items-center gap-1 truncate max-w-[360px]">
                <Users className="h-3 w-3 flex-shrink-0" />
                {meeting.participants.join(", ")}
              </span>
            )}
            {counts.length > 0 && (
              <>
                <span className="text-muted-foreground/40">&middot;</span>
                <span>
                  {counts
                    .map(([k, label]) => `${meeting.signal_counts[k]} ${label}`)
                    .join(", ")}
                </span>
              </>
            )}
          </div>
        </div>

        <div className="flex-shrink-0 opacity-0 group-hover:opacity-100 transition-opacity self-center">
          <ArrowRight className="h-4 w-4 text-muted-foreground" />
        </div>
      </div>
    </div>
  );
}

function MeetingsSkeleton() {
  return (
    <Card>
      <CardContent className="pt-4">
        {[1, 2, 3, 4].map((i) => (
          <div key={i} className="py-3 border-b border-border/40 last:border-b-0 space-y-2">
            <Skeleton className="h-4 w-64" />
            <Skeleton className="h-3 w-full max-w-md" />
            <Skeleton className="h-3 w-48" />
          </div>
        ))}
      </CardContent>
    </Card>
  );
}

function EmptyState({ filtered }: { filtered: boolean }) {
  return (
    <Card>
      <CardContent className="py-16 text-center">
        <div className="inline-flex items-center justify-center h-16 w-16 rounded-full bg-primary/10 mb-6">
          <CalendarDays className="h-8 w-8 text-primary" />
        </div>
        <h2 className="text-xl font-semibold text-foreground mb-3">
          {filtered ? "No meetings match this search" : "No meetings yet"}
        </h2>
        <p className="text-muted-foreground max-w-md mx-auto text-sm">
          {filtered
            ? "Try different words, or clear the search."
            : "Meetings appear here as transcripts are ingested, each with a summary and the signals extracted from it."}
        </p>
      </CardContent>
    </Card>
  );
}

// ---- page ----

export default function MeetingsPage() {
  const { getNavLabel } = useDomain();
  const [items, setItems] = useState<MeetingHistoryItem[]>([]);
  const [total, setTotal] = useState(0);
  const [nextCursor, setNextCursor] = useState<string | null>(null);
  const [stats, setStats] = useState<MeetingHistoryStats | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [appliedQuery, setAppliedQuery] = useState("");
  const [selected, setSelected] = useState<MeetingHistoryItem | null>(null);

  const load = useCallback(async (q: string) => {
    setLoading(true);
    setError(null);
    try {
      const res = await fetchMeetingHistoryList({ q: q || undefined, page_size: PAGE_SIZE });
      setItems(res.items);
      setTotal(res.total);
      setNextCursor(res.next_cursor);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load meetings");
    } finally {
      setLoading(false);
    }
  }, []);

  const loadStats = useCallback(async () => {
    try {
      setStats(await fetchMeetingHistoryStats());
    } catch {
      // stats are non-critical — silently fail
    }
  }, []);

  const loadMore = async () => {
    if (!nextCursor) return;
    setLoadingMore(true);
    try {
      const res = await fetchMeetingHistoryList({
        q: appliedQuery || undefined,
        page_size: PAGE_SIZE,
        cursor: nextCursor,
      });
      setItems((prev) => [...prev, ...res.items]);
      setNextCursor(res.next_cursor);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load meetings");
    } finally {
      setLoadingMore(false);
    }
  };

  useEffect(() => {
    load(appliedQuery);
  }, [appliedQuery, load]);

  useEffect(() => {
    loadStats();
  }, [loadStats]);

  // Debounce typing into the applied query.
  useEffect(() => {
    const t = setTimeout(() => setAppliedQuery(query.trim()), 300);
    return () => clearTimeout(t);
  }, [query]);

  const handleRefresh = () => {
    load(appliedQuery);
    loadStats();
  };

  const renderContent = () => {
    if (loading) return <MeetingsSkeleton />;

    if (error) {
      return (
        <Card className="border-destructive/50">
          <CardContent className="py-12 text-center">
            <AlertCircle className="h-12 w-12 mx-auto text-destructive/60 mb-4" />
            <div className="text-lg font-semibold text-foreground mb-2">
              Unable to Load Meetings
            </div>
            <div className="text-sm text-muted-foreground mb-6 max-w-md mx-auto">{error}</div>
            <Button onClick={handleRefresh} variant="default">
              <RefreshCw className="h-4 w-4 mr-2" />
              Try Again
            </Button>
          </CardContent>
        </Card>
      );
    }

    if (items.length === 0) return <EmptyState filtered={appliedQuery !== ""} />;

    return (
      <Card>
        <CardContent className="pt-4">
          {items.map((m) => (
            <MeetingRow key={m.bot_id} meeting={m} onClick={() => setSelected(m)} />
          ))}
          {nextCursor && (
            <div className="pt-4 text-center">
              <Button variant="outline" size="sm" onClick={loadMore} disabled={loadingMore}>
                {loadingMore ? "Loading…" : `Load more (${total - items.length} left)`}
              </Button>
            </div>
          )}
        </CardContent>
      </Card>
    );
  };

  return (
    <>
      <PageContainer className="space-y-6">
        <PageHeader
          title={getNavLabel("intelligence", "/meetings", "Meetings")}
          description={describeStats(stats)}
          actions={
            <Button
              onClick={handleRefresh}
              variant="outline"
              size="icon"
              disabled={loading}
              aria-label="Refresh meetings"
            >
              <RefreshCw className={`h-4 w-4 ${loading ? "animate-spin" : ""}`} />
            </Button>
          }
        />

        <div className="relative max-w-md">
          <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground" />
          <Input
            type="search"
            placeholder="Search titles, people, topics…"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            className="pl-8"
            aria-label="Search meetings"
          />
        </div>

        {renderContent()}
      </PageContainer>

      {selected && (
        <MeetingViewer
          botId={selected.bot_id}
          meetingTitle={selected.title}
          open={selected !== null}
          onOpenChange={(open) => {
            if (!open) setSelected(null);
          }}
        />
      )}
    </>
  );
}
