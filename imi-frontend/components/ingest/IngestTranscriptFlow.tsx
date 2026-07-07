"use client";

import { useState } from "react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { useToast } from "@/components/ui/use-toast";
import { Loader2, Upload } from "lucide-react";
import { IngestProgress } from "@/components/IngestProgress";
import { DeltaReportCard } from "@/components/DeltaReportCard";
import { submitIngest, fetchDelta, type DeltaReport } from "@/lib/api/ingest";
import { cn } from "@/lib/utils";

// ---------------------------------------------------------------------------
// IngestTranscriptFlow — self-contained transcript ingest flow
// Extracted from app/(protected)/meetings/page.tsx.
// Callers control width via className (e.g. className="max-w-2xl").
// ---------------------------------------------------------------------------

export interface IngestTranscriptFlowProps {
  /** Called with the delta report once it has loaded. */
  onComplete?: (report: DeltaReport) => void;
  /**
   * Called with `true` when a job is actively running (submitted but delta not
   * yet received), and `false` when the flow reaches a terminal state (delta
   * loaded, error, or reset).  Used by parent dialogs to guard accidental
   * dismissal mid-ingest.
   */
  onBusyChange?: (busy: boolean) => void;
  /** Merged onto the root element so callers can constrain width. */
  className?: string;
}

export function IngestTranscriptFlow({ onComplete, onBusyChange, className }: IngestTranscriptFlowProps) {
  const [content, setContent] = useState("");
  const [title, setTitle] = useState("");
  const [jobId, setJobId] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [deltaReport, setDeltaReport] = useState<DeltaReport | null>(null);
  const [loadingDelta, setLoadingDelta] = useState(false);
  const { toast } = useToast();

  const handleSubmit = async () => {
    if (!content.trim()) return;
    setSubmitting(true);
    setSubmitError(null);
    setJobId(null);
    setDeltaReport(null);
    try {
      const resp = await submitIngest({
        content: content.trim(),
        title: title.trim() || undefined,
        source: "transcript",
      });
      if (resp.status === "duplicate") {
        toast({
          title: "Duplicate content",
          description: "This transcript was already ingested. Showing existing job.",
        });
      }
      setJobId(resp.job_id);
      onBusyChange?.(true);
    } catch (err) {
      setSubmitError(err instanceof Error ? err.message : "Submission failed");
    } finally {
      setSubmitting(false);
    }
  };

  const handleDeltaReady = async () => {
    if (!jobId || deltaReport) return;
    setLoadingDelta(true);
    try {
      const report = await fetchDelta(jobId);
      setDeltaReport(report);
      onBusyChange?.(false);
      onComplete?.(report);
    } catch {
      // Delta may not be ready yet on the first callback; retry is fine
    } finally {
      setLoadingDelta(false);
    }
  };

  const handleFailed = (error: string) => {
    onBusyChange?.(false);
    toast({
      title: "Ingestion failed",
      description: error,
      variant: "destructive",
    });
  };

  const reset = () => {
    setJobId(null);
    setDeltaReport(null);
    setContent("");
    setTitle("");
    setSubmitError(null);
    onBusyChange?.(false);
  };

  return (
    <div className={cn("space-y-6", className)}>
      {/* Input form — hide once a job is running */}
      {!jobId && (
        <Card>
          <CardHeader>
            <CardTitle className="text-base">Ingest transcript</CardTitle>
            <CardDescription>
              Paste a meeting transcript to extract signals, decisions, and
              commitments into your knowledge base.
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="space-y-1.5">
              <Label htmlFor="ingest-title">Title (optional)</Label>
              <Input
                id="ingest-title"
                placeholder="e.g. Q3 planning call"
                value={title}
                onChange={(e) => setTitle(e.target.value)}
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="ingest-content">Transcript</Label>
              <Textarea
                id="ingest-content"
                placeholder="Paste transcript text here…"
                value={content}
                onChange={(e) => setContent(e.target.value)}
                rows={10}
                className="font-mono text-xs resize-y"
              />
              <p className="text-xs text-muted-foreground">
                Max 500 KB
              </p>
            </div>
            {submitError && (
              <p className="text-sm text-destructive">{submitError}</p>
            )}
            <Button
              onClick={handleSubmit}
              disabled={submitting || !content.trim()}
            >
              {submitting ? (
                <Loader2 className="h-4 w-4 mr-2 animate-spin" />
              ) : (
                <Upload className="h-4 w-4 mr-2" />
              )}
              {submitting ? "Submitting…" : "Ingest transcript"}
            </Button>
          </CardContent>
        </Card>
      )}

      {/* Live pipeline progress */}
      {jobId && !deltaReport && (
        <Card>
          <CardHeader>
            <CardTitle className="text-sm font-medium text-muted-foreground">
              Pipeline running
            </CardTitle>
            <p className="text-xs text-muted-foreground font-mono">{jobId}</p>
          </CardHeader>
          <CardContent>
            <IngestProgress
              jobId={jobId}
              onDeltaReady={handleDeltaReady}
              onFailed={handleFailed}
            />
          </CardContent>
        </Card>
      )}

      {/* Delta report */}
      {(deltaReport || loadingDelta) && (
        <Card>
          <CardHeader className="pb-3">
            <CardTitle className="text-sm font-medium text-muted-foreground">
              Delta report
            </CardTitle>
          </CardHeader>
          <CardContent>
            <DeltaReportCard report={deltaReport} loading={loadingDelta} />
          </CardContent>
        </Card>
      )}

      {/* Reset button after completion */}
      {(deltaReport) && (
        <Button variant="outline" size="sm" onClick={reset}>
          Ingest another transcript
        </Button>
      )}
    </div>
  );
}
