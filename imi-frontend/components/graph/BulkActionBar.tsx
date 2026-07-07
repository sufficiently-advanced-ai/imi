'use client';

// Floating action bar that appears when nodes are multi-selected in the
// graph. Surfaces signal-specific bulk actions (close, reopen) when the
// selection includes signal nodes, plus generic clear/select.

import { useState } from 'react';
import { Button } from '@/components/ui/button';
import { useToast } from '@/components/ui/use-toast';
import { bulkCloseSignals, bulkSetSignalStatus } from '@/lib/api/graph-mutations';

interface SelectableNode {
  id: string;
  entityType: string;
  attributes: Record<string, unknown>;
}

interface BulkActionBarProps {
  selectedNodes: SelectableNode[];
  onClear: () => void;
  // Called after a successful bulk action so the caller can refresh data.
  onAction?: () => void;
}

export function BulkActionBar({
  selectedNodes,
  onClear,
  onAction,
}: BulkActionBarProps) {
  const { toast } = useToast();
  const [busy, setBusy] = useState(false);

  if (selectedNodes.length === 0) return null;

  // Signal entities are surfaced in the graph via entityType === "signal".
  // The underlying node id format is "signal-<uuid>" per chat_tools.py; we
  // strip the prefix before sending to the signal mutation API.
  const signalIds = selectedNodes
    .filter((n) => n.entityType === 'signal')
    .map((n) => (n.id.startsWith('signal-') ? n.id.slice('signal-'.length) : n.id));

  const runBulk = async (
    action: () => Promise<{ total: number; succeeded: number; failed: number }>,
    verb: string,
  ) => {
    setBusy(true);
    try {
      const result = await action();
      toast({
        title: `${verb}: ${result.succeeded}/${result.total}`,
        description:
          result.failed > 0 ? `${result.failed} failed — see server logs` : undefined,
        variant: result.failed > 0 ? 'destructive' : 'default',
      });
      onAction?.();
    } catch (err) {
      toast({
        title: `Bulk ${verb.toLowerCase()} failed`,
        description: err instanceof Error ? err.message : String(err),
        variant: 'destructive',
      });
    } finally {
      setBusy(false);
    }
  };

  return (
    <div
      className="fixed bottom-6 left-1/2 z-40 -translate-x-1/2 rounded-lg border bg-background/95 px-4 py-2 shadow-lg backdrop-blur"
      role="toolbar"
      aria-label="Bulk actions"
    >
      <div className="flex items-center gap-3 text-sm">
        <span className="font-medium">
          {selectedNodes.length} selected
          {signalIds.length > 0 && (
            <span className="ml-1 text-muted-foreground">
              ({signalIds.length} signal{signalIds.length === 1 ? '' : 's'})
            </span>
          )}
        </span>
        <div className="h-4 w-px bg-border" />
        {signalIds.length > 0 && (
          <>
            <Button
              size="sm"
              variant="default"
              disabled={busy}
              onClick={() => runBulk(() => bulkCloseSignals(signalIds), 'Closed')}
            >
              Close
            </Button>
            <Button
              size="sm"
              variant="outline"
              disabled={busy}
              onClick={() =>
                runBulk(
                  () => bulkSetSignalStatus(signalIds, 'open'),
                  'Reopened',
                )
              }
            >
              Reopen
            </Button>
            <Button
              size="sm"
              variant="outline"
              disabled={busy}
              onClick={() =>
                runBulk(
                  () => bulkSetSignalStatus(signalIds, 'in_progress'),
                  'In-progress',
                )
              }
            >
              In progress
            </Button>
            <div className="h-4 w-px bg-border" />
          </>
        )}
        <Button size="sm" variant="ghost" onClick={onClear} disabled={busy}>
          Clear
        </Button>
      </div>
    </div>
  );
}
