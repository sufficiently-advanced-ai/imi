'use client';

// Confirm dialog for merging two duplicate entities (manual select-two).
// The user picks which node survives (primary); the other is merged into it
// and archived. A non-mutating preview shows the impact before confirming.

import { useCallback, useEffect, useState } from 'react';
import { Button } from '@/components/ui/button';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { useToast } from '@/components/ui/use-toast';
import { mergeNodes, type MergeImpact } from '@/lib/api/graph-mutations';
import type { DomainGraphNode } from '@/lib/api/domain';

interface MergeNodesDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  nodeA: DomainGraphNode | null;
  nodeB: DomainGraphNode | null;
  onMerged?: () => void;
}

function nodeLabel(node: DomainGraphNode | null): string {
  if (!node) return '';
  return String(node.attributes?.name ?? node.id);
}

export function MergeNodesDialog({
  open,
  onOpenChange,
  nodeA,
  nodeB,
  onMerged,
}: MergeNodesDialogProps) {
  const { toast } = useToast();
  const [primaryId, setPrimaryId] = useState<string>('');
  const [impact, setImpact] = useState<MergeImpact | null>(null);
  const [loadingPreview, setLoadingPreview] = useState(false);
  const [merging, setMerging] = useState(false);

  // Default the survivor to the first selected node whenever the pair changes.
  useEffect(() => {
    if (open && nodeA) setPrimaryId(nodeA.id);
  }, [open, nodeA]);

  const duplicateId =
    primaryId && nodeA && nodeB
      ? primaryId === nodeA.id
        ? nodeB.id
        : nodeA.id
      : '';

  const loadPreview = useCallback(async () => {
    if (!primaryId || !duplicateId) return;
    setLoadingPreview(true);
    setImpact(null);
    try {
      const res = await mergeNodes(primaryId, duplicateId, { preview: true });
      setImpact(res.merge_impact ?? null);
    } catch (err) {
      // A failed preview shouldn't block the merge; surface it quietly.
      toast({
        title: 'Could not load merge preview',
        description: err instanceof Error ? err.message : String(err),
        variant: 'destructive',
      });
    } finally {
      setLoadingPreview(false);
    }
  }, [primaryId, duplicateId, toast]);

  useEffect(() => {
    if (open && primaryId && duplicateId) loadPreview();
  }, [open, primaryId, duplicateId, loadPreview]);

  const handleMerge = async () => {
    if (!primaryId || !duplicateId) return;
    setMerging(true);
    try {
      const res = await mergeNodes(primaryId, duplicateId);
      const transferred =
        (res.merge_summary?.relationships_transferred as number | undefined) ?? 0;
      toast({
        title: 'Entities merged',
        description: `Merged into ${primaryId} — ${transferred} relationship(s) transferred`,
      });
      onMerged?.();
      onOpenChange(false);
    } catch (err) {
      toast({
        title: 'Merge failed',
        description: err instanceof Error ? err.message : String(err),
        variant: 'destructive',
      });
    } finally {
      setMerging(false);
    }
  };

  if (!nodeA || !nodeB) return null;

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-lg">
        <DialogHeader>
          <DialogTitle>Merge duplicates</DialogTitle>
          <DialogDescription>
            Choose which entity survives. The other is merged into it and
            archived; its relationships and names are transferred.
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-3 py-2">
          {[nodeA, nodeB].map((node) => (
            <label
              key={node.id}
              className="flex cursor-pointer items-start gap-3 rounded-md border p-3 text-sm hover:bg-accent"
            >
              <input
                type="radio"
                name="merge-primary"
                className="mt-1"
                checked={primaryId === node.id}
                onChange={() => setPrimaryId(node.id)}
              />
              <span className="min-w-0">
                <span className="block font-medium">{nodeLabel(node)}</span>
                <span className="block truncate font-mono text-xs text-muted-foreground">
                  {node.id}
                </span>
                <span className="text-xs text-muted-foreground">
                  {primaryId === node.id ? 'Survivor (keeps its data)' : 'Merged away'}
                </span>
              </span>
            </label>
          ))}

          <div className="rounded-md bg-muted/40 p-3 text-xs">
            {loadingPreview ? (
              <span className="text-muted-foreground">Calculating impact…</span>
            ) : impact ? (
              <ul className="space-y-1">
                <li>
                  Relationships transferred:{' '}
                  <strong>{impact.relationships_affected ?? 0}</strong>
                </li>
                <li>
                  Aliases added:{' '}
                  <strong>{impact.aliases_to_merge?.length ?? 0}</strong>
                </li>
                {impact.data_conflicts && impact.data_conflicts.length > 0 && (
                  <li className="text-destructive">
                    {impact.data_conflicts.length} field conflict(s) — the
                    survivor&apos;s values win:{' '}
                    {impact.data_conflicts.map((c) => c.field).join(', ')}
                  </li>
                )}
              </ul>
            ) : (
              <span className="text-muted-foreground">No impact details available.</span>
            )}
          </div>
        </div>

        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)} disabled={merging}>
            Cancel
          </Button>
          <Button onClick={handleMerge} disabled={merging || !duplicateId}>
            {merging ? 'Merging…' : 'Merge'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
