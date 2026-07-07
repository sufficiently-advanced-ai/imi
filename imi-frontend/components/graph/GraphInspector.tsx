'use client';

import React from 'react';
import { useDomain } from '@/contexts/DomainContext';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { X } from 'lucide-react';
import type { DomainGraphNode, DomainGraphEdge } from '@/lib/api/domain';

interface GraphInspectorProps {
  selectedNode: DomainGraphNode | null;
  selectedEdge: DomainGraphEdge | null;
  onClose(): void;
  onShowDetails(): void;
  onEdit(node: DomainGraphNode): void;
  onRecenter(nodeId: string): void;
  onDeleteNode(node: DomainGraphNode): void;
  onDeleteEdge(edge: DomainGraphEdge): void;
}

// Signal metadata fields surfaced when a node represents a signal (decision,
// action item, key point). Additive — plain rows below the connection count.
const SIGNAL_FIELDS: Array<{ key: string; label: string }> = [
  { key: 'signal_type', label: 'Signal type' },
  { key: 'status', label: 'Status' },
  { key: 'owner_name', label: 'Owner' },
  { key: 'confidence', label: 'Confidence' },
  { key: 'source_meeting', label: 'Source meeting' },
];

/**
 * Floating inspector (Task C2): selected-node card, selected-edge card. The
 * page renders it as a right-side overlay (desktop) or Sheet (mobile) only
 * when something is selected — there is no empty state anymore, since the page
 * unmounts the inspector when nothing is selected. A close (X) button in the
 * header is wired to `onClose` (the page clears selectedNode/selectedEdge).
 * Node/edge details and the quick-action row are unchanged; signal nodes get a
 * small additive metadata block.
 */
export function GraphInspector({
  selectedNode,
  selectedEdge,
  onClose,
  onShowDetails,
  onEdit,
  onRecenter,
  onDeleteNode,
  onDeleteEdge,
}: GraphInspectorProps) {
  const { getEntityDisplayName } = useDomain();

  if (selectedNode) {
    const attrs = selectedNode.attributes ?? {};
    const isSignal = Boolean(attrs.signal_type);
    const degree = (selectedNode as { degree?: number }).degree;

    return (
      <Card>
        <CardHeader className="pb-3">
          <CardTitle className="flex items-start justify-between gap-2 text-base">
            <span className="min-w-0 flex-1 truncate" title={attrs.name || selectedNode.id}>
              {attrs.name || selectedNode.id}
            </span>
            <div className="flex shrink-0 items-center gap-1.5">
              <Button size="sm" variant="outline" onClick={onShowDetails}>
                Details
              </Button>
              <Button
                size="icon"
                variant="ghost"
                onClick={onClose}
                className="h-8 w-8"
                aria-label="Close inspector"
              >
                <X className="h-4 w-4" />
              </Button>
            </div>
          </CardTitle>
          <Badge variant="secondary" className="self-start">
            {getEntityDisplayName(selectedNode.entityType, false)}
          </Badge>
        </CardHeader>
        <CardContent className="space-y-3 text-sm text-muted-foreground">
          {typeof degree === 'number' && (
            <div className="flex justify-between">
              <span>Connections</span>
              <span className="font-medium text-foreground">{degree}</span>
            </div>
          )}

          {isSignal && (
            <dl className="space-y-1 border-t pt-2">
              {SIGNAL_FIELDS.map(({ key, label }) => {
                const value = attrs[key];
                if (value === undefined || value === null || value === '') return null;
                return (
                  <div key={key} className="flex justify-between gap-2 text-xs">
                    <dt className="text-muted-foreground">{label}</dt>
                    <dd className="min-w-0 truncate text-right font-medium text-foreground">
                      {String(value)}
                    </dd>
                  </div>
                );
              })}
            </dl>
          )}

          {/* Quick actions — same paths as the node context menu. */}
          <div className="flex flex-wrap gap-1.5 border-t pt-3">
            <Button size="sm" variant="outline" onClick={() => onEdit(selectedNode)}>
              Edit
            </Button>
            <Button size="sm" variant="outline" onClick={() => onRecenter(selectedNode.id)}>
              Re-center
            </Button>
            <Button
              size="sm"
              variant="outline"
              className="text-destructive hover:text-destructive"
              onClick={() => onDeleteNode(selectedNode)}
            >
              Delete
            </Button>
          </div>
        </CardContent>
      </Card>
    );
  }

  if (selectedEdge) {
    return (
      <Card>
        <CardHeader className="pb-3">
          <CardTitle className="flex items-center justify-between gap-2 text-base">
            <span>Selected relationship</span>
            <Button
              size="icon"
              variant="ghost"
              onClick={onClose}
              className="h-8 w-8 shrink-0"
              aria-label="Close inspector"
            >
              <X className="h-4 w-4" />
            </Button>
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-2 text-sm">
          <Badge variant="secondary" className="font-mono text-xs">
            {selectedEdge.relationshipType || selectedEdge.relationship_type}
          </Badge>
          <div className="space-y-1 pt-1 text-muted-foreground">
            <div>
              <span className="text-xs uppercase tracking-wide">From</span>
              <p className="text-foreground">{selectedEdge.source}</p>
            </div>
            <div>
              <span className="text-xs uppercase tracking-wide">To</span>
              <p className="text-foreground">{selectedEdge.target}</p>
            </div>
          </div>
          {/* Quick actions — same path as the edge context menu. */}
          <div className="flex flex-wrap gap-1.5 border-t pt-3">
            <Button
              size="sm"
              variant="outline"
              className="text-destructive hover:text-destructive"
              onClick={() => onDeleteEdge(selectedEdge)}
            >
              Delete relationship
            </Button>
          </div>
        </CardContent>
      </Card>
    );
  }

  // The page only mounts the inspector when a node/edge is selected, so this
  // branch is unreachable in practice; return null defensively.
  return null;
}
