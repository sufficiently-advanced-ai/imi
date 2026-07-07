'use client';

import React from 'react';
import { useDomain } from '@/contexts/DomainContext';
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover';

interface GraphStatsChipProps {
  totalNodes: number;
  totalEdges: number;
  nodesByType: Record<string, number>;
  edgesByType?: Record<string, number>;
  truncated?: boolean;
  totalAvailableNodes?: number;
}

/**
 * Ambient graph statistics (Task C2): a compact pill ("128 nodes · 342 edges")
 * the page floats in the bottom-left corner of the canvas. Clicking it opens a
 * Popover with the per-type breakdown plus the full-graph truncation notice
 * (moved here out of the toolbar). The pill itself recedes — small, uncolored,
 * low-contrast — so it reads as ambient context, not chrome.
 */
export function GraphStatsChip({
  totalNodes,
  totalEdges,
  nodesByType,
  edgesByType,
  truncated,
  totalAvailableNodes,
}: GraphStatsChipProps) {
  const { getEntityDisplayName } = useDomain();

  const hasBreakdown =
    Object.keys(nodesByType).length > 0 ||
    Object.keys(edgesByType ?? {}).length > 0 ||
    truncated;

  const pillLabel = `${totalNodes} ${totalNodes === 1 ? 'node' : 'nodes'} · ${totalEdges} ${
    totalEdges === 1 ? 'edge' : 'edges'
  }`;

  return (
    <Popover>
      <PopoverTrigger asChild>
        <button
          type="button"
          className="rounded-full border px-3 py-1 text-xs font-medium tabular-nums text-muted-foreground transition-colors hover:text-foreground"
          aria-label="Graph statistics"
        >
          {pillLabel}
          {truncated && <span className="ml-1 text-amber-600 dark:text-amber-500">·  truncated</span>}
        </button>
      </PopoverTrigger>
      {hasBreakdown && (
        <PopoverContent align="start" side="top" className="w-60 space-y-2 text-sm">
          <p className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
            Graph
          </p>
          <div className="flex justify-between">
            <span className="text-muted-foreground">Nodes</span>
            <span className="font-medium tabular-nums">{totalNodes}</span>
          </div>
          <div className="flex justify-between">
            <span className="text-muted-foreground">Edges</span>
            <span className="font-medium tabular-nums">{totalEdges}</span>
          </div>
          {Object.keys(nodesByType).length > 0 && (
            <div className="space-y-1 border-t pt-2">
              {Object.entries(nodesByType).map(([type, count]) => (
                <div key={type} className="flex justify-between text-xs">
                  <span className="text-muted-foreground">{getEntityDisplayName(type, true)}</span>
                  <span className="tabular-nums">{count as number}</span>
                </div>
              ))}
            </div>
          )}
          {truncated && (
            <p className="border-t pt-2 text-xs text-muted-foreground">
              Showing {totalNodes}
              {typeof totalAvailableNodes === 'number' && <> of {totalAvailableNodes}</>} entities
              (highest-degree first). Narrow the filters to see more.
            </p>
          )}
        </PopoverContent>
      )}
    </Popover>
  );
}
