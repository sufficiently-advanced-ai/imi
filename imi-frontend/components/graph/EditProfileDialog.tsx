'use client';

// Edit an entity's AI-generated profile. Two layers:
//   • Body — immediate narrative fix, but the AI enricher may re-derive it on
//     the next meeting (we warn about this).
//   • Corrections — durable facts stored in frontmatter and injected into the
//     authoritative grounding block, so they survive rebuilds AND regeneration.

import { useEffect, useState } from 'react';
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
import { Textarea } from '@/components/ui/textarea';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { useToast } from '@/components/ui/use-toast';
import {
  getProfileEditable,
  updateProfileBody,
  updateCorrections,
} from '@/lib/api/graph-mutations';

interface EditProfileDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  nodeId: string;
  onSaved?: () => void;
}

function correctionsEqual(a: string[], b: string[]): boolean {
  if (a.length !== b.length) return false;
  return a.every((v, i) => v === b[i]);
}

export function EditProfileDialog({
  open,
  onOpenChange,
  nodeId,
  onSaved,
}: EditProfileDialogProps) {
  const { toast } = useToast();
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [body, setBody] = useState('');
  const [initialBody, setInitialBody] = useState('');
  const [corrections, setCorrections] = useState<string[]>([]);
  const [initialCorrections, setInitialCorrections] = useState<string[]>([]);
  const [newCorrection, setNewCorrection] = useState('');

  useEffect(() => {
    if (!open) return;
    let cancelled = false;
    setLoading(true);
    getProfileEditable(nodeId)
      .then((data) => {
        if (cancelled) return;
        setBody(data.body ?? '');
        setInitialBody(data.body ?? '');
        const corr = data.manual_corrections ?? [];
        setCorrections(corr);
        setInitialCorrections(corr);
      })
      .catch((err) => {
        if (cancelled) return;
        toast({
          title: 'Could not load profile',
          description: err instanceof Error ? err.message : String(err),
          variant: 'destructive',
        });
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    setNewCorrection('');
    return () => {
      cancelled = true;
    };
  }, [open, nodeId, toast]);

  const addCorrection = (value: string) => {
    const trimmed = value.trim();
    if (!trimmed) return;
    setCorrections((prev) => (prev.includes(trimmed) ? prev : [...prev, trimmed]));
    setNewCorrection('');
  };

  const handleSave = async () => {
    setSaving(true);
    try {
      const tasks: Promise<unknown>[] = [];
      if (body !== initialBody) tasks.push(updateProfileBody(nodeId, body));
      if (!correctionsEqual(corrections, initialCorrections)) {
        tasks.push(updateCorrections(nodeId, corrections));
      }
      if (tasks.length === 0) {
        onOpenChange(false);
        return;
      }
      // Body and corrections are independent writes — report partial failure
      // explicitly so the user isn't told everything failed when one succeeded.
      const results = await Promise.allSettled(tasks);
      const failed = results.filter((r) => r.status === 'rejected');
      if (failed.length > 0) {
        throw new Error(
          failed.length === results.length
            ? 'No changes were saved.'
            : 'Some changes were saved, but at least one update failed.',
        );
      }
      toast({ title: 'Profile saved', description: nodeId });
      onSaved?.();
      onOpenChange(false);
    } catch (err) {
      toast({
        title: 'Save failed',
        description: err instanceof Error ? err.message : String(err),
        variant: 'destructive',
      });
    } finally {
      setSaving(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="flex max-h-[85vh] max-w-2xl flex-col">
        <DialogHeader>
          <DialogTitle>Edit profile</DialogTitle>
          <DialogDescription className="truncate font-mono text-xs">
            {nodeId}
          </DialogDescription>
        </DialogHeader>

        {loading ? (
          <p className="py-8 text-center text-sm text-muted-foreground">Loading…</p>
        ) : (
          <Tabs defaultValue="corrections" className="min-h-0 flex-1">
            <TabsList>
              <TabsTrigger value="corrections">Corrections</TabsTrigger>
              <TabsTrigger value="body">Narrative</TabsTrigger>
            </TabsList>

            <TabsContent value="corrections" className="space-y-3">
              <p className="text-xs text-muted-foreground">
                Durable — always honored when the profile is regenerated. Best
                for fixing a recurring bad fact (e.g. a wrong title or a
                misattributed responsibility).
              </p>
              {corrections.length > 0 && (
                <ul className="space-y-1.5">
                  {corrections.map((c) => (
                    <li
                      key={c}
                      className="flex items-start justify-between gap-2 rounded-md border p-2 text-sm"
                    >
                      <span className="min-w-0">{c}</span>
                      <button
                        type="button"
                        aria-label={`Remove correction`}
                        className="shrink-0 text-muted-foreground hover:text-destructive"
                        onClick={() =>
                          setCorrections((prev) => prev.filter((x) => x !== c))
                        }
                      >
                        ×
                      </button>
                    </li>
                  ))}
                </ul>
              )}
              <Input
                placeholder="Add a correction and press Enter"
                value={newCorrection}
                onChange={(e) => setNewCorrection(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === 'Enter') {
                    e.preventDefault();
                    addCorrection(newCorrection);
                  }
                }}
              />
            </TabsContent>

            <TabsContent value="body" className="space-y-2">
              <div className="rounded-md bg-amber-500/10 p-2 text-xs text-amber-700 dark:text-amber-400">
                Direct edits to the narrative may be re-derived when this profile
                is regenerated. For a permanent fix, add a Correction instead.
              </div>
              <Label htmlFor="profile-body" className="sr-only">
                Profile body
              </Label>
              <Textarea
                id="profile-body"
                value={body}
                onChange={(e) => setBody(e.target.value)}
                rows={16}
                className="font-mono text-xs"
              />
            </TabsContent>
          </Tabs>
        )}

        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)} disabled={saving}>
            Cancel
          </Button>
          <Button onClick={handleSave} disabled={saving || loading}>
            {saving ? 'Saving…' : 'Save'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
