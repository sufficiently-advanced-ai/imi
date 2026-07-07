"use client";

import React, { useEffect, useState } from "react";
import { useParams } from "next/navigation";
import Link from "next/link";
import { Card, CardContent, CardHeader } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { PageHeader } from "@/components/ui/page-header";
import { PageContainer } from "@/components/ui/page-container";
import { AlertCircle, Scale } from "lucide-react";
import {
  fetchInspector,
  type InspectorResponse,
} from "@/lib/api/agent-memory";

/**
 * Memory inspector — the trust surface (OB1 absorption Phase 5).
 * Answers: why does this memory exist, what created it, how was it used,
 * and what can it influence. Deleted records still answer from their audit
 * trail (the audit-survives-deletion guarantee, made visible).
 */
export default function MemoryInspectorPage() {
  const params = useParams<{ id: string }>();
  const recordId = params?.id;

  const [data, setData] = useState<InspectorResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!recordId) {
      return;
    }
    // Reset before fetching: lineage/superseded-by links navigate within this
    // same route, reusing the component instance — stale error/data from the
    // previous record must not leak into the new one.
    setData(null);
    setError(null);
    setLoading(true);
    (async () => {
      try {
        setData(await fetchInspector(recordId));
      } catch (e) {
        setError(e instanceof Error ? e.message : "Failed to load inspector");
      } finally {
        setLoading(false);
      }
    })();
  }, [recordId]);

  if (loading) {
    return (
      <PageContainer>
        <Skeleton className="h-40 w-full" />
      </PageContainer>
    );
  }
  if (error || !data) {
    return (
      <PageContainer>
        <Card>
          <CardContent className="pt-6 flex items-center gap-2 text-destructive">
            <AlertCircle className="h-4 w-4" />
            {error ?? "Record not found"}
          </CardContent>
        </Card>
      </PageContainer>
    );
  }

  const record = data.record as Record<string, unknown> | null;

  return (
    <PageContainer>
      <PageHeader
        title="Memory inspector"
        description={`${data.record_kind} · ${data.record_id}`}
      />

      {/* 1. Why does this memory exist? */}
      <Card className="mb-4">
        <CardHeader className="text-sm font-medium">Record</CardHeader>
        <CardContent>
          {record ? (
            <>
              <p className="text-sm whitespace-pre-wrap break-words">
                {String(record.content ?? "")}
              </p>
              <div className="mt-2 flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
                <Badge variant="outline">
                  {String(record.provenance_status ?? "")}
                </Badge>
                <span>{String(record.review_status ?? "")}</span>
                {typeof record.created_at === "string" && (
                  <span>
                    · {new Date(record.created_at).toLocaleString()}
                  </span>
                )}
              </div>
            </>
          ) : (
            <p className="text-sm text-muted-foreground">
              This record was deleted — the audit history below survives it.
            </p>
          )}
        </CardContent>
      </Card>

      {/* 4. What can it influence? */}
      <Card className="mb-4">
        <CardHeader className="text-sm font-medium">Influence</CardHeader>
        <CardContent>
          <div className="flex flex-wrap items-center gap-2 text-sm">
            <Badge
              variant={
                data.influence.position === "instruction"
                  ? "success"
                  : data.influence.position === "evidence"
                    ? "outline"
                    : "destructive"
              }
            >
              <Scale className="h-3 w-3 mr-1" />
              {data.influence.position}
            </Badge>
            {data.influence.superseded_by && (
              <span className="text-xs text-muted-foreground">
                superseded by{" "}
                <Link
                  className="underline"
                  href={`/memory/${data.influence.superseded_by}`}
                >
                  {data.influence.superseded_by}
                </Link>
              </span>
            )}
          </div>
          {data.lineage.length > 1 && (
            <div className="mt-2 text-xs text-muted-foreground">
              Lineage:{" "}
              {data.lineage.map((entry, index) => (
                <span key={entry.record_id}>
                  {index > 0 && " → "}
                  {entry.relation === "self" ? (
                    entry.record_id
                  ) : (
                    <Link className="underline" href={`/memory/${entry.record_id}`}>
                      {entry.record_id}
                    </Link>
                  )}
                </span>
              ))}
            </div>
          )}
        </CardContent>
      </Card>

      {/* 3. How has it been used? */}
      <Card className="mb-4">
        <CardHeader className="text-sm font-medium">Usage</CardHeader>
        <CardContent className="text-sm">
          <div className="flex flex-wrap gap-4">
            <span>Returned {data.usage.times_returned}×</span>
            <span>Used {data.usage.times_used}×</span>
            <span>Ignored {data.usage.times_ignored}×</span>
            {data.usage.last_returned_at && (
              <span className="text-muted-foreground">
                last {new Date(data.usage.last_returned_at).toLocaleString()}
              </span>
            )}
          </div>
          {data.judge_usage.length > 0 && (
            <div className="mt-2 text-xs text-muted-foreground">
              Judge decisions:{" "}
              {data.judge_usage.map((usage) => (
                <span key={usage.decision_id} className="mr-2">
                  {usage.decision} ({usage.used_as})
                </span>
              ))}
            </div>
          )}
        </CardContent>
      </Card>

      {/* 2. What created/changed it? */}
      <Card>
        <CardHeader className="text-sm font-medium">Audit history</CardHeader>
        <CardContent>
          {data.audit_history.length === 0 ? (
            <p className="text-sm text-muted-foreground">No audit rows.</p>
          ) : (
            <ol className="space-y-2">
              {data.audit_history.map((row, index) => (
                <li key={index} className="text-sm">
                  <span className="font-medium">{row.action}</span>
                  {row.gate_response && (
                    <Badge variant="outline" className="ml-2">
                      {row.gate_response}
                    </Badge>
                  )}
                  {row.actor && (
                    <span className="text-muted-foreground"> by {row.actor}</span>
                  )}
                  <div className="text-xs text-muted-foreground">
                    {row.reasoning}
                  </div>
                  <div className="text-xs text-muted-foreground">
                    {new Date(row.created_at).toLocaleString()}
                  </div>
                </li>
              ))}
            </ol>
          )}
        </CardContent>
      </Card>
    </PageContainer>
  );
}
