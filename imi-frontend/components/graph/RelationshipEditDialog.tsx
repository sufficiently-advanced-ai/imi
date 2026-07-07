'use client';

// Add, set, or redirect a relationship from a source entity. Used for
// clarifying reporting structure (e.g. "set reports_to"). Redirect mode
// removes the existing edge and adds the new one (single-valued rels like
// reports_to), since edge *existence* — not properties — is what's durable.

import { useEffect, useMemo, useRef, useState } from 'react';
import { Button } from '@/components/ui/button';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { useToast } from '@/components/ui/use-toast';
import { addRelationship, removeRelationship } from '@/lib/api/graph-mutations';
import { searchEntities, type EntitySearchResult, type DomainGraphNode } from '@/lib/api/domain';

interface RelationshipEditDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  sourceNode: DomainGraphNode | null;
  relationshipTypes: string[];
  domain?: string;
  // Redirect mode: the existing edge to replace (remove old, add new).
  existingEdge?: { relationshipType: string; target: string } | null;
  onSaved?: () => void;
}

export function RelationshipEditDialog({
  open,
  onOpenChange,
  sourceNode,
  relationshipTypes,
  domain,
  existingEdge,
  onSaved,
}: RelationshipEditDialogProps) {
  const { toast } = useToast();
  const [relType, setRelType] = useState('');
  const [query, setQuery] = useState('');
  const [results, setResults] = useState<EntitySearchResult[]>([]);
  const [target, setTarget] = useState<EntitySearchResult | null>(null);
  const [searching, setSearching] = useState(false);
  const [saving, setSaving] = useState(false);
  const searchTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    if (open) {
      setRelType(existingEdge?.relationshipType ?? relationshipTypes[0] ?? '');
      setQuery('');
      setResults([]);
      setTarget(null);
    }
  }, [open, existingEdge, relationshipTypes]);

  // Debounced entity search for picking the target.
  useEffect(() => {
    if (!open) return;
    if (searchTimer.current) clearTimeout(searchTimer.current);
    if (query.trim().length < 2) {
      setResults([]);
      return;
    }
    searchTimer.current = setTimeout(async () => {
      setSearching(true);
      try {
        const res = await searchEntities({ query: query.trim(), maxResults: 8, domain });
        // Drop the source itself and de-duplicate by id (the search can return
        // the same entity more than once), so the results list below renders
        // with stable, unique React keys.
        const seen = new Set<string>();
        setResults(
          res.filter((r) => {
            if (!r.id || r.id === sourceNode?.id || seen.has(r.id)) return false;
            seen.add(r.id);
            return true;
          }),
        );
      } catch {
        setResults([]);
      } finally {
        setSearching(false);
      }
    }, 250);
    return () => {
      if (searchTimer.current) clearTimeout(searchTimer.current);
    };
  }, [query, open, domain, sourceNode?.id]);

  const isRedirect = Boolean(existingEdge);
  const canSave = useMemo(
    () => Boolean(sourceNode && relType && target),
    [sourceNode, relType, target],
  );

  const handleSave = async () => {
    if (!sourceNode || !relType || !target) return;
    setSaving(true);
    try {
      // Redirect: single-valued rels (reports_to) reject a second target while
      // the first exists, so we must remove the old edge before adding the new
      // one. If the add then fails, re-add the original so we don't silently
      // drop the relationship.
      if (existingEdge) {
        await removeRelationship(
          sourceNode.id,
          existingEdge.relationshipType,
          existingEdge.target,
        );
        try {
          await addRelationship(sourceNode.id, {
            relationship_type: relType,
            target_entity_id: target.id,
          });
        } catch (addErr) {
          try {
            await addRelationship(sourceNode.id, {
              relationship_type: existingEdge.relationshipType,
              target_entity_id: existingEdge.target,
            });
          } catch {
            // Best-effort rollback; surface the original failure regardless.
          }
          throw addErr;
        }
      } else {
        await addRelationship(sourceNode.id, {
          relationship_type: relType,
          target_entity_id: target.id,
        });
      }
      toast({
        title: isRedirect ? 'Relationship updated' : 'Relationship added',
        description: `${sourceNode.id} —[${relType}]→ ${target.id}`,
      });
      onSaved?.();
      onOpenChange(false);
    } catch (err) {
      toast({
        title: 'Update failed',
        description: err instanceof Error ? err.message : String(err),
        variant: 'destructive',
      });
    } finally {
      setSaving(false);
    }
  };

  if (!sourceNode) return null;

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-lg">
        <DialogHeader>
          <DialogTitle>{isRedirect ? 'Redirect relationship' : 'Add relationship'}</DialogTitle>
          <DialogDescription className="truncate font-mono text-xs">
            from {sourceNode.id}
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-4 py-2">
          <div className="space-y-1.5">
            <Label htmlFor="rel-type">relationship</Label>
            <select
              id="rel-type"
              value={relType}
              onChange={(e) => setRelType(e.target.value)}
              className="flex h-9 w-full rounded-md border border-input bg-background px-3 py-1 text-sm"
            >
              {relationshipTypes.length === 0 && <option value="">—</option>}
              {relationshipTypes.map((t) => (
                <option key={t} value={t}>
                  {t}
                </option>
              ))}
            </select>
          </div>

          <div className="space-y-1.5">
            <Label htmlFor="rel-target">target</Label>
            {target ? (
              <div className="flex items-center justify-between rounded-md border p-2 text-sm">
                <span className="min-w-0">
                  <span className="block font-medium">{target.name}</span>
                  <span className="block truncate font-mono text-xs text-muted-foreground">
                    {target.id}
                  </span>
                </span>
                <button
                  type="button"
                  className="text-xs text-muted-foreground hover:text-foreground"
                  onClick={() => setTarget(null)}
                >
                  Change
                </button>
              </div>
            ) : (
              <>
                <Input
                  id="rel-target"
                  placeholder="Search for an entity…"
                  value={query}
                  onChange={(e) => setQuery(e.target.value)}
                />
                {searching && (
                  <p className="text-xs text-muted-foreground">Searching…</p>
                )}
                {results.length > 0 && (
                  <div className="max-h-48 overflow-y-auto rounded-md border">
                    {results.map((r) => (
                      <button
                        key={r.id}
                        type="button"
                        className="flex w-full flex-col items-start px-2 py-1.5 text-left text-sm hover:bg-accent"
                        onClick={() => setTarget(r)}
                      >
                        <span className="font-medium">{r.name}</span>
                        <span className="truncate font-mono text-xs text-muted-foreground">
                          {r.id}
                        </span>
                      </button>
                    ))}
                  </div>
                )}
              </>
            )}
          </div>
        </div>

        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)} disabled={saving}>
            Cancel
          </Button>
          <Button onClick={handleSave} disabled={!canSave || saving}>
            {saving ? 'Saving…' : isRedirect ? 'Update' : 'Add'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
