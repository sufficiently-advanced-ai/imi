"use client";

/**
 * Warns when the knowledge graph may not reflect the corpus on disk — the
 * startup reconcile adopted the disk state as baseline without ingesting it,
 * or a needed full rebuild did not complete. Renders nothing otherwise
 * (including when the status cannot be fetched: the banner is advisory).
 */

import React, { useEffect, useState } from "react";
import { AlertTriangle } from "lucide-react";

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import {
  fetchCorpusSync,
  STALE_CORPUS_STATUSES,
  type CorpusSyncStatus,
} from "@/lib/api/corpus-sync";

const TITLES: Record<string, string> = {
  adopted: "The graph may be missing corpus files",
  rebuild_required: "The graph needs a full rebuild",
};

export function CorpusSyncBanner() {
  const [sync, setSync] = useState<CorpusSyncStatus | null>(null);

  useEffect(() => {
    let cancelled = false;
    fetchCorpusSync()
      .then((s) => {
        if (!cancelled) setSync(s);
      })
      .catch(() => {
        // Advisory only — omit on failure.
      });
    return () => {
      cancelled = true;
    };
  }, []);

  if (!sync || !STALE_CORPUS_STATUSES.has(sync.status)) return null;

  return (
    <Alert variant="warning" data-testid="corpus-sync-banner">
      <AlertTriangle className="h-4 w-4" />
      <AlertTitle>{TITLES[sync.status]}</AlertTitle>
      <AlertDescription>
        {sync.warnings.map((w) => (
          <p key={w}>{w}</p>
        ))}
        <p>
          Run a graph rebuild (<code>POST /api/admin/rebuild-graph</code>) to
          re-ingest the corpus; this notice clears when it completes.
        </p>
      </AlertDescription>
    </Alert>
  );
}

export default CorpusSyncBanner;
