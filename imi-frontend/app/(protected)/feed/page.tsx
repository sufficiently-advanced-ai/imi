"use client";

import React, { useState, useEffect, useCallback, useMemo } from "react";
import { Card, CardContent, CardHeader } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { PageHeader } from "@/components/ui/page-header";
import { PageContainer } from "@/components/ui/page-container";
import {
  RefreshCw,
  AlertCircle,
  Zap,
  CircleDot,
  CheckSquare,
  Lightbulb,
  KeyRound,
  ArrowRight,
  Clock,
  Users,
} from "lucide-react";
import {
  fetchSignalFeed,
  type Signal,
  type SignalFeedResponse,
} from "@/lib/api/signals";
import MeetingViewer from "@/components/meetings/MeetingViewer";

// Signal type configuration
const SIGNAL_CONFIG: Record<
  string,
  {
    icon: React.ComponentType<{ className?: string }>;
    label: string;
    color: string;
    accent: string;
    badgeVariant: "default" | "blue" | "success" | "warning" | "secondary";
  }
> = {
  decision: {
    icon: CircleDot,
    label: "Decision",
    color: "text-blue-500 dark:text-blue-400",
    accent: "border-l-blue-500 dark:border-l-blue-400",
    badgeVariant: "blue",
  },
  action_item: {
    icon: CheckSquare,
    label: "Action Item",
    color: "text-amber-500 dark:text-amber-400",
    accent: "border-l-amber-500 dark:border-l-amber-400",
    badgeVariant: "warning",
  },
  key_point: {
    icon: KeyRound,
    label: "Key Point",
    color: "text-emerald-500 dark:text-emerald-400",
    accent: "border-l-emerald-500 dark:border-l-emerald-400",
    badgeVariant: "success",
  },
  insight: {
    icon: Lightbulb,
    label: "Insight",
    color: "text-purple-500 dark:text-purple-400",
    accent: "border-l-purple-500 dark:border-l-purple-400",
    badgeVariant: "secondary",
  },
};

type FilterType = "all" | "decision" | "action_item" | "key_point" | "insight";

// --- Components ---

function FeedStats({
  totalSignals,
  totalMeetings,
  loading,
}: {
  totalSignals: number;
  totalMeetings: number;
  loading: boolean;
}) {
  if (loading) {
    return (
      <div className="flex items-center gap-6">
        <Skeleton className="h-5 w-32" />
        <Skeleton className="h-5 w-32" />
      </div>
    );
  }

  return (
    <div className="flex items-center gap-4 text-sm text-muted-foreground">
      <span>
        <span className="font-semibold text-foreground">{totalSignals}</span>{" "}
        signals from{" "}
        <span className="font-semibold text-foreground">{totalMeetings}</span>{" "}
        meetings
      </span>
    </div>
  );
}

function FilterBar({
  active,
  onChange,
}: {
  active: FilterType;
  onChange: (f: FilterType) => void;
}) {
  const filters: { label: string; value: FilterType; icon?: React.ComponentType<{ className?: string }> }[] = [
    { label: "All Signals", value: "all" },
    { label: "Decisions", value: "decision", icon: CircleDot },
    { label: "Action Items", value: "action_item", icon: CheckSquare },
    { label: "Key Points", value: "key_point", icon: KeyRound },
    { label: "Insights", value: "insight", icon: Lightbulb },
  ];

  return (
    <div className="flex flex-wrap gap-2">
      {filters.map((f) => {
        const Icon = f.icon;
        return (
          <Button
            key={f.value}
            variant={active === f.value ? "default" : "outline"}
            size="sm"
            onClick={() => onChange(f.value)}
            className="gap-1.5"
          >
            {Icon && <Icon className="h-3.5 w-3.5" />}
            {f.label}
          </Button>
        );
      })}
    </div>
  );
}

function SignalCard({ signal }: { signal: Signal }) {
  const config = SIGNAL_CONFIG[signal.type] || SIGNAL_CONFIG.key_point;
  const Icon = config.icon;

  const meetingTime = useMemo(() => {
    try {
      const date = new Date(signal.source_timestamp);
      return date.toLocaleTimeString("en-US", {
        hour: "numeric",
        minute: "2-digit",
      });
    } catch {
      return "";
    }
  }, [signal.source_timestamp]);

  // Get participant display names from entities
  const participantNames = useMemo(() => {
    const parts = signal.entities?.participants || [];
    return parts
      .map((p) =>
        p
          .replace("person-", "")
          .split("-")
          .map((w) => w.charAt(0).toUpperCase() + w.slice(1))
          .join(" "),
      )
      .slice(0, 3);
  }, [signal.entities]);

  const signalContent = (
    <div className="flex gap-3">
      {/* Icon */}
      <div className={`mt-0.5 flex-shrink-0 ${config.color}`}>
        <Icon className="h-[18px] w-[18px]" />
      </div>

      {/* Content */}
      <div className="flex-1 min-w-0 space-y-1">
        {/* Type badge + content */}
        <div className="flex items-start gap-2">
          <Badge variant={config.badgeVariant} className="text-[10px] px-1.5 py-0 flex-shrink-0 mt-0.5">
            {config.label.toUpperCase()}
          </Badge>
          <p className="text-sm text-foreground leading-snug line-clamp-2">
            {signal.content}
          </p>
        </div>

        {/* Action item status & owner */}
        {signal.type === "action_item" && (signal.owner || signal.status) && (
          <div className="flex items-center gap-2 text-xs text-muted-foreground">
            {signal.owner && (
              <span className="flex items-center gap-1">
                <Users className="h-3 w-3" />
                {signal.owner}
              </span>
            )}
            {signal.status && (
              <Badge
                variant={signal.status === "done" ? "success" : "outline"}
                className="text-[10px] px-1.5 py-0"
              >
                {signal.status}
              </Badge>
            )}
          </div>
        )}

        {/* Source meeting */}
        <div className="flex items-center gap-2 text-xs text-muted-foreground">
          <span className="truncate max-w-[240px]">
            {signal.source_meeting_title || "Untitled Meeting"}
          </span>
          {meetingTime && (
            <>
              <span className="text-muted-foreground/40">&middot;</span>
              <span className="flex items-center gap-0.5">
                <Clock className="h-3 w-3" />
                {meetingTime}
              </span>
            </>
          )}
          {participantNames.length > 0 && (
            <>
              <span className="text-muted-foreground/40">&middot;</span>
              <span className="truncate max-w-[200px]">
                {participantNames.join(", ")}
              </span>
            </>
          )}
        </div>
      </div>

      {/* Link arrow */}
      <div className="flex-shrink-0 opacity-0 group-hover:opacity-100 transition-opacity self-center">
        <ArrowRight className="h-4 w-4 text-muted-foreground" />
      </div>
    </div>
  );

  const cardClasses = `group py-3 border-b border-border/40 last:border-b-0 hover:bg-accent/20 transition-colors -mx-4 px-4 rounded-sm border-l-2 ${config.accent}`;

  // If we have a source meeting ID, wrap in MeetingViewer for click-to-open
  if (signal.source_meeting_id) {
    return (
      <MeetingViewer
        botId={signal.source_meeting_id}
        meetingTitle={signal.source_meeting_title || "Untitled Meeting"}
        trigger={
          <div className={`${cardClasses} cursor-pointer`}>
            {signalContent}
          </div>
        }
      />
    );
  }

  return (
    <div className={cardClasses}>
      {signalContent}
    </div>
  );
}

function DaySectionSkeleton() {
  return (
    <Card>
      <CardHeader className="pb-3">
        <Skeleton className="h-4 w-24" />
      </CardHeader>
      <CardContent className="pt-0">
        {[1, 2, 3].map((i) => (
          <div key={i} className="py-3 border-b border-border/40 last:border-b-0">
            <div className="flex gap-3">
              <Skeleton className="h-5 w-5 rounded mt-0.5" />
              <div className="flex-1 space-y-2">
                <div className="flex items-start gap-2">
                  <Skeleton className="h-4 w-16 rounded-full" />
                  <Skeleton className="h-4 w-full max-w-md" />
                </div>
                <Skeleton className="h-3 w-48" />
              </div>
            </div>
          </div>
        ))}
      </CardContent>
    </Card>
  );
}

function EmptyState() {
  return (
    <Card>
      <CardContent className="py-16 text-center">
        <div className="inline-flex items-center justify-center h-16 w-16 rounded-full bg-primary/10 mb-6">
          <Zap className="h-8 w-8 text-primary" />
        </div>
        <h2 className="text-xl font-semibold text-foreground mb-3">
          Your Signal Feed
        </h2>
        <p className="text-muted-foreground max-w-md mx-auto mb-2">
          As imi captures meetings, decisions, action items, and key points will
          appear here in a reverse-chronological feed.
        </p>
        <p className="text-sm text-muted-foreground/70 max-w-md mx-auto">
          You&apos;ll see signals from all team meetings &mdash; including ones
          you weren&apos;t in.
        </p>
      </CardContent>
    </Card>
  );
}

// --- Main Page ---

export default function SignalFeedPage() {
  const [feedData, setFeedData] = useState<SignalFeedResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [activeFilter, setActiveFilter] = useState<FilterType>("all");

  const loadFeed = useCallback(async (filter: FilterType) => {
    setLoading(true);
    setError(null);
    try {
      const signalType = filter === "all" ? undefined : filter;
      const data = await fetchSignalFeed(signalType, 200);
      setFeedData(data);
    } catch (err) {
      const message = err instanceof Error ? err.message : "Failed to load signal feed";
      setError(message);
      console.error("Error loading signal feed:", err);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    loadFeed(activeFilter);
  }, [activeFilter, loadFeed]);

  const handleFilterChange = (filter: FilterType) => {
    setActiveFilter(filter);
  };

  const handleRefresh = () => {
    loadFeed(activeFilter);
  };

  const renderContent = () => {
    if (loading) {
      return (
        <div className="space-y-4">
          <DaySectionSkeleton />
          <DaySectionSkeleton />
        </div>
      );
    }

    if (error) {
      return (
        <Card className="border-destructive/50">
          <CardContent className="py-12 text-center">
            <AlertCircle className="h-12 w-12 mx-auto text-destructive/60 mb-4" />
            <div className="text-lg font-semibold text-foreground mb-2">
              Unable to Load Signal Feed
            </div>
            <div className="text-sm text-muted-foreground mb-6 max-w-md mx-auto">
              {error}
            </div>
            <Button onClick={handleRefresh} variant="default">
              <RefreshCw className="h-4 w-4 mr-2" />
              Try Again
            </Button>
          </CardContent>
        </Card>
      );
    }

    if (!feedData || feedData.days.length === 0) {
      return <EmptyState />;
    }

    return (
      <div className="space-y-4">
        {feedData.days.map((day) => (
          <Card key={day.date}>
            <CardHeader className="pb-2">
              <h2 className="text-xs font-semibold text-muted-foreground uppercase tracking-wide">
                {day.label}
              </h2>
            </CardHeader>
            <CardContent className="pt-0">
              {day.signals.map((signal) => (
                <SignalCard key={signal.id} signal={signal} />
              ))}
            </CardContent>
          </Card>
        ))}
      </div>
    );
  };

  return (
    <PageContainer className="space-y-6">
      <PageHeader
        title="Signal Feed"
        description="Live feed of decisions, action items, and insights"
        actions={
          <Button
            onClick={handleRefresh}
            variant="outline"
            size="icon"
            disabled={loading}
          >
            <RefreshCw className={`h-4 w-4 ${loading ? "animate-spin" : ""}`} />
          </Button>
        }
      />

      <FeedStats
        totalSignals={feedData?.total_signals ?? 0}
        totalMeetings={feedData?.total_meetings ?? 0}
        loading={loading}
      />

      <FilterBar active={activeFilter} onChange={handleFilterChange} />

      {renderContent()}
    </PageContainer>
  );
}
