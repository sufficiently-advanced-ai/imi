"use client";

import React, { useEffect, useState, useCallback } from "react";
import {
  Card,
  CardHeader,
  CardTitle,
  CardContent,
  CardDescription,
} from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { PageContainer } from "@/components/ui/page-container";
import { PageHeader } from "@/components/ui/page-header";
import { BasePathLink } from "@/lib/utils/links";
import {
  ArrowRight,
  ChevronDown,
  ChevronRight,
  Scale,
  CheckCircle2,
  Inbox,
  FileText,
} from "lucide-react";

import {
  fetchDecisions,
  fetchDecisionStats,
  type Decision,
  type DecisionStats,
} from "@/lib/api/decisions";
import {
  fetchIngestJobs,
  fetchDelta,
  type IngestJobRecord,
  type DeltaReport,
} from "@/lib/api/ingest";
import {
  fetchLatestWeeklyDigest,
} from "@/lib/api/digest";
import { useReviewCounts } from "@/lib/hooks/useReviewCounts";

import { DecisionRow } from "@/components/decisions/DecisionRow";
import { DecisionDetailDialog } from "@/components/decisions/DecisionDetailDialog";
import { ExportConstitutionButton } from "@/components/decisions/ExportConstitutionButton";
import { AddTranscriptDialog } from "@/components/ingest/AddTranscriptDialog";
import { DeltaReportCard } from "@/components/DeltaReportCard";
import MarkdownViewer from "@/components/MarkdownViewer";

// ---------------------------------------------------------------------------
// useAsync — tiny per-card async helper. Each card owns its own
// {loading, error, data} so one failing fetch never blanks the others.
// ---------------------------------------------------------------------------

interface AsyncState<T> {
  loading: boolean;
  error: string | null;
  data: T | null;
}

function useAsync<T>(
  fn: () => Promise<T>,
  deps: React.DependencyList,
): AsyncState<T> {
  const [state, setState] = useState<AsyncState<T>>({
    loading: true,
    error: null,
    data: null,
  });

  useEffect(() => {
    let cancelled = false;
    setState({ loading: true, error: null, data: null });
    fn()
      .then((data) => {
        if (!cancelled) setState({ loading: false, error: null, data });
      })
      .catch((err) => {
        if (!cancelled) {
          setState({
            loading: false,
            error: err instanceof Error ? err.message : "Something went wrong",
            data: null,
          });
        }
      });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);

  return state;
}

// ---------------------------------------------------------------------------
// Card 1: Constitution summary
// ---------------------------------------------------------------------------

function ConstitutionCard({ onSelect }: { onSelect: (id: string) => void }) {
  const { loading, error, data } = useAsync(
    () => fetchDecisions({ state: "active", limit: 6 }),
    [],
  );

  return (
    <Card className="lg:col-span-2">
      <CardHeader className="pb-3">
        <CardTitle className="flex items-center gap-2 text-base">
          <Scale className="h-4 w-4 text-muted-foreground" />
          Constitution
        </CardTitle>
        <CardDescription>Active decisions guiding the work</CardDescription>
      </CardHeader>
      <CardContent className="pt-0">
        {loading ? (
          <div className="space-y-3">
            {[1, 2, 3, 4].map((i) => (
              <div key={i} className="space-y-2 py-1">
                <Skeleton className="h-4 w-full max-w-md" />
                <Skeleton className="h-3 w-32" />
              </div>
            ))}
          </div>
        ) : error ? (
          <p className="py-6 text-sm text-muted-foreground">
            Couldn&apos;t load decisions right now.
          </p>
        ) : !data || data.decisions.length === 0 ? (
          <div className="py-8 text-center">
            <p className="text-sm font-medium text-foreground">
              No active decisions yet
            </p>
            <p className="mt-1 text-sm text-muted-foreground">
              Ingesting transcripts builds the constitution as decisions are
              made.
            </p>
          </div>
        ) : (
          <>
            <div>
              {data.decisions.map((decision: Decision) => (
                <DecisionRow
                  key={decision.id}
                  decision={decision}
                  onClick={() => onSelect(decision.id)}
                />
              ))}
            </div>
            <div className="pt-3">
              <BasePathLink
                href="/decisions"
                className="inline-flex items-center gap-1 text-sm font-medium text-primary hover:underline"
              >
                View full constitution
                <ArrowRight className="h-3.5 w-3.5" />
              </BasePathLink>
            </div>
          </>
        )}
      </CardContent>
    </Card>
  );
}

// ---------------------------------------------------------------------------
// Card 2: Needs review
// ---------------------------------------------------------------------------

function NeedsReviewCard() {
  const { count } = useReviewCounts();

  return (
    <Card>
      <CardHeader className="pb-3">
        <CardTitle className="text-base">Needs review</CardTitle>
        <CardDescription>Items awaiting your judgment</CardDescription>
      </CardHeader>
      <CardContent className="pt-0">
        {count === null ? (
          <Skeleton className="h-12 w-20" />
        ) : count === 0 ? (
          <div className="flex items-center gap-2 py-2 text-sm text-muted-foreground">
            <CheckCircle2 className="h-4 w-4 text-green-600" />
            All caught up
          </div>
        ) : (
          <>
            <div className="text-4xl font-semibold tracking-tight text-foreground">
              {count}
            </div>
            <p className="mt-1 text-sm text-muted-foreground">
              {count === 1 ? "item" : "items"} awaiting review
            </p>
            <Button asChild size="sm" className="mt-4">
              <BasePathLink href="/decisions?filter=review">
                Review now
                <ArrowRight className="ml-1.5 h-3.5 w-3.5" />
              </BasePathLink>
            </Button>
          </>
        )}
      </CardContent>
    </Card>
  );
}

// ---------------------------------------------------------------------------
// Card 3: Latest intake
// ---------------------------------------------------------------------------

function jobTitle(job: IngestJobRecord): string {
  return job.title || "Untitled transcript";
}

function jobDate(job: IngestJobRecord): string {
  if (!job.created_at) return "";
  try {
    return new Date(job.created_at).toLocaleDateString("en-US", {
      month: "short",
      day: "numeric",
      year: "numeric",
    });
  } catch {
    return "";
  }
}

function LatestIntakeCard() {
  const { loading, error, data } = useAsync(() => fetchIngestJobs(), []);

  // Newest job — defensively sort by created_at desc.
  const newest: IngestJobRecord | null =
    data && data.length > 0
      ? [...data].sort((a, b) =>
          (b.created_at ?? "").localeCompare(a.created_at ?? ""),
        )[0]
      : null;

  const [delta, setDelta] = useState<DeltaReport | null>(null);
  const [deltaLoading, setDeltaLoading] = useState(false);
  const [expanded, setExpanded] = useState(false);

  const newestId = newest?.job_id ?? null;
  const newestStatus = newest?.status ?? null;

  useEffect(() => {
    // Delta is only available once the pipeline has completed.
    if (!newestId || newestStatus !== "completed") {
      setDelta(null);
      return;
    }
    let cancelled = false;
    setDeltaLoading(true);
    fetchDelta(newestId)
      .then((report) => {
        if (!cancelled) setDelta(report);
      })
      .catch(() => {
        // Guard failures silently — delta may not be ready yet.
        if (!cancelled) setDelta(null);
      })
      .finally(() => {
        if (!cancelled) setDeltaLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [newestId, newestStatus]);

  return (
    <Card>
      <CardHeader className="pb-3">
        <CardTitle className="flex items-center gap-2 text-base">
          <Inbox className="h-4 w-4 text-muted-foreground" />
          Latest intake
        </CardTitle>
      </CardHeader>
      <CardContent className="pt-0">
        {loading ? (
          <div className="space-y-2">
            <Skeleton className="h-4 w-40" />
            <Skeleton className="h-3 w-24" />
          </div>
        ) : error ? (
          <p className="py-2 text-sm text-muted-foreground">
            Couldn&apos;t load recent intake.
          </p>
        ) : !newest ? (
          <div className="py-4 text-center">
            <p className="text-sm font-medium text-foreground">
              No transcripts yet
            </p>
            <p className="mb-4 mt-1 text-sm text-muted-foreground">
              Add a transcript to start building your constitution.
            </p>
            <AddTranscriptDialog />
          </div>
        ) : (
          <div className="space-y-3">
            <div>
              <div className="flex items-center gap-2">
                <p className="truncate text-sm font-medium text-foreground">
                  {jobTitle(newest)}
                </p>
                <Badge
                  variant={
                    newest.status === "completed"
                      ? "success"
                      : newest.status === "failed"
                        ? "destructive"
                        : "outline"
                  }
                  className="flex-shrink-0 px-1.5 py-0 text-[10px]"
                >
                  {newest.status.toUpperCase()}
                </Badge>
              </div>
              {jobDate(newest) && (
                <p className="mt-0.5 text-xs text-muted-foreground">
                  {jobDate(newest)}
                </p>
              )}
            </div>

            {delta && (
              <div className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted-foreground">
                <span>{delta.new_decisions.length} new decisions</span>
                <span>
                  {delta.proposed_supersessions.length} supersessions
                </span>
                <span>{delta.potential_conflicts.length} conflicts</span>
              </div>
            )}

            {(delta || deltaLoading) && (
              <>
                <button
                  type="button"
                  onClick={() => setExpanded((v) => !v)}
                  className="inline-flex items-center gap-1 text-sm font-medium text-primary hover:underline"
                >
                  {expanded ? (
                    <ChevronDown className="h-3.5 w-3.5" />
                  ) : (
                    <ChevronRight className="h-3.5 w-3.5" />
                  )}
                  {expanded ? "Hide details" : "View change report"}
                </button>
                {expanded && (
                  <DeltaReportCard report={delta} loading={deltaLoading} />
                )}
              </>
            )}
          </div>
        )}
      </CardContent>
    </Card>
  );
}

// ---------------------------------------------------------------------------
// Card 4: What changed this week
// ---------------------------------------------------------------------------

function WeeklyDigestCard() {
  const { loading, error, data } = useAsync(() => fetchLatestWeeklyDigest(), []);

  return (
    <Card className="lg:col-span-3">
      <CardHeader className="pb-3">
        <CardTitle className="flex items-center gap-2 text-base">
          <FileText className="h-4 w-4 text-muted-foreground" />
          What changed this week
        </CardTitle>
      </CardHeader>
      <CardContent className="pt-0">
        {loading ? (
          <div className="space-y-2">
            <Skeleton className="h-4 w-3/4" />
            <Skeleton className="h-4 w-full" />
            <Skeleton className="h-4 w-2/3" />
          </div>
        ) : error || !data ? (
          <p className="py-2 text-sm text-muted-foreground">No digest yet.</p>
        ) : (
          <MarkdownViewer content={data} />
        )}
      </CardContent>
    </Card>
  );
}

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

export default function OverviewPage() {
  const [stats, setStats] = useState<DecisionStats | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [dialogOpen, setDialogOpen] = useState(false);

  useEffect(() => {
    let cancelled = false;
    fetchDecisionStats()
      .then((s) => {
        if (!cancelled) setStats(s);
      })
      .catch(() => {
        // Headline is non-critical — omit on failure.
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const handleSelect = useCallback((id: string) => {
    setSelectedId(id);
    setDialogOpen(true);
  }, []);

  return (
    <>
      <PageContainer className="space-y-6">
        <PageHeader
          title="Overview"
          description={stats?.headline}
          actions={
            <div className="flex items-center gap-2">
              <AddTranscriptDialog />
              <ExportConstitutionButton />
            </div>
          }
        />

        <div className="grid gap-6 lg:grid-cols-3">
          <ConstitutionCard onSelect={handleSelect} />
          <div className="space-y-6">
            <NeedsReviewCard />
          </div>
          <LatestIntakeCard />
          <WeeklyDigestCard />
        </div>
      </PageContainer>

      <DecisionDetailDialog
        decisionId={selectedId}
        open={dialogOpen}
        onOpenChange={setDialogOpen}
      />
    </>
  );
}
