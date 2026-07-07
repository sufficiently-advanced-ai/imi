"use client";

import React, { useState, useEffect, useCallback, Suspense } from "react";
import { useSearchParams } from "next/navigation";
import { Card, CardContent, CardHeader } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { PageHeader } from "@/components/ui/page-header";
import {
  RefreshCw,
  AlertCircle,
  Scale,
  User,
  Building2,
  Clock,
  ArrowRight,
} from "lucide-react";
import {
  fetchDecisions,
  fetchDecisionStats,
  decisionStateBadgeVariant,
  type Decision,
  type DecisionState,
  type DecisionListResponse,
  type DecisionStats,
} from "@/lib/api/decisions";
import { useReviewCounts } from "@/lib/hooks/useReviewCounts";
import { useDomain } from "@/contexts/DomainContext";
import { GovernanceLadder } from "@/components/decisions/GovernanceLadder";
import { DecisionDetailDialog } from "@/components/decisions/DecisionDetailDialog";
import { ReviewQueue } from "@/components/decisions/ReviewQueue";
import { ExportConstitutionButton } from "@/components/decisions/ExportConstitutionButton";
import { PageContainer } from "@/components/ui/page-container";

// ---- helpers ----

type FilterState = "all" | "review" | DecisionState;

function formatDate(ts: string | null): string {
  if (!ts) return "";
  try {
    return new Date(ts).toLocaleDateString("en-US", {
      month: "short",
      day: "numeric",
      year: "numeric",
    });
  } catch {
    return "";
  }
}

// ---- sub-components ----

function FilterBar({
  active,
  counts,
  reviewCount,
  onChange,
}: {
  active: FilterState;
  counts: Record<string, number>;
  reviewCount: number;
  onChange: (f: FilterState) => void;
}) {
  const filters: { label: string; value: FilterState }[] = [
    { label: "All", value: "all" },
    { label: "Active", value: "active" },
    { label: "Candidate", value: "candidate" },
    { label: "Stale", value: "stale" },
    { label: "Superseded", value: "superseded" },
    { label: "Rejected", value: "rejected" },
  ];

  return (
    <div className="flex flex-wrap gap-2">
      {filters.map((f) => {
        const count =
          f.value === "all"
            ? Object.values(counts).reduce((a, b) => a + b, 0)
            : counts[f.value] ?? 0;

        return (
          <Button
            key={f.value}
            variant={active === f.value ? "default" : "outline"}
            size="sm"
            onClick={() => onChange(f.value)}
            className="gap-1.5"
          >
            {f.label}
            {count > 0 && (
              <span
                className={`text-[10px] font-semibold px-1 py-0 rounded-full leading-tight ${
                  active === f.value
                    ? "bg-primary-foreground/20 text-primary-foreground"
                    : "bg-muted text-muted-foreground"
                }`}
              >
                {count}
              </span>
            )}
          </Button>
        );
      })}
      <div className="w-px self-stretch bg-border mx-1" aria-hidden />
      <Button
        variant={active === "review" ? "default" : "outline"}
        size="sm"
        onClick={() => onChange("review")}
        className="gap-1.5"
      >
        <Scale className="h-3.5 w-3.5" />
        Review
        {reviewCount > 0 && (
          <span
            className={`text-[10px] font-semibold px-1 py-0 rounded-full leading-tight ${
              active === "review"
                ? "bg-primary-foreground/20 text-primary-foreground"
                : "bg-muted text-muted-foreground"
            }`}
          >
            {reviewCount}
          </span>
        )}
      </Button>
    </div>
  );
}

function DecisionCard({
  decision,
  onClick,
}: {
  decision: Decision;
  onClick: () => void;
}) {
  const isSuperseded = decision.state === "superseded";

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
        {/* State accent dot */}
        <div className="mt-1.5 flex-shrink-0">
          <div
            className={`h-2 w-2 rounded-full ${
              decision.state === "active"
                ? "bg-green-500"
                : decision.state === "stale"
                  ? "bg-yellow-500"
                  : decision.state === "rejected"
                    ? "bg-destructive"
                    : "bg-muted-foreground/40"
            }`}
          />
        </div>

        {/* Content */}
        <div className="flex-1 min-w-0 space-y-1.5">
          {/* State badge + content */}
          <div className="flex items-start gap-2 flex-wrap">
            <Badge
              variant={decisionStateBadgeVariant(decision.state)}
              className="text-[10px] px-1.5 py-0 flex-shrink-0 mt-0.5"
            >
              {decision.state.toUpperCase()}
            </Badge>
            <p
              className={`text-sm text-foreground leading-snug line-clamp-2 ${
                isSuperseded ? "line-through opacity-50" : ""
              }`}
            >
              {decision.content}
            </p>
          </div>

          {/* Governance ladder */}
          <GovernanceLadder
            canUseAsEvidence={decision.can_use_as_evidence}
            canUseAsInstruction={decision.can_use_as_instruction}
          />

          {/* Meta row */}
          <div className="flex items-center gap-3 text-xs text-muted-foreground flex-wrap">
            {decision.owner && (
              <span className="flex items-center gap-1">
                <User className="h-3 w-3" />
                {decision.owner}
              </span>
            )}
            {decision.client_id && (
              <span className="flex items-center gap-1">
                <Building2 className="h-3 w-3" />
                {decision.client_id}
              </span>
            )}
            {decision.source_meeting_title && (
              <span className="truncate max-w-[240px]">
                {decision.source_meeting_title}
              </span>
            )}
            {decision.source_timestamp && (
              <>
                <span className="text-muted-foreground/40">&middot;</span>
                <span className="flex items-center gap-0.5">
                  <Clock className="h-3 w-3" />
                  {formatDate(decision.source_timestamp)}
                </span>
              </>
            )}
          </div>
        </div>

        {/* Arrow */}
        <div className="flex-shrink-0 opacity-0 group-hover:opacity-100 transition-opacity self-center">
          <ArrowRight className="h-4 w-4 text-muted-foreground" />
        </div>
      </div>
    </div>
  );
}

function DecisionsSkeleton() {
  return (
    <Card>
      <CardHeader className="pb-3">
        <Skeleton className="h-4 w-24" />
      </CardHeader>
      <CardContent className="pt-0">
        {[1, 2, 3, 4].map((i) => (
          <div
            key={i}
            className="py-3 border-b border-border/40 last:border-b-0"
          >
            <div className="flex gap-3">
              <Skeleton className="h-2 w-2 rounded-full mt-1.5" />
              <div className="flex-1 space-y-2">
                <div className="flex items-start gap-2">
                  <Skeleton className="h-4 w-16 rounded-full" />
                  <Skeleton className="h-4 w-full max-w-md" />
                </div>
                <Skeleton className="h-3 w-32" />
                <Skeleton className="h-3 w-48" />
              </div>
            </div>
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
          <Scale className="h-8 w-8 text-primary" />
        </div>
        <h2 className="text-xl font-semibold text-foreground mb-3">
          {filtered ? "No decisions match this filter" : "No decisions yet"}
        </h2>
        <p className="text-muted-foreground max-w-md mx-auto text-sm">
          {filtered
            ? "Try selecting a different state filter, or view all decisions."
            : "Decisions appear here as meetings are processed. They track what was decided, by whom, and whether they can be used as evidence or operating instructions."}
        </p>
      </CardContent>
    </Card>
  );
}

// ---- Valid filter values for URL param validation ----

const VALID_FILTER_VALUES: FilterState[] = [
  "all",
  "review",
  "active",
  "candidate",
  "stale",
  "superseded",
  "rejected",
];

function isValidFilter(value: string | null): value is FilterState {
  return value !== null && (VALID_FILTER_VALUES as string[]).includes(value);
}

// ---- Main page content (needs useSearchParams → must be inside Suspense) ----

function DecisionsContent() {
  const searchParams = useSearchParams();
  const paramFilter = searchParams.get("filter");
  const initialFilter: FilterState = isValidFilter(paramFilter) ? paramFilter : "all";
  const { getNavLabel } = useDomain();

  const [data, setData] = useState<DecisionListResponse | null>(null);
  const [stats, setStats] = useState<DecisionStats | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [activeFilter, setActiveFilter] = useState<FilterState>(initialFilter);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [dialogOpen, setDialogOpen] = useState(false);
  const { count: reviewCountRaw, refresh: refreshReviewCount } = useReviewCounts();
  const reviewCount = reviewCountRaw ?? 0;

  const loadDecisions = useCallback(async (filter: FilterState) => {
    // The review queue fetches its own data; "review" is not a DecisionState.
    if (filter === "review") return;
    setLoading(true);
    setError(null);
    try {
      const result = await fetchDecisions(
        filter === "all" ? {} : { state: filter as DecisionState },
      );
      setData(result);
    } catch (err) {
      const msg =
        err instanceof Error ? err.message : "Failed to load decisions";
      setError(msg);
    } finally {
      setLoading(false);
    }
  }, []);

  const loadStats = useCallback(async () => {
    try {
      const s = await fetchDecisionStats();
      setStats(s);
    } catch {
      // stats are non-critical — silently fail
    }
  }, []);

  useEffect(() => {
    loadDecisions(activeFilter);
  }, [activeFilter, loadDecisions]);

  useEffect(() => {
    loadStats();
  }, [loadStats]);

  const handleRefresh = () => {
    loadDecisions(activeFilter);
    loadStats();
    refreshReviewCount();
  };

  // Confirming/dismissing a candidate changes lifecycle states (supersession
  // confirm can flip a decision to superseded; conflict confirm derives the
  // conflicting state), so refresh decisions + stats alongside the badge.
  const handleReviewActioned = useCallback(() => {
    refreshReviewCount();
    loadDecisions("all");
    loadStats();
  }, [refreshReviewCount, loadDecisions, loadStats]);

  const handleCardClick = (id: string) => {
    setSelectedId(id);
    setDialogOpen(true);
  };

  const renderContent = () => {
    if (loading) return <DecisionsSkeleton />;

    if (error) {
      return (
        <Card className="border-destructive/50">
          <CardContent className="py-12 text-center">
            <AlertCircle className="h-12 w-12 mx-auto text-destructive/60 mb-4" />
            <div className="text-lg font-semibold text-foreground mb-2">
              Unable to Load Decisions
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

    if (!data || data.decisions.length === 0) {
      return <EmptyState filtered={activeFilter !== "all"} />;
    }

    return (
      <Card>
        <CardContent className="pt-4">
          {data.decisions.map((decision) => (
            <DecisionCard
              key={decision.id}
              decision={decision}
              onClick={() => handleCardClick(decision.id)}
            />
          ))}
        </CardContent>
      </Card>
    );
  };

  return (
    <>
      <PageContainer className="space-y-6">
        <PageHeader
          title={getNavLabel("intelligence", "/decisions", "Constitution")}
          description={stats?.headline}
          actions={
            <div className="flex items-center gap-2">
              <ExportConstitutionButton />
              <Button
                onClick={handleRefresh}
                variant="outline"
                size="icon"
                disabled={loading}
                aria-label="Refresh decisions"
              >
                <RefreshCw
                  className={`h-4 w-4 ${loading ? "animate-spin" : ""}`}
                />
              </Button>
            </div>
          }
        />

        <FilterBar
          active={activeFilter}
          counts={data?.counts_by_state ?? stats?.counts_by_state ?? {}}
          reviewCount={reviewCount}
          onChange={setActiveFilter}
        />

        {activeFilter === "review" ? (
          <ReviewQueue onActioned={handleReviewActioned} />
        ) : (
          renderContent()
        )}
      </PageContainer>

      <DecisionDetailDialog
        decisionId={selectedId}
        open={dialogOpen}
        onOpenChange={setDialogOpen}
      />
    </>
  );
}

// ---- Page export — Suspense required because DecisionsContent uses useSearchParams ----

export default function DecisionsPage() {
  return (
    <Suspense>
      <DecisionsContent />
    </Suspense>
  );
}
