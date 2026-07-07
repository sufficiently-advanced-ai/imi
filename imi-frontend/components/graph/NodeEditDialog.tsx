'use client';

// Edit dialog for a graph node. Renders typed inputs based on the domain
// attribute schema, falling back to text inputs for unknown keys. Saving
// calls PUT /api/entities/{id} via updateNode.

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
import { Checkbox } from '@/components/ui/checkbox';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Textarea } from '@/components/ui/textarea';
import { useToast } from '@/components/ui/use-toast';
import { updateNode, type UpdateNodeInput } from '@/lib/api/graph-mutations';

// Aliases live in frontmatter as a string list (or, defensively, a single
// string). Normalize to a clean string[] for editing.
function normalizeAliases(raw: unknown): string[] {
  if (Array.isArray(raw)) {
    const normalized = raw.map((a) => String(a).trim()).filter((a) => a.length > 0);
    return [...new Set(normalized)];
  }
  if (typeof raw === 'string' && raw.trim()) return [raw.trim()];
  return [];
}

function aliasesEqual(a: string[], b: string[]): boolean {
  if (a.length !== b.length) return false;
  return a.every((v, i) => v === b[i]);
}

interface DomainAttribute {
  name: string;
  type: string;
  required?: boolean;
  enum?: string[] | null;
}

interface DomainEntityDef {
  name: string;
  attributes?: DomainAttribute[];
}

interface NodeEditDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  nodeId: string;
  entityType: string;
  initialAttributes: Record<string, unknown>;
  entityDef: DomainEntityDef | null;
  onSaved?: () => void;
}

export function NodeEditDialog({
  open,
  onOpenChange,
  nodeId,
  entityType,
  initialAttributes,
  entityDef,
  onSaved,
}: NodeEditDialogProps) {
  const { toast } = useToast();
  const [attributes, setAttributes] = useState<Record<string, unknown>>(initialAttributes);
  const [aliases, setAliases] = useState<string[]>(normalizeAliases(initialAttributes.aliases));
  const [newAlias, setNewAlias] = useState('');
  const [keepOldNameAlias, setKeepOldNameAlias] = useState(true);
  const [saving, setSaving] = useState(false);

  // Re-seed local state when the dialog opens on a new node
  useEffect(() => {
    if (open) {
      setAttributes(initialAttributes);
      setAliases(normalizeAliases(initialAttributes.aliases));
      setNewAlias('');
      setKeepOldNameAlias(true);
    }
  }, [open, initialAttributes]);

  const addAlias = (value: string) => {
    const trimmed = value.trim();
    if (!trimmed) return;
    setAliases((prev) => (prev.includes(trimmed) ? prev : [...prev, trimmed]));
    setNewAlias('');
  };

  const removeAlias = (value: string) => {
    setAliases((prev) => prev.filter((a) => a !== value));
  };

  const setAttr = (key: string, value: unknown) => {
    setAttributes((prev) => ({ ...prev, [key]: value }));
  };

  const schemaAttrs = entityDef?.attributes || [];
  const knownKeys = new Set(schemaAttrs.map((a) => a.name));
  // Show canonical schema attrs first, then any custom keys present on the node.
  const customKeys = Object.keys(initialAttributes).filter(
    (k) => !knownKeys.has(k) && k !== 'name' && k !== 'aliases' && !k.startsWith('_'),
  );

  const handleSave = async () => {
    setSaving(true);
    try {
      // Diff against initial so we only send changed fields.
      const patch: Record<string, unknown> = {};
      for (const [k, v] of Object.entries(attributes)) {
        if (k === 'aliases') continue; // handled separately below
        if (initialAttributes[k] !== v) patch[k] = v;
      }

      // Aliases: combine manual edits with the rename→alias convenience.
      // When the name changed and the box is checked, the previous name is
      // kept as an alias so old references still resolve to this entity.
      const initialAliases = normalizeAliases(initialAttributes.aliases);
      const finalAliases = [...aliases];
      const oldName = String(initialAttributes.name ?? '').trim();
      const nameChanged = String(attributes.name ?? '') !== String(initialAttributes.name ?? '');
      if (nameChanged && keepOldNameAlias && oldName && !finalAliases.includes(oldName)) {
        finalAliases.push(oldName);
      }
      if (!aliasesEqual(finalAliases, initialAliases)) {
        patch.aliases = finalAliases;
      }

      if (Object.keys(patch).length === 0) {
        onOpenChange(false);
        return;
      }
      const body: UpdateNodeInput = { attributes: patch };
      await updateNode(nodeId, body);
      toast({
        title: 'Node updated',
        description: `${nodeId} — ${Object.keys(patch).length} field(s)`,
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

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="flex max-h-[85vh] max-w-lg flex-col">
        <DialogHeader>
          <DialogTitle>Edit {entityType}</DialogTitle>
          <DialogDescription className="truncate font-mono text-xs">
            {nodeId}
          </DialogDescription>
        </DialogHeader>

        <div className="-mr-1 min-h-0 flex-1 space-y-4 overflow-y-auto py-2 pr-1">
          {/* Name — always rendered, even if not in the canonical schema */}
          <div className="space-y-1.5">
            <Label htmlFor="name">name</Label>
            <Input
              id="name"
              value={String(attributes.name ?? '')}
              onChange={(e) => setAttr('name', e.target.value)}
            />
            <div className="flex items-center gap-2">
              <Checkbox
                id="keep-old-name-alias"
                checked={keepOldNameAlias}
                onCheckedChange={(checked) => setKeepOldNameAlias(!!checked)}
              />
              <Label
                htmlFor="keep-old-name-alias"
                className="text-xs font-normal text-muted-foreground"
              >
                Keep the previous name as an alias when renaming
              </Label>
            </div>
          </div>

          {/* Aliases — alternate surface forms that resolve to this entity */}
          <div className="space-y-1.5">
            <Label>aliases</Label>
            {aliases.length > 0 && (
              <div className="flex flex-wrap gap-1.5">
                {aliases.map((alias) => (
                  <span
                    key={alias}
                    className="inline-flex items-center gap-1 rounded-full border bg-muted/50 px-2 py-0.5 text-xs"
                  >
                    {alias}
                    <button
                      type="button"
                      aria-label={`Remove alias ${alias}`}
                      className="text-muted-foreground hover:text-destructive"
                      onClick={() => removeAlias(alias)}
                    >
                      ×
                    </button>
                  </span>
                ))}
              </div>
            )}
            <Input
              placeholder="Add an alias and press Enter"
              value={newAlias}
              onChange={(e) => setNewAlias(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter') {
                  e.preventDefault();
                  addAlias(newAlias);
                }
              }}
            />
          </div>

          {schemaAttrs
            .filter((a) => a.name !== 'name')
            .map((attr) => (
              <AttributeField
                key={attr.name}
                attr={attr}
                value={attributes[attr.name]}
                onChange={(v) => setAttr(attr.name, v)}
              />
            ))}

          {customKeys.length > 0 && (
            <div className="space-y-2 border-t pt-3">
              <p className="text-xs font-semibold uppercase text-muted-foreground">
                Custom properties
              </p>
              {customKeys.map((key) => (
                <div key={key} className="space-y-1.5">
                  <Label htmlFor={`custom-${key}`} className="text-muted-foreground">
                    {key}
                  </Label>
                  <Input
                    id={`custom-${key}`}
                    value={String(attributes[key] ?? '')}
                    onChange={(e) => setAttr(key, e.target.value)}
                  />
                </div>
              ))}
            </div>
          )}
        </div>

        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)} disabled={saving}>
            Cancel
          </Button>
          <Button onClick={handleSave} disabled={saving}>
            {saving ? 'Saving…' : 'Save'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function AttributeField({
  attr,
  value,
  onChange,
}: {
  attr: DomainAttribute;
  value: unknown;
  onChange: (v: unknown) => void;
}) {
  const id = `attr-${attr.name}`;

  if (attr.type === 'enum' && Array.isArray(attr.enum) && attr.enum.length > 0) {
    return (
      <div className="space-y-1.5">
        <Label htmlFor={id}>{attr.name}</Label>
        <select
          id={id}
          value={String(value ?? '')}
          onChange={(e) => onChange(e.target.value || null)}
          className="flex h-9 w-full rounded-md border border-input bg-background px-3 py-1 text-sm"
        >
          <option value="">—</option>
          {attr.enum.map((opt) => (
            <option key={opt} value={opt}>
              {opt}
            </option>
          ))}
        </select>
      </div>
    );
  }

  if (attr.type === 'boolean') {
    return (
      <div className="flex items-center justify-between">
        <Label htmlFor={id}>{attr.name}</Label>
        <input
          id={id}
          type="checkbox"
          checked={Boolean(value)}
          onChange={(e) => onChange(e.target.checked)}
        />
      </div>
    );
  }

  if (attr.type === 'number') {
    return (
      <div className="space-y-1.5">
        <Label htmlFor={id}>{attr.name}</Label>
        <Input
          id={id}
          type="number"
          value={value === null || value === undefined ? '' : String(value)}
          onChange={(e) =>
            onChange(e.target.value === '' ? null : Number(e.target.value))
          }
        />
      </div>
    );
  }

  if (attr.type === 'date' || attr.type === 'datetime') {
    return (
      <div className="space-y-1.5">
        <Label htmlFor={id}>{attr.name}</Label>
        <Input
          id={id}
          type={attr.type === 'date' ? 'date' : 'datetime-local'}
          value={String(value ?? '')}
          onChange={(e) => onChange(e.target.value || null)}
        />
      </div>
    );
  }

  // Fallback: long-form content uses textarea, everything else uses input.
  const isLong = typeof value === 'string' && value.length > 80;
  return (
    <div className="space-y-1.5">
      <Label htmlFor={id}>{attr.name}</Label>
      {isLong ? (
        <Textarea
          id={id}
          value={String(value ?? '')}
          onChange={(e) => onChange(e.target.value)}
          rows={3}
        />
      ) : (
        <Input
          id={id}
          value={String(value ?? '')}
          onChange={(e) => onChange(e.target.value)}
        />
      )}
    </div>
  );
}
