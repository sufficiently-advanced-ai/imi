"use client";

import React, { useState, useCallback } from "react";
import {
  CheckCircle2,
  XCircle,
  Users,
  GitBranch,
  ListTodo,
  Scale,
  Lightbulb,
} from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { cn } from "@/lib/utils";
import {
  confirmCandidate,
  dismissCandidate,
  type ConflictCandidate,
  type DeltaReport,
  type SupersessionProposal,
} from "@/lib/api/ingest";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export interface DeltaReportCardProps {
  report: DeltaReport | null;
  loading?: boolean;
}

// ---------------------------------------------------------------------------
// SupersessionRow — one candidate with confirm / dismiss
// ---------------------------------------------------------------------------

function SupersessionRow({ proposal }: { proposal: SupersessionProposal }) {
  const [status, setStatus] = useState<"pending" | "confirmed" | "dismissed">(
    proposal.status as "pending" | "confirmed" | "dismissed",
  );
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handle = useCallback(
    async (action: "confirm" | "dismiss") => {
      const prev = status;
      const optimistic = action === "confirm" ? "confirmed" : "dismissed";
      setStatus(optimistic);
      setBusy(true);
      setError(null);

      try {
        if (action === "confirm") {
          await confirmCandidate({
            new_signal_id: proposal.new_signal_id,
            old_signal_id: proposal.old_signal_id,
          });
        } else {
          await dismissCandidate({
            new_signal_id: proposal.new_signal_id,
            old_signal_id: proposal.old_signal_id,
          });
        }
      } catch (err) {
        // Rollback on error
        setStatus(prev);
        setError(err instanceof Error ? err.message : "Request failed");
      } finally {
        setBusy(false);
      }
    },
    [status, proposal.new_signal_id, proposal.old_signal_id],
  );

  const confidencePct = Math.round(proposal.confidence * 100);

  return (
    <div
      className={cn(
        "rounded-md border p-3 space-y-2 text-sm transition-opacity",
        status !== "pending" && "opacity-60",
      )}
    >
      {/* New → Old */}
      <div className="space-y-1">
        <p className="text-xs text-muted-foreground uppercase tracking-wide font-medium">
          New
        </p>
        <p className="leading-snug">{proposal.new_signal_id}</p>
        <p className="text-xs text-muted-foreground uppercase tracking-wide font-medium mt-1">
          Supersedes
        </p>
        <p className="text-muted-foreground leading-snug">{proposal.old_content}</p>
      </div>

      {/* Reason + confidence */}
      <div className="flex items-center gap-2 flex-wrap">
        {proposal.reason && (
          <span className="text-xs text-muted-foreground">{proposal.reason}</span>
        )}
        <Badge variant="outline" className="text-xs">
          {confidencePct}% confidence
        </Badge>
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
      </div>

      {/* Actions — only show when still pending */}
      {status === "pending" && (
        <div className="flex gap-2">
          <Button
            size="sm"
            variant="default"
            disabled={busy}
            onClick={() => handle("confirm")}
            className="h-7 text-xs bg-emerald-600 hover:bg-emerald-700"
          >
            <CheckCircle2 className="h-3.5 w-3.5 mr-1" />
            Confirm
          </Button>
          <Button
            size="sm"
            variant="ghost"
            disabled={busy}
            onClick={() => handle("dismiss")}
            className="h-7 text-xs"
          >
            <XCircle className="h-3.5 w-3.5 mr-1" />
            Dismiss
          </Button>
        </div>
      )}

      {error && <p className="text-xs text-destructive">{error}</p>}
    </div>
  );
}

// ---------------------------------------------------------------------------
// ConflictRow — one potential conflict (read-only, no actions)
// ---------------------------------------------------------------------------

function ConflictRow({
  conflict,
  report,
}: {
  conflict: ConflictCandidate;
  report: DeltaReport;
}) {
  const confidencePct = Math.round(conflict.confidence * 100);

  // Resolve new_signal_id to its decision content, matching what the backend
  // markdown renderer does via _find_decision_content(). Fall back to the raw
  // ID when the decision is not found in the report (e.g. cross-ingest refs).
  const newContent =
    report.new_decisions.find((d) => d.signal_id === conflict.new_signal_id)
      ?.content ?? conflict.new_signal_id;

  return (
    <div className="rounded-md border p-3 space-y-2 text-sm">
      {/* New → Other */}
      <div className="space-y-1">
        <p className="text-xs text-muted-foreground uppercase tracking-wide font-medium">
          New
        </p>
        <p className="leading-snug">{newContent}</p>
        <p className="text-xs text-muted-foreground uppercase tracking-wide font-medium mt-1">
          May conflict with
        </p>
        <p className="text-muted-foreground leading-snug">{conflict.other_content}</p>
      </div>

      {/* Rationale + confidence badge */}
      <div className="flex items-center gap-2 flex-wrap">
        {conflict.rationale && (
          <span className="text-xs text-muted-foreground">{conflict.rationale}</span>
        )}
        <Badge variant="outline" className="text-xs">
          {confidencePct}% confidence
        </Badge>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Section heading helper
// ---------------------------------------------------------------------------

function SectionHeading({
  icon: Icon,
  label,
  count,
}: {
  icon: React.ComponentType<{ className?: string }>;
  label: string;
  count: number;
}) {
  return (
    <div className="flex items-center gap-2 mb-2">
      <Icon className="h-4 w-4 text-muted-foreground" />
      <span className="font-medium text-sm">{label}</span>
      <Badge variant="secondary" className="text-xs">
        {count}
      </Badge>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main DeltaReportCard
// ---------------------------------------------------------------------------

export function DeltaReportCard({ report, loading }: DeltaReportCardProps) {
  if (loading) {
    return (
      <div className="space-y-3">
        <Skeleton className="h-5 w-48" />
        <Skeleton className="h-16 w-full" />
        <Skeleton className="h-16 w-full" />
      </div>
    );
  }

  if (!report) return null;

  const hasAnyContent =
    report.new_decisions.length > 0 ||
    report.proposed_supersessions.length > 0 ||
    (report.potential_conflicts?.length ?? 0) > 0 ||
    report.commitments_opened.length > 0 ||
    report.commitments_closed.length > 0 ||
    report.entities_touched.length > 0;

  return (
    <div className="space-y-5" data-testid="delta-report-card">
      {/* Header */}
      <div>
        <h3 className="font-semibold text-base">
          What your brain learned
          {report.meeting_title && (
            <span className="text-muted-foreground font-normal">
              {" "}
              — {report.meeting_title}
            </span>
          )}
        </h3>
        <p className="text-xs text-muted-foreground mt-0.5">
          {new Date(report.generated_at).toLocaleString()}
        </p>
      </div>

      {!hasAnyContent && (
        <p className="text-sm text-muted-foreground italic">
          No signals extracted from this content.
        </p>
      )}

      {/* New decisions */}
      {report.new_decisions.length > 0 && (
        <section>
          <SectionHeading
            icon={Scale}
            label="New decisions"
            count={report.new_decisions.length}
          />
          <ul className="space-y-1.5">
            {report.new_decisions.map((item) => (
              <li
                key={item.signal_id}
                className="text-sm leading-snug pl-2 border-l-2 border-violet-200 dark:border-violet-800"
              >
                {item.content}
                {item.entities.length > 0 && (
                  <span className="text-xs text-muted-foreground ml-1">
                    ({item.entities.join(", ")})
                  </span>
                )}
              </li>
            ))}
          </ul>
        </section>
      )}

      {/* Proposed supersessions */}
      {report.proposed_supersessions.length > 0 && (
        <section>
          <SectionHeading
            icon={GitBranch}
            label="Proposed supersessions"
            count={report.proposed_supersessions.length}
          />
          <div className="space-y-2">
            {report.proposed_supersessions.map((p) => (
              <SupersessionRow key={`${p.new_signal_id}-${p.old_signal_id}`} proposal={p} />
            ))}
          </div>
        </section>
      )}

      {/* Potential conflicts */}
      {(report.potential_conflicts?.length ?? 0) > 0 && (
        <section>
          <SectionHeading
            icon={Lightbulb}
            label="Potential conflicts"
            count={report.potential_conflicts.length}
          />
          <div className="space-y-2">
            {report.potential_conflicts.map((c) => (
              <ConflictRow
                key={`${c.new_signal_id}-${c.other_signal_id}`}
                conflict={c}
                report={report}
              />
            ))}
          </div>
        </section>
      )}

      {/* Commitments opened */}
      {report.commitments_opened.length > 0 && (
        <section>
          <SectionHeading
            icon={ListTodo}
            label="Commitments opened"
            count={report.commitments_opened.length}
          />
          <ul className="space-y-1.5">
            {report.commitments_opened.map((item) => (
              <li
                key={item.signal_id}
                className="text-sm leading-snug pl-2 border-l-2 border-amber-200 dark:border-amber-800"
              >
                {item.content}
                {item.owner && (
                  <span className="text-xs text-muted-foreground ml-1">
                    → {item.owner}
                  </span>
                )}
                {item.due_date && (
                  <span className="text-xs text-muted-foreground ml-1">
                    by {item.due_date}
                  </span>
                )}
              </li>
            ))}
          </ul>
        </section>
      )}

      {/* Commitments closed */}
      {report.commitments_closed.length > 0 && (
        <section>
          <SectionHeading
            icon={CheckCircle2}
            label="Commitments closed"
            count={report.commitments_closed.length}
          />
          <ul className="space-y-1.5">
            {report.commitments_closed.map((item) => (
              <li
                key={item.signal_id}
                className="text-sm leading-snug text-muted-foreground line-through decoration-muted-foreground/40 pl-2 border-l-2 border-emerald-200 dark:border-emerald-800"
              >
                {item.content}
              </li>
            ))}
          </ul>
        </section>
      )}

      {/* Entities touched */}
      {report.entities_touched.length > 0 && (
        <section>
          <SectionHeading
            icon={Users}
            label="Entities touched"
            count={report.entities_touched.length}
          />
          <div className="flex flex-wrap gap-1.5">
            {report.entities_touched.map((e) => (
              <Badge key={e.id} variant="secondary" className="text-xs">
                {e.name}
                <span className="ml-1 text-muted-foreground">· {e.type}</span>
              </Badge>
            ))}
          </div>
        </section>
      )}
    </div>
  );
}
