"use client";

import React, { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { Card, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { Textarea } from "@/components/ui/textarea";
import { PageHeader } from "@/components/ui/page-header";
import { PageContainer } from "@/components/ui/page-container";
import {
  AlertCircle,
  Brain,
  Check,
  CopyCheck,
  RefreshCw,
  Scale,
  X,
} from "lucide-react";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import {
  captureReviewBadgeVariant,
  createCapture,
  fetchCaptures,
  reviewCapture,
  type Capture,
  type CaptureReviewAction,
} from "@/lib/api/captures";
import {
  fetchReviewQueue,
  reviewRecord,
  type MemoryReviewAction,
  type ReviewQueueItem,
} from "@/lib/api/agent-memory";
import {
  fetchJudgeDecisions,
  judgeDecisionBadgeVariant,
  type JudgeDecisionEvent,
} from "@/lib/api/judge";

/**
 * Memory — the general capture surface (OB1 absorption Phase 1).
 *
 * Quick-capture any thought; captures enter the governance ladder as
 * imported, evidence-grade memory. Pending records carry inline review
 * actions (the human gate that mints instruction-grade memory, ADR-002).
 */
export default function MemoryPage() {
  const [captures, setCaptures] = useState<Capture[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [draft, setDraft] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);

  const loadCaptures = useCallback(async () => {
    try {
      setError(null);
      const response = await fetchCaptures({ limit: 100 });
      setCaptures(response.captures);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load captures");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    loadCaptures();
  }, [loadCaptures]);

  const handleCapture = async () => {
    const content = draft.trim();
    if (!content || submitting) {
      return;
    }
    setSubmitting(true);
    setNotice(null);
    try {
      const result = await createCapture({ content });
      if (result.deduped) {
        setNotice("Already captured — returned the existing record.");
      } else {
        setDraft("");
      }
      await loadCaptures();
    } catch (e) {
      setNotice(e instanceof Error ? e.message : "Capture failed");
    } finally {
      setSubmitting(false);
    }
  };

  const handleReview = async (id: string, action: CaptureReviewAction) => {
    try {
      await reviewCapture(id, action);
      await loadCaptures();
    } catch (e) {
      setNotice(e instanceof Error ? e.message : "Review failed");
    }
  };

  return (
    <PageContainer>
      <PageHeader
        title="Memory"
        description="Captured thoughts in the governance ladder — evidence until a human confirms."
        actions={
          <div className="flex gap-2">
            <Button variant="outline" size="sm" asChild>
              <Link href="/memory/traces">Recall traces</Link>
            </Button>
            <Button variant="outline" size="sm" onClick={loadCaptures}>
              <RefreshCw className="h-4 w-4 mr-2" />
              Refresh
            </Button>
          </div>
        }
      />

      {/* Quick capture */}
      <Card className="mb-6">
        <CardContent className="pt-6">
          <div className="flex flex-col gap-3">
            <Textarea
              placeholder="Capture a thought — it's persisted immediately, then enriched and embedded…"
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) {
                  handleCapture();
                }
              }}
              rows={3}
            />
            <div className="flex items-center justify-between">
              <span className="text-sm text-muted-foreground">
                {notice ?? "⌘↵ to capture"}
              </span>
              <Button
                onClick={handleCapture}
                disabled={submitting || !draft.trim()}
              >
                <Brain className="h-4 w-4 mr-2" />
                Capture
              </Button>
            </div>
          </div>
        </CardContent>
      </Card>

      <Tabs defaultValue="captures">
        <TabsList className="mb-4">
          <TabsTrigger value="captures">Captures</TabsTrigger>
          <TabsTrigger value="queue">Review queue</TabsTrigger>
          <TabsTrigger value="judge">Judge activity</TabsTrigger>
        </TabsList>

        <TabsContent value="captures">
          {loading ? (
            <div className="space-y-3">
              {[0, 1, 2].map((i) => (
                <Skeleton key={i} className="h-24 w-full" />
              ))}
            </div>
          ) : error ? (
            <Card>
              <CardContent className="pt-6 flex items-center gap-2 text-destructive">
                <AlertCircle className="h-4 w-4" />
                {error}
              </CardContent>
            </Card>
          ) : captures.length === 0 ? (
            <Card>
              <CardContent className="pt-6 text-muted-foreground text-sm">
                Nothing captured yet — type a thought above, or call the{" "}
                <code>capture_thought</code> MCP tool from any connected AI.
              </CardContent>
            </Card>
          ) : (
            <div className="space-y-3">
              {captures.map((capture) => (
                <CaptureRow
                  key={capture.id}
                  capture={capture}
                  onReview={handleReview}
                />
              ))}
            </div>
          )}
        </TabsContent>

        <TabsContent value="queue">
          <ReviewQueueTab />
        </TabsContent>

        <TabsContent value="judge">
          <JudgeActivityTab />
        </TabsContent>
      </Tabs>
    </PageContainer>
  );
}

/**
 * Judge activity — recent judgment events (allow/block/revise/escalate) with
 * risk class and check summaries. Oversight only; decisions are immutable.
 */
function JudgeActivityTab() {
  const [events, setEvents] = useState<JudgeDecisionEvent[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    (async () => {
      try {
        const response = await fetchJudgeDecisions({ limit: 100 });
        setEvents(response.decisions);
      } catch (e) {
        setError(e instanceof Error ? e.message : "Failed to load judge activity");
      } finally {
        setLoading(false);
      }
    })();
  }, []);

  if (loading) {
    return (
      <div className="space-y-3">
        {[0, 1].map((i) => (
          <Skeleton key={i} className="h-20 w-full" />
        ))}
      </div>
    );
  }
  if (error) {
    return (
      <Card>
        <CardContent className="pt-6 flex items-center gap-2 text-destructive">
          <AlertCircle className="h-4 w-4" />
          {error}
        </CardContent>
      </Card>
    );
  }
  if (events.length === 0) {
    return (
      <Card>
        <CardContent className="pt-6 text-muted-foreground text-sm">
          No judge activity yet — agent runtimes report action judgments via
          the <code>/api/judge</code> endpoints.
        </CardContent>
      </Card>
    );
  }
  return (
    <div className="space-y-3">
      {events.map((event) => (
        <Card key={event.decision_id}>
          <CardContent className="pt-6">
            <div className="flex items-start justify-between gap-4">
              <div className="min-w-0 flex-1">
                <p className="text-sm">{event.reasoning_summary}</p>
                <div className="mt-2 flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
                  <Badge variant={judgeDecisionBadgeVariant(event.decision)}>
                    {event.decision}
                  </Badge>
                  <span>{event.risk_class}</span>
                  {event.runtime_name && <span>· {event.runtime_name}</span>}
                  {event.task_id && <span>· {event.task_id}</span>}
                  {Object.entries(event.checks || {}).map(([check, outcome]) => (
                    <span key={check}>
                      {check}:{outcome}
                    </span>
                  ))}
                  {event.created_at && (
                    <span>
                      · {new Date(event.created_at).toLocaleDateString()}
                    </span>
                  )}
                </div>
              </div>
            </div>
          </CardContent>
        </Card>
      ))}
    </div>
  );
}

/**
 * Unified review queue — pending captures + agent memories across kinds.
 * The server resolves the record kind on review (POST /api/memories/{id}/review).
 */
function ReviewQueueTab() {
  const [items, setItems] = useState<ReviewQueueItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);

  const loadQueue = useCallback(async () => {
    try {
      setError(null);
      const response = await fetchReviewQueue({ limit: 100 });
      setItems(response.items);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load review queue");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    loadQueue();
  }, [loadQueue]);

  const handleReview = async (id: string, action: MemoryReviewAction) => {
    setActionError(null);
    try {
      await reviewRecord(id, action);
    } catch (e) {
      setActionError(
        e instanceof Error ? e.message : `Review action '${action}' failed`,
      );
    }
    await loadQueue();
  };

  if (loading) {
    return (
      <div className="space-y-3">
        {[0, 1].map((i) => (
          <Skeleton key={i} className="h-24 w-full" />
        ))}
      </div>
    );
  }
  if (error) {
    return (
      <Card>
        <CardContent className="pt-6 flex items-center gap-2 text-destructive">
          <AlertCircle className="h-4 w-4" />
          {error}
        </CardContent>
      </Card>
    );
  }
  if (items.length === 0) {
    return (
      <Card>
        <CardContent className="pt-6 text-muted-foreground text-sm">
          Nothing pending — agent-written memories and new captures land here
          for review before they can guide behavior.
        </CardContent>
      </Card>
    );
  }
  return (
    <div className="space-y-3">
      {actionError && (
        <Card>
          <CardContent className="pt-6 flex items-center gap-2 text-destructive text-sm">
            <AlertCircle className="h-4 w-4" />
            {actionError}
          </CardContent>
        </Card>
      )}
      {items.map((item) => (
        <Card key={item.id}>
          <CardContent className="pt-6">
            <div className="flex items-start justify-between gap-4">
              <div className="min-w-0 flex-1">
                <p className="text-sm whitespace-pre-wrap break-words">
                  {item.content}
                </p>
                <div className="mt-2 flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
                  <Badge variant="outline">{item.record_kind}</Badge>
                  {item.memory_type && (
                    <Badge variant="secondary">{item.memory_type}</Badge>
                  )}
                  <span>{item.provenance_status}</span>
                  {item.runtime_name && <span>· {item.runtime_name}</span>}
                  {item.task_id && <span>· {item.task_id}</span>}
                  <span>
                    · {new Date(item.created_at).toLocaleDateString()}
                  </span>
                </div>
              </div>
              <div className="flex shrink-0 gap-1">
                <Button
                  variant="outline"
                  size="sm"
                  title="Confirm — makes this instruction-grade"
                  onClick={() => handleReview(item.id, "confirm")}
                >
                  <Check className="h-4 w-4 mr-1" />
                  Confirm
                </Button>
                <Button
                  variant="outline"
                  size="sm"
                  title="Keep as evidence only"
                  onClick={() => handleReview(item.id, "evidence_only")}
                >
                  <CopyCheck className="h-4 w-4 mr-1" />
                  Evidence
                </Button>
                <Button
                  variant="outline"
                  size="sm"
                  title="Reject — excluded from retrieval"
                  onClick={() => handleReview(item.id, "reject")}
                >
                  <X className="h-4 w-4 mr-1" />
                  Reject
                </Button>
              </div>
            </div>
          </CardContent>
        </Card>
      ))}
    </div>
  );
}

function CaptureRow({
  capture,
  onReview,
}: {
  capture: Capture;
  onReview: (id: string, action: CaptureReviewAction) => void;
}) {
  const topics = capture.enrichment?.topics ?? [];
  const captureType = capture.enrichment?.type;

  return (
    <Card>
      <CardContent className="pt-6">
        <div className="flex items-start justify-between gap-4">
          <div className="min-w-0 flex-1">
            <Link href={`/memory/${capture.id}`} className="block hover:opacity-80">
              <p className="text-sm whitespace-pre-wrap break-words">
                {capture.content}
              </p>
            </Link>
            <div className="mt-2 flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
              <Badge variant={captureReviewBadgeVariant(capture.review_status)}>
                {capture.review_status}
              </Badge>
              {capture.can_use_as_instruction && (
                <Badge variant="success">
                  <Scale className="h-3 w-3 mr-1" />
                  instruction
                </Badge>
              )}
              <span>{capture.source}</span>
              {captureType && <span>· {captureType}</span>}
              {topics.map((topic) => (
                <span key={topic}>#{topic}</span>
              ))}
              <span>
                · {new Date(capture.created_at).toLocaleDateString()}
              </span>
            </div>
          </div>
          {capture.review_status === "pending" && (
            <div className="flex shrink-0 gap-1">
              <Button
                variant="outline"
                size="sm"
                title="Confirm — makes this instruction-grade"
                onClick={() => onReview(capture.id, "confirm")}
              >
                <Check className="h-4 w-4 mr-1" />
                Confirm
              </Button>
              <Button
                variant="outline"
                size="sm"
                title="Keep as evidence only"
                onClick={() => onReview(capture.id, "evidence_only")}
              >
                <CopyCheck className="h-4 w-4 mr-1" />
                Evidence
              </Button>
              <Button
                variant="outline"
                size="sm"
                title="Reject — excluded from retrieval"
                onClick={() => onReview(capture.id, "reject")}
              >
                <X className="h-4 w-4 mr-1" />
                Reject
              </Button>
            </div>
          )}
        </div>
      </CardContent>
    </Card>
  );
}
