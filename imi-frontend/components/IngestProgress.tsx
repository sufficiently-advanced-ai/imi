"use client";

import React, { useEffect, useRef, useState, useCallback } from "react";
import { CheckCircle2, Circle, Loader2, XCircle } from "lucide-react";
import { cn } from "@/lib/utils";
import {
  createIngestEventSource,
  fetchIngestStatus,
  type IngestSSEEvent,
  type IngestPhaseEvent,
} from "@/lib/api/ingest";

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

/** Ordered phase labels matching the backend PHASES list. */
const PHASES = [
  "CLASSIFY",
  "BUILD_MEETING",
  "PROMOTE_SIGNALS",
  "DETECT_SUPERSESSION",
  "DETECT_CONFLICTS",
  "ENRICH_GRAPH",
  "PERSIST",
  "DELTA_REPORT",
  "COMPLETE",
] as const;

const PHASE_LABELS: Record<string, string> = {
  CLASSIFY: "Classify",
  BUILD_MEETING: "Build observation",
  PROMOTE_SIGNALS: "Promote signals",
  DETECT_SUPERSESSION: "Detect supersessions",
  DETECT_CONFLICTS: "Detect conflicts",
  ENRICH_GRAPH: "Enrich graph",
  PERSIST: "Persist",
  DELTA_REPORT: "Build delta report",
  COMPLETE: "Complete",
};

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

type PhaseState = "pending" | "active" | "done" | "failed";

export interface IngestProgressProps {
  jobId: string;
  /** Called when the delta report is ready (event or status poll). */
  onDeltaReady?: () => void;
  /** Called when the pipeline fails. */
  onFailed?: (error: string) => void;
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function phaseStateIcon(state: PhaseState) {
  switch (state) {
    case "done":
      return <CheckCircle2 className="h-4 w-4 text-emerald-500 shrink-0" />;
    case "active":
      return (
        <Loader2 className="h-4 w-4 text-violet-500 shrink-0 animate-spin" />
      );
    case "failed":
      return <XCircle className="h-4 w-4 text-destructive shrink-0" />;
    default:
      return (
        <Circle className="h-4 w-4 text-muted-foreground/30 shrink-0" />
      );
  }
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export function IngestProgress({
  jobId,
  onDeltaReady,
  onFailed,
}: IngestProgressProps) {
  const [phasesCompleted, setPhasesCompleted] = useState<Set<string>>(
    new Set(),
  );
  const [currentPhase, setCurrentPhase] = useState<string | null>(null);
  const [failed, setFailed] = useState(false);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);
  const [, setDone] = useState(false);

  // Keep stable refs for callbacks
  const onDeltaReadyRef = useRef(onDeltaReady);
  const onFailedRef = useRef(onFailed);
  onDeltaReadyRef.current = onDeltaReady;
  onFailedRef.current = onFailed;

  const esRef = useRef<EventSource | null>(null);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const deltaNotifiedRef = useRef(false);

  const stopPolling = useCallback(() => {
    if (pollRef.current) {
      clearInterval(pollRef.current);
      pollRef.current = null;
    }
  }, []);

  const startPolling = useCallback(() => {
    if (pollRef.current) return; // already polling
    pollRef.current = setInterval(async () => {
      try {
        const status = await fetchIngestStatus(jobId);
        setPhasesCompleted(new Set(status.phases_completed));
        setCurrentPhase(status.current_phase);

        if (status.status === "failed") {
          setFailed(true);
          setErrorMsg(status.error ?? "Pipeline failed");
          onFailedRef.current?.(status.error ?? "Pipeline failed");
          stopPolling();
        } else if (status.status === "completed") {
          setDone(true);
          onDeltaReadyRef.current?.();
          stopPolling();
        }
        // Check for delta_report phase — fire onDeltaReady once
        if (status.phases_completed.includes("DELTA_REPORT") && !deltaNotifiedRef.current) {
          deltaNotifiedRef.current = true;
          onDeltaReadyRef.current?.();
        }
      } catch {
        // poll errors are non-fatal; SSE likely reconnecting
      }
    }, 3000);
  }, [jobId, stopPolling]);

  useEffect(() => {
    // Reset deltaNotified flag when jobId changes
    deltaNotifiedRef.current = false;

    // Open SSE stream; fall back to polling on error
    const es = createIngestEventSource(
      jobId,
      (event: IngestSSEEvent) => {
        if (event.type === "ingest_phase") {
          const e = event as IngestPhaseEvent;
          if (e.status === "completed") {
            setPhasesCompleted(new Set(e.phases_completed));
            setCurrentPhase(null);
          } else if (e.status === "started") {
            setCurrentPhase(e.phase);
          }
        } else if (event.type === "delta_report_ready") {
          if (!deltaNotifiedRef.current) {
            deltaNotifiedRef.current = true;
            onDeltaReadyRef.current?.();
          }
        } else if (event.type === "ingest_complete") {
          setDone(true);
          setPhasesCompleted(new Set(PHASES));
          setCurrentPhase(null);
          stopPolling();
          // Fallback: if delta_report_ready was missed before ingest_complete,
          // fire onDeltaReady now so the parent can fetch the delta report.
          if (!deltaNotifiedRef.current) {
            deltaNotifiedRef.current = true;
            onDeltaReadyRef.current?.();
          }
        } else if (event.type === "ingest_failed") {
          setFailed(true);
          const err =
            (event as { type: string; error: string }).error ?? "Pipeline failed";
          setErrorMsg(err);
          onFailedRef.current?.(err);
          stopPolling();
          // NOTE: Keep currentPhase set so the failed phase renders with red styling
        }
      },
      (_err) => {
        // SSE error — start polling fallback
        startPolling();
      },
    );
    esRef.current = es;

    return () => {
      es.close();
      stopPolling();
    };
  }, [jobId, startPolling, stopPolling]);

  return (
    <div className="space-y-1" role="status" aria-label="Ingestion pipeline progress">
      {PHASES.map((phase) => {
        let state: PhaseState = "pending";
        if (phasesCompleted.has(phase)) {
          state = "done";
        } else if (currentPhase === phase) {
          state = failed ? "failed" : "active";
        }

        return (
          <div
            key={phase}
            className={cn(
              "flex items-center gap-2.5 px-1 py-1 rounded text-sm transition-colors",
              state === "active" && "text-violet-700 dark:text-violet-300 font-medium",
              state === "done" && "text-muted-foreground",
              state === "pending" && "text-muted-foreground/50",
              state === "failed" && "text-destructive",
            )}
          >
            {phaseStateIcon(state)}
            <span>{PHASE_LABELS[phase] ?? phase}</span>
          </div>
        );
      })}

      {errorMsg && (
        <p className="mt-2 text-xs text-destructive px-1">{errorMsg}</p>
      )}
    </div>
  );
}
