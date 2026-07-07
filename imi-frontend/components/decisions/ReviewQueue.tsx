"use client";

import React, { useCallback, useEffect, useState } from "react";
import { CheckCircle2, XCircle, RefreshCw, Users } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { cn } from "@/lib/utils";
import {
  fetchSupersessionCandidates,
  fetchConflictCandidates,
  confirmConflictCandidate,
  dismissConflictCandidate,
  fetchDecisions,
  reviewDecision,
  type SupersessionCandidate,
  type ConflictQueueCandidate,
  type Decision,
} from "@/lib/api/decisions";
import { confirmCandidate, dismissCandidate } from "@/lib/api/ingest";

type RowStatus = "pending" | "confirmed" | "dismissed";
type ActionKind = "supersession" | "conflict" | "decision";
type Action = "confirm" | "dismiss" | "evidence_only" | "reject";

export interface ReviewQueueProps {
  onActioned?: (kind: ActionKind, action: Action) => void;
}

/**
 * Optimistic action state for a candidate row: flip status immediately,
 * roll back with an inline error if the API call fails. Same pattern as
 * DeltaReportCard's SupersessionRow.
 */
function useRowAction(
  perform: (action: Action) => Promise<unknown>,
  onDone?: (action: Action) => void,
) {
  const [status, setStatus] = useState<RowStatus>("pending");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handle = useCallback(
    async (action: Action) => {
      const prev = status;
      setStatus(action === "confirm" ? "confirmed" : "dismissed");
      setBusy(true);
      setError(null);
      try {
        await perform(action);
        onDone?.(action);
      } catch (err) {
        setStatus(prev);
        setError(err instanceof Error ? err.message : "Request failed");
      } finally {
        setBusy(false);
      }
    },
    [status, perform, onDone],
  );

  return { status, busy, error, handle };
}

function RowActions({
  status,
  busy,
  error,
  onAction,
}: {
  status: RowStatus;
  busy: boolean;
  error: string | null;
  onAction: (a: Action) => void;
}) {
  return (
    <>
      {status === "pending" && (
        <div className="flex gap-2">
          <Button
            size="sm"
            variant="default"
            disabled={busy}
            onClick={() => onAction("confirm")}
            className="h-7 text-xs bg-emerald-600 hover:bg-emerald-700"
          >
            <CheckCircle2 className="h-3.5 w-3.5 mr-1" />
            Confirm
          </Button>
          <Button
            size="sm"
            variant="ghost"
            disabled={busy}
            onClick={() => onAction("dismiss")}
            className="h-7 text-xs"
          >
            <XCircle className="h-3.5 w-3.5 mr-1" />
            Dismiss
          </Button>
        </div>
      )}
      {status === "confirmed" && (
        <Badge variant="default" className="text-xs bg-emerald-500">
          Confirmed
        </Badge>
      )}
      {status === "dismissed" && (
        <Badge variant="secondary" className="text-xs">
          Dismissed
        </Badge>
      )}
      {error && <p className="text-xs text-destructive">{error}</p>}
    </>
  );
}

function SupersessionRow({
  candidate,
  onActioned,
}: {
  candidate: SupersessionCandidate;
  onActioned?: ReviewQueueProps["onActioned"];
}) {
  const { status, busy, error, handle } = useRowAction(
    (action) =>
      action === "confirm"
        ? confirmCandidate({
            new_signal_id: candidate.new_signal_id,
            old_signal_id: candidate.old_signal_id,
          })
        : dismissCandidate({
            new_signal_id: candidate.new_signal_id,
            old_signal_id: candidate.old_signal_id,
          }),
    (action) => onActioned?.("supersession", action),
  );

  return (
    <div
      className={cn(
        "rounded-md border p-3 space-y-2 text-sm transition-opacity",
        status !== "pending" && "opacity-60",
      )}
    >
      <div className="space-y-1">
        <p className="text-xs text-muted-foreground uppercase tracking-wide font-medium">
          New
        </p>
        <p className="leading-snug line-clamp-3">{candidate.new_content}</p>
        <p className="text-xs text-muted-foreground uppercase tracking-wide font-medium mt-1">
          Supersedes
        </p>
        <p className="text-muted-foreground leading-snug line-clamp-3">
          {candidate.old_content}
        </p>
      </div>
      <div className="flex items-center gap-2 flex-wrap">
        {candidate.reason && (
          <span className="text-xs text-muted-foreground">{candidate.reason}</span>
        )}
        <Badge variant="outline" className="text-xs">
          {Math.round(candidate.confidence * 100)}% confidence
        </Badge>
        {candidate.matched_entities.map((entity) => (
          <Badge key={entity} variant="secondary" className="text-xs">
            {entity}
          </Badge>
        ))}
      </div>
      <RowActions status={status} busy={busy} error={error} onAction={handle} />
    </div>
  );
}

function ConflictRow({
  candidate,
  onActioned,
}: {
  candidate: ConflictQueueCandidate;
  onActioned?: ReviewQueueProps["onActioned"];
}) {
  const { status, busy, error, handle } = useRowAction(
    (action) =>
      action === "confirm"
        ? confirmConflictCandidate({
            signal_id: candidate.signal_id,
            other_signal_id: candidate.other_signal_id,
          })
        : dismissConflictCandidate({
            signal_id: candidate.signal_id,
            other_signal_id: candidate.other_signal_id,
          }),
    (action) => onActioned?.("conflict", action),
  );

  return (
    <div
      className={cn(
        "rounded-md border p-3 space-y-2 text-sm transition-opacity",
        status !== "pending" && "opacity-60",
      )}
    >
      <div className="space-y-1">
        <p className="text-xs text-muted-foreground uppercase tracking-wide font-medium">
          Signal A
        </p>
        <p className="leading-snug line-clamp-3">{candidate.signal_content}</p>
        <p className="text-xs text-muted-foreground uppercase tracking-wide font-medium mt-1">
          Signal B
        </p>
        <p className="leading-snug line-clamp-3">{candidate.other_content}</p>
      </div>
      <div className="flex items-center gap-2 flex-wrap">
        {candidate.rationale && (
          <span className="text-xs text-muted-foreground">{candidate.rationale}</span>
        )}
        <Badge variant="outline" className="text-xs">
          {Math.round(candidate.confidence * 100)}% confidence
        </Badge>
        {candidate.speakers.length > 0 && (
          <span className="flex items-center gap-1 text-xs text-muted-foreground">
            <Users className="h-3 w-3" />
            {candidate.speakers.join(", ")}
          </span>
        )}
      </div>
      <RowActions status={status} busy={busy} error={error} onAction={handle} />
    </div>
  );
}

type DecisionRowStatus = "pending" | "approved" | "evidence_only" | "rejected";

function formatDate(ts: string | null | undefined): string {
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

function DecisionReviewRow({
  decision,
  onActioned,
}: {
  decision: Decision;
  onActioned?: ReviewQueueProps["onActioned"];
}) {
  const [status, setStatus] = useState<DecisionRowStatus>("pending");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handle = useCallback(
    async (action: "confirm" | "evidence_only" | "reject") => {
      const prev = status;
      const optimistic: DecisionRowStatus =
        action === "confirm"
          ? "approved"
          : action === "evidence_only"
            ? "evidence_only"
            : "rejected";
      setStatus(optimistic);
      setBusy(true);
      setError(null);
      try {
        const resp = await reviewDecision(decision.id, { action });
        if (!resp.reviewed) {
          throw new Error("Review was not applied");
        }
        onActioned?.("decision", action);
      } catch (err) {
        setStatus(prev);
        setError(err instanceof Error ? err.message : "Request failed");
      } finally {
        setBusy(false);
      }
    },
    [status, decision.id, onActioned],
  );

  return (
    <div
      className={cn(
        "rounded-md border p-3 space-y-2 text-sm transition-opacity",
        status !== "pending" && "opacity-60",
      )}
    >
      <p className="leading-snug line-clamp-3">{decision.content}</p>
      <div className="flex items-center gap-3 text-xs text-muted-foreground flex-wrap">
        {decision.owner && <span>{decision.owner}</span>}
        {decision.source_meeting_title && (
          <span className="truncate max-w-[240px]">
            {decision.source_meeting_title}
          </span>
        )}
        {decision.source_timestamp && (
          <span>{formatDate(decision.source_timestamp)}</span>
        )}
      </div>
      {status === "pending" && (
        <div className="flex gap-2 flex-wrap">
          <Button
            size="sm"
            variant="default"
            disabled={busy}
            onClick={() => handle("confirm")}
            className="h-7 text-xs bg-emerald-600 hover:bg-emerald-700"
          >
            <CheckCircle2 className="h-3.5 w-3.5 mr-1" />
            Approve
          </Button>
          <Button
            size="sm"
            variant="outline"
            disabled={busy}
            onClick={() => handle("evidence_only")}
            className="h-7 text-xs"
          >
            Evidence only
          </Button>
          <Button
            size="sm"
            variant="ghost"
            disabled={busy}
            onClick={() => handle("reject")}
            className="h-7 text-xs"
          >
            <XCircle className="h-3.5 w-3.5 mr-1" />
            Reject
          </Button>
        </div>
      )}
      {status === "approved" && (
        <Badge variant="default" className="text-xs bg-emerald-500">
          Approved
        </Badge>
      )}
      {status === "evidence_only" && (
        <Badge variant="outline" className="text-xs">
          Evidence only
        </Badge>
      )}
      {status === "rejected" && (
        <Badge variant="secondary" className="text-xs">
          Rejected
        </Badge>
      )}
      {error && <p className="text-xs text-destructive">{error}</p>}
    </div>
  );
}

const UNREVIEWED_PAGE_SIZE = 50;

export function ReviewQueue({ onActioned }: ReviewQueueProps) {
  const [supersessions, setSupersessions] = useState<
    SupersessionCandidate[] | null
  >(null);
  const [conflicts, setConflicts] = useState<ConflictQueueCandidate[] | null>(
    null,
  );
  const [unreviewed, setUnreviewed] = useState<{
    decisions: Decision[];
    total: number;
  } | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setError(null);
    setSupersessions(null);
    setConflicts(null);
    setUnreviewed(null);
    try {
      const [supersessionList, conflictList, candidateDecisions] =
        await Promise.all([
          fetchSupersessionCandidates(),
          fetchConflictCandidates(),
          fetchDecisions({
            state: "candidate",
            limit: UNREVIEWED_PAGE_SIZE,
          }),
        ]);
      setSupersessions(supersessionList);
      setConflicts(conflictList);
      setUnreviewed({
        decisions: candidateDecisions.decisions,
        total: candidateDecisions.total,
      });
    } catch (err) {
      setError(
        err instanceof Error ? err.message : "Failed to load candidates",
      );
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  // Decision rows stay visible with their status chip after an action, but
  // the section count must reflect remaining *pending* reviews.
  const handleDecisionActioned = useCallback<
    NonNullable<ReviewQueueProps["onActioned"]>
  >(
    (kind, action) => {
      setUnreviewed((prev) =>
        prev ? { ...prev, total: Math.max(0, prev.total - 1) } : prev,
      );
      onActioned?.(kind, action);
    },
    [onActioned],
  );

  if (error) {
    return (
      <Card>
        <CardContent className="py-8 text-center space-y-3">
          <p className="text-sm text-destructive">{error}</p>
          <Button variant="outline" size="sm" onClick={load}>
            <RefreshCw className="h-3.5 w-3.5 mr-1" />
            Retry
          </Button>
        </CardContent>
      </Card>
    );
  }

  if (supersessions === null || conflicts === null || unreviewed === null) {
    return (
      <Card>
        <CardContent className="py-6 space-y-3">
          {[1, 2, 3].map((i) => (
            <Skeleton key={i} className="h-20 w-full" />
          ))}
        </CardContent>
      </Card>
    );
  }

  if (
    supersessions.length === 0 &&
    conflicts.length === 0 &&
    unreviewed.total === 0
  ) {
    return (
      <Card>
        <CardContent className="py-16 text-center">
          <p className="text-sm text-muted-foreground">
            No pending candidates — all caught up.
          </p>
        </CardContent>
      </Card>
    );
  }

  return (
    <div className="space-y-6">
      {supersessions.length > 0 && (
        <section className="space-y-2">
          <h2 className="text-sm font-semibold text-foreground">
            Proposed supersessions ({supersessions.length})
          </h2>
          {supersessions.map((candidate) => (
            <SupersessionRow
              key={`${candidate.new_signal_id}:${candidate.old_signal_id}`}
              candidate={candidate}
              onActioned={onActioned}
            />
          ))}
        </section>
      )}
      {conflicts.length > 0 && (
        <section className="space-y-2">
          <h2 className="text-sm font-semibold text-foreground">
            Potential conflicts ({conflicts.length})
          </h2>
          {conflicts.map((candidate) => (
            <ConflictRow
              key={`${candidate.signal_id}:${candidate.other_signal_id}`}
              candidate={candidate}
              onActioned={onActioned}
            />
          ))}
        </section>
      )}
      {unreviewed.decisions.length > 0 && (
        <section className="space-y-2">
          <h2 className="text-sm font-semibold text-foreground">
            Unreviewed decisions ({unreviewed.total})
            {unreviewed.total > UNREVIEWED_PAGE_SIZE && (
              <span className="ml-2 text-xs font-normal text-muted-foreground">
                showing first {UNREVIEWED_PAGE_SIZE}
              </span>
            )}
          </h2>
          {unreviewed.decisions.map((decision) => (
            <DecisionReviewRow
              key={decision.id}
              decision={decision}
              onActioned={handleDecisionActioned}
            />
          ))}
        </section>
      )}
    </div>
  );
}
