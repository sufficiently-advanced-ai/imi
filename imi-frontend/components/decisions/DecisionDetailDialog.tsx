"use client";

import React, { useEffect, useState } from "react";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
} from "@/components/ui/dialog";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { GovernanceLadder } from "./GovernanceLadder";
import { fetchDecisionById, decisionStateBadgeVariant } from "@/lib/api/decisions";
import type { DecisionDetail } from "@/lib/api/decisions";
import {
  AlertCircle,
  ArrowRight,
  Clock,
  User,
  Building2,
  CalendarDays,
} from "lucide-react";
import { cn } from "@/lib/utils";

// ---- helpers ----

function formatDate(ts: string | null): string {
  if (!ts) return "—";
  try {
    return new Date(ts).toLocaleDateString("en-US", {
      year: "numeric",
      month: "short",
      day: "numeric",
      hour: "numeric",
      minute: "2-digit",
    });
  } catch {
    return ts;
  }
}

function formatShortDate(ts: string): string {
  try {
    return new Date(ts).toLocaleDateString("en-US", {
      month: "short",
      day: "numeric",
      year: "numeric",
    });
  } catch {
    return ts;
  }
}

// ---- sub-components ----

function MetaRow({
  icon: Icon,
  label,
  value,
}: {
  icon: React.ComponentType<{ className?: string }>;
  label: string;
  value: string | null | undefined;
}) {
  if (!value) return null;
  return (
    <div className="flex items-center gap-2 text-sm">
      <Icon className="h-3.5 w-3.5 text-muted-foreground flex-shrink-0" />
      <span className="text-muted-foreground">{label}:</span>
      <span className="text-foreground">{value}</span>
    </div>
  );
}

function LineageChain({ lineage }: { lineage: DecisionDetail["lineage"] }) {
  if (!lineage || lineage.length === 0) return null;

  return (
    <div className="space-y-2">
      <h3 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
        Lineage
      </h3>
      <div className="space-y-1.5">
        {lineage.map((entry, i) => (
          <div key={entry.id} className="relative flex items-start gap-2">
            {/* Connector */}
            {i > 0 && (
              <div className="absolute -mt-1 ml-3">
                <ArrowRight className="h-3 w-3 text-muted-foreground/40" />
              </div>
            )}
            <div
              className={cn(
                "flex-1 rounded-md border px-3 py-2 text-sm transition-colors",
                entry.relation === "self"
                  ? "border-primary/40 bg-primary/5"
                  : "border-border bg-muted/30",
              )}
            >
              <div className="flex items-center gap-2 mb-1">
                <span
                  className={cn(
                    "text-[10px] font-semibold uppercase tracking-wide",
                    entry.relation === "self"
                      ? "text-primary"
                      : "text-muted-foreground",
                  )}
                >
                  {entry.relation}
                </span>
                <Badge
                  variant={decisionStateBadgeVariant(entry.state)}
                  className="text-[10px] px-1.5 py-0"
                >
                  {entry.state}
                </Badge>
                {entry.source_timestamp && (
                  <span className="text-[10px] text-muted-foreground ml-auto">
                    {formatShortDate(entry.source_timestamp)}
                  </span>
                )}
              </div>
              <p
                className={cn(
                  "text-sm leading-snug",
                  entry.relation === "predecessor" && "line-through opacity-60",
                )}
              >
                {entry.content}
              </p>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

function AuditTimeline({ history }: { history: DecisionDetail["audit_history"] }) {
  if (!history || history.length === 0) return null;

  return (
    <div className="space-y-2">
      <h3 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
        Audit History
      </h3>
      <div className="space-y-3">
        {history.map((entry, i) => (
          <div key={i} className="flex gap-3 text-sm">
            {/* Timeline dot + line */}
            <div className="flex flex-col items-center">
              <div className="h-2 w-2 rounded-full bg-muted-foreground/40 mt-1.5 flex-shrink-0" />
              {i < history.length - 1 && (
                <div className="w-px flex-1 bg-border mt-1" />
              )}
            </div>

            <div className="flex-1 pb-3">
              <div className="flex items-center gap-2 flex-wrap">
                <span className="font-medium text-foreground capitalize">
                  {entry.action.replace(/_/g, " ")}
                </span>
                {entry.gate_response && (
                  <Badge variant="outline" className="text-[10px] px-1.5 py-0">
                    {entry.gate_response}
                  </Badge>
                )}
                {entry.actor && (
                  <span className="text-muted-foreground text-xs">
                    by {entry.actor}
                  </span>
                )}
                <span className="text-muted-foreground text-xs ml-auto">
                  {formatDate(entry.created_at)}
                </span>
              </div>
              {entry.reasoning && (
                <p className="mt-1 text-muted-foreground text-xs leading-relaxed">
                  {entry.reasoning}
                </p>
              )}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

// ---- main component ----

interface DecisionDetailDialogProps {
  decisionId: string | null;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

export function DecisionDetailDialog({
  decisionId,
  open,
  onOpenChange,
}: DecisionDetailDialogProps) {
  const [detail, setDetail] = useState<DecisionDetail | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!open || !decisionId) {
      setDetail(null);
      setError(null);
      return;
    }

    let cancelled = false;

    async function load() {
      setLoading(true);
      setError(null);
      try {
        const data = await fetchDecisionById(decisionId!);
        if (!cancelled) setDetail(data);
      } catch (err) {
        if (!cancelled) {
          setError(
            err instanceof Error ? err.message : "Failed to load decision",
          );
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    }

    load();
    return () => {
      cancelled = true;
    };
  }, [open, decisionId]);

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-2xl max-h-[85vh] overflow-y-auto">
        {loading && (
          <>
            <DialogHeader>
              <DialogTitle>
                <Skeleton className="h-5 w-48" />
              </DialogTitle>
              <DialogDescription>
                <Skeleton className="h-4 w-32" />
              </DialogDescription>
            </DialogHeader>
            <div className="space-y-3 mt-2">
              <Skeleton className="h-16 w-full" />
              <Skeleton className="h-4 w-3/4" />
              <Skeleton className="h-4 w-1/2" />
            </div>
          </>
        )}

        {error && (
          <>
            <DialogHeader>
              <DialogTitle>Decision Detail</DialogTitle>
              <DialogDescription>Could not load this decision.</DialogDescription>
            </DialogHeader>
            <div className="flex items-center gap-3 py-6 text-destructive">
              <AlertCircle className="h-5 w-5 flex-shrink-0" />
              <p className="text-sm">{error}</p>
            </div>
          </>
        )}

        {!loading && !error && detail && (
          <>
            <DialogHeader>
              <div className="flex items-center gap-2 flex-wrap mb-1">
                <Badge
                  variant={decisionStateBadgeVariant(detail.state)}
                  className="text-xs"
                >
                  {detail.state}
                </Badge>
                {detail.state_reason && (
                  <span className="text-xs text-muted-foreground">
                    {detail.state_reason}
                  </span>
                )}
              </div>
              <DialogTitle className="text-base leading-snug mt-1">
                {detail.content}
              </DialogTitle>
            </DialogHeader>

            <div className="space-y-5 mt-1">
              {/* Governance position */}
              <div className="flex items-center gap-3">
                <span className="text-xs text-muted-foreground">
                  Governance:
                </span>
                <GovernanceLadder ladder={detail.governance_ladder} />
              </div>

              {/* Metadata row */}
              <div className="flex flex-col gap-1.5 text-sm border rounded-md px-3 py-2.5 bg-muted/20">
                <MetaRow
                  icon={User}
                  label="Owner"
                  value={detail.owner}
                />
                <MetaRow
                  icon={Building2}
                  label="Client"
                  value={detail.client_id}
                />
                <MetaRow
                  icon={CalendarDays}
                  label="Source meeting"
                  value={detail.source_meeting_title}
                />
                <MetaRow
                  icon={Clock}
                  label="Captured"
                  value={formatDate(detail.source_timestamp)}
                />
                {detail.age_days !== null && detail.age_days !== undefined && (
                  <div className="flex items-center gap-2 text-sm">
                    <Clock className="h-3.5 w-3.5 text-muted-foreground flex-shrink-0" />
                    <span className="text-muted-foreground">Age:</span>
                    <span className="text-foreground">
                      {detail.age_days} day{detail.age_days !== 1 ? "s" : ""}
                    </span>
                  </div>
                )}
              </div>

              {/* Lineage */}
              <LineageChain lineage={detail.lineage} />

              {/* Audit history */}
              <AuditTimeline history={detail.audit_history} />
            </div>
          </>
        )}
      </DialogContent>
    </Dialog>
  );
}
