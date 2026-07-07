'use client';

import React from 'react';
import { useDomain } from '@/contexts/DomainContext';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover';
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/tooltip';
import { Search } from 'lucide-react';
import { cn } from '@/lib/utils';
import type { GraphLayers } from './layers';

// Layer segmented-control options, in increasing-inclusivity order.
const LAYER_OPTIONS: ReadonlyArray<{ value: GraphLayers; label: string }> = [
  { value: 'entities', label: 'Entities' },
  { value: 'decisions', label: '+ Decisions' },
  { value: 'signals', label: 'All signals' },
];

interface GraphToolbarProps {
  viewMode: 'picker' | 'neighborhood' | 'full' | 'influence';
  seedDisplayName: string | null;
  seedId: string | null;
  /** Whether the current seed is a client — gates the influence-map action. */
  seedIsClient: boolean;
  fullGraphNodeLimit: number;

  depth: number;
  onDepthChange(d: number): void; // clamp 1–3

  onShowInfluenceMap(): void;
  onBackToPicker(): void;
  onShowFullGraph(): void;
  onBackToNeighborhood(): void;

  searchQuery: string;
  onSearchQueryChange(q: string): void;
  // Entity / relationship filters use the page's multi-select semantics:
  // an empty array means "all"; the Select serializes the array as a
  // comma-joined value and 'all' maps back to [].
  entityTypes: string[];
  entityTypeFilter: string[];
  onEntityTypeFilterChange(v: string[]): void;
  relationshipTypes: string[];
  relationshipTypeFilter: string[];
  onRelationshipTypeFilterChange(v: string[]): void;

  layers: GraphLayers;
  onLayersChange(l: GraphLayers): void;
}

/**
 * Graph toolbar (Task C2): a compact horizontal strip the page floats across
 * the top of the full-bleed canvas. Reads as one control bar — a mode chip
 * Popover (depth stepper + influence/full-graph/back escape hatches) on the
 * left, then the search box + entity/relationship selects + signals toggle.
 *
 * The page wraps this in the floating-surface div (OVERLAY_SURFACE +
 * rounded-lg); the toolbar itself is position-agnostic and chrome-free.
 * The picker body and truncation notice no longer live here — the page floats
 * SeedPickerOverlay over the whole canvas in picker mode, and the truncation
 * notice moved into the stats-chip popover.
 */
export function GraphToolbar({
  viewMode,
  seedDisplayName,
  seedId,
  seedIsClient,
  fullGraphNodeLimit,
  depth,
  onDepthChange,
  onShowInfluenceMap,
  onBackToPicker,
  onShowFullGraph,
  onBackToNeighborhood,
  searchQuery,
  onSearchQueryChange,
  entityTypes,
  entityTypeFilter,
  onEntityTypeFilterChange,
  relationshipTypes,
  relationshipTypeFilter,
  onRelationshipTypeFilterChange,
  layers,
  onLayersChange,
}: GraphToolbarProps) {
  const { getEntityDisplayName } = useDomain();

  // The seed chip's label mirrors the original mode-card status sentence so the
  // user keeps the same orientation at a glance.
  const chipLabel = (() => {
    switch (viewMode) {
      case 'picker':
        return 'Pick a starting entity';
      case 'neighborhood':
        return `Neighborhood · ${seedDisplayName ?? seedId ?? ''} · depth ${depth}`;
      case 'full':
        return `Full graph (capped at ${fullGraphNodeLimit})`;
      case 'influence':
        return `Influence map · ${seedDisplayName ?? seedId ?? ''}`;
    }
  })();

  return (
    <div className="flex flex-wrap items-center gap-2 px-2 py-1.5">
      {/* Mode chip — popover groups the depth stepper, influence-map /
          full-graph escape hatches, and re-pick / back actions. */}
      <Popover>
        <PopoverTrigger asChild>
          <Button size="sm" variant="outline" className="h-8 max-w-[260px] shrink-0">
            <span className="truncate">{chipLabel}</span>
          </Button>
        </PopoverTrigger>
        <PopoverContent align="start" className="w-64 space-y-3">
          {viewMode === 'neighborhood' && (
            <>
              <label className="flex items-center justify-between gap-1.5 text-xs text-muted-foreground">
                Depth
                <input
                  type="number"
                  min={1}
                  max={3}
                  value={depth}
                  onChange={(e) => onDepthChange(Math.max(1, Math.min(3, Number(e.target.value) || 2)))}
                  className="h-7 w-12 rounded border border-input bg-background px-1.5 text-sm"
                />
              </label>
              {seedIsClient && seedId && (
                <Button size="sm" variant="outline" className="w-full" onClick={onShowInfluenceMap}>
                  View as influence map
                </Button>
              )}
              <Button size="sm" variant="outline" className="w-full" onClick={onBackToPicker}>
                Pick a different seed
              </Button>
            </>
          )}
          {viewMode === 'picker' && (
            <Button size="sm" variant="outline" className="w-full" onClick={onShowFullGraph}>
              Show full graph
            </Button>
          )}
          {viewMode === 'full' && (
            <Button size="sm" variant="outline" className="w-full" onClick={onBackToPicker}>
              Back to picker
            </Button>
          )}
          {viewMode === 'influence' && (
            <>
              <Button size="sm" variant="outline" className="w-full" onClick={onBackToNeighborhood}>
                Back to neighborhood
              </Button>
              <Button size="sm" variant="outline" className="w-full" onClick={onBackToPicker}>
                Back to picker
              </Button>
            </>
          )}
        </PopoverContent>
      </Popover>

      {/* Thin divider between mode chip and filters */}
      <div className="hidden h-5 w-px bg-border sm:block" />

      {/* Search */}
      <div className="relative min-w-[160px] flex-1">
        <Search className="absolute left-2.5 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
        <Input
          placeholder="Search nodes…"
          value={searchQuery}
          onChange={(e) => onSearchQueryChange(e.target.value)}
          className="h-8 border-0 bg-transparent pl-8 shadow-none focus-visible:ring-1"
        />
      </div>

      <Select
        value={entityTypeFilter.length === 0 ? 'all' : entityTypeFilter.join(',')}
        onValueChange={(value) => onEntityTypeFilterChange(value === 'all' ? [] : value.split(','))}
      >
        <SelectTrigger className="h-8 w-[160px]">
          <SelectValue placeholder="All entity types" />
        </SelectTrigger>
        <SelectContent>
          <SelectItem value="all">All entity types</SelectItem>
          {entityTypes.map(type => (
            <SelectItem key={type} value={type}>{getEntityDisplayName(type, true)}</SelectItem>
          ))}
        </SelectContent>
      </Select>

      <Select
        value={relationshipTypeFilter.length === 0 ? 'all' : relationshipTypeFilter.join(',')}
        onValueChange={(value) => onRelationshipTypeFilterChange(value === 'all' ? [] : value.split(','))}
      >
        <SelectTrigger className="h-8 w-[160px]">
          <SelectValue placeholder="All relationships" />
        </SelectTrigger>
        <SelectContent>
          <SelectItem value="all">All relationships</SelectItem>
          {relationshipTypes.map(type => (
            <SelectItem key={type} value={type}>{type}</SelectItem>
          ))}
        </SelectContent>
      </Select>

      {/* Layer segmented control — progressively reveals signal nodes.
          'entities' is the default; the higher rungs add decisions and then
          all signal types. Wrapped in a tooltip preserving the spirit of the
          old checkbox label (signals dominate once ingestion is running). */}
      <TooltipProvider>
        <Tooltip>
          <TooltipTrigger asChild>
            <div
              role="radiogroup"
              aria-label="Graph layers"
              className="inline-flex shrink-0 items-center rounded-md border bg-background p-0.5"
            >
              {LAYER_OPTIONS.map((opt) => {
                const active = layers === opt.value;
                return (
                  <button
                    key={opt.value}
                    type="button"
                    role="radio"
                    aria-checked={active}
                    onClick={() => onLayersChange(opt.value)}
                    className={cn(
                      'h-7 whitespace-nowrap rounded px-2.5 text-xs font-medium transition-colors',
                      active
                        ? 'bg-primary text-primary-foreground'
                        : 'text-muted-foreground hover:bg-muted hover:text-foreground',
                    )}
                  >
                    {opt.label}
                  </button>
                );
              })}
            </div>
          </TooltipTrigger>
          <TooltipContent side="bottom" className="max-w-[240px]">
            Layer in decisions, then all signals (action items, key points).
            Kept off by default — signals dominate the view once meeting
            ingestion is active.
          </TooltipContent>
        </Tooltip>
      </TooltipProvider>
    </div>
  );
}
