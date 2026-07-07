"use client";

import React, { useEffect, useState } from "react";
import Link from "next/link";
import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { PageHeader } from "@/components/ui/page-header";
import { PageContainer } from "@/components/ui/page-container";
import { AlertCircle, Check, X } from "lucide-react";
import {
  fetchRecallTrace,
  fetchRecallTraces,
  type RecallTraceItem,
  type RecallTraceSummary,
} from "@/lib/api/agent-memory";

/**
 * Recall traces — the debugger (OB1 absorption Phase 5).
 * What agents asked, what memory was returned at which rank, and what they
 * reported using or ignoring.
 */
export default function MemoryTracesPage() {
  const [traces, setTraces] = useState<RecallTraceSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [expanded, setExpanded] = useState<Record<string, RecallTraceItem[]>>({});

  useEffect(() => {
    (async () => {
      try {
        const response = await fetchRecallTraces({ limit: 100 });
        setTraces(response.traces);
      } catch (e) {
        setError(e instanceof Error ? e.message : "Failed to load traces");
      } finally {
        setLoading(false);
      }
    })();
  }, []);

  const toggle = async (requestId: string) => {
    if (expanded[requestId]) {
      setExpanded((prev) => {
        const next = { ...prev };
        delete next[requestId];
        return next;
      });
      return;
    }
    try {
      const detail = await fetchRecallTrace(requestId);
      setExpanded((prev) => ({ ...prev, [requestId]: detail.items ?? [] }));
    } catch {
      // detail fetch failure leaves the row collapsed
    }
  };

  return (
    <PageContainer>
      <PageHeader
        title="Recall traces"
        description="What agents recalled, at which rank, and what they actually used."
      />

      {loading ? (
        <div className="space-y-3">
          {[0, 1, 2].map((i) => (
            <Skeleton key={i} className="h-16 w-full" />
          ))}
        </div>
      ) : error ? (
        <Card>
          <CardContent className="pt-6 flex items-center gap-2 text-destructive">
            <AlertCircle className="h-4 w-4" />
            {error}
          </CardContent>
        </Card>
      ) : traces.length === 0 ? (
        <Card>
          <CardContent className="pt-6 text-muted-foreground text-sm">
            No recall traces yet — every memory_recall / judge recall leaves
            one.
          </CardContent>
        </Card>
      ) : (
        <div className="space-y-3">
          {traces.map((trace) => (
            <Card key={trace.request_id}>
              <CardContent className="pt-6">
                <button
                  type="button"
                  className="w-full text-left"
                  onClick={() => toggle(trace.request_id)}
                >
                  <p className="text-sm font-medium">{trace.query}</p>
                  <div className="mt-1 flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
                    <Badge variant="outline">{trace.authority}</Badge>
                    <span>{trace.surface}</span>
                    {trace.runtime_name && <span>· {trace.runtime_name}</span>}
                    {trace.task_id && <span>· {trace.task_id}</span>}
                    {trace.created_at && (
                      <span>
                        · {new Date(trace.created_at).toLocaleString()}
                      </span>
                    )}
                  </div>
                </button>

                {expanded[trace.request_id] && (
                  <ol className="mt-3 space-y-1 border-t pt-3">
                    {expanded[trace.request_id].map((item) => (
                      <li
                        key={item.record_id}
                        className="flex flex-wrap items-center gap-2 text-xs"
                      >
                        <span className="text-muted-foreground">
                          #{item.rank}
                        </span>
                        <Link
                          className="underline"
                          href={`/memory/${item.record_id}`}
                        >
                          {item.record_id}
                        </Link>
                        <Badge variant="outline">{item.record_kind}</Badge>
                        {item.similarity != null && (
                          <span>sim {item.similarity.toFixed(2)}</span>
                        )}
                        {item.used === true && (
                          <span className="text-green-600 dark:text-green-400">
                            <Check className="inline h-3 w-3" /> used
                          </span>
                        )}
                        {item.used === false && (
                          <span className="text-muted-foreground">
                            <X className="inline h-3 w-3" /> ignored
                            {item.ignored_reason && ` — ${item.ignored_reason}`}
                          </span>
                        )}
                      </li>
                    ))}
                    {expanded[trace.request_id].length === 0 && (
                      <li className="text-xs text-muted-foreground">
                        Nothing returned.
                      </li>
                    )}
                  </ol>
                )}
              </CardContent>
            </Card>
          ))}
        </div>
      )}
    </PageContainer>
  );
}
