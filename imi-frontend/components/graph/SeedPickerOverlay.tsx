'use client';

import React, { useEffect, useRef, useState } from 'react';
import { Input } from '@/components/ui/input';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { searchEntities, type EntitySearchResult, type TopEntity } from '@/lib/api/domain';

// Debounce window for remote seed search — long enough to coalesce a burst of
// keystrokes into one request, short enough to feel responsive.
const SEARCH_DEBOUNCE_MS = 250;
const SEARCH_MAX_RESULTS = 12;

interface SeedPickerOverlayProps {
  topEntities: TopEntity[];
  onSelectSeed(id: string): void;
  /**
   * Escape hatch to the full graph. Rendered as a button inside the picker
   * card (moved here from the toolbar in C2) so the full-graph affordance lives
   * with the rest of the picker controls.
   */
  onShowFullGraph(): void;
  /** Unused in C2; wired for server-side seed search in C3. */
  domain: string | null;
  /** Max nodes the full-graph view loads — surfaced in the escape-hatch copy. */
  fullGraphNodeLimit?: number;
}

/**
 * Picker-mode overlay (Task C2): a centered card the page floats over the whole
 * full-bleed canvas (`absolute inset-0 grid place-items-center`). Free-text
 * seed search + top-entity chips + a "Show full graph" escape hatch. Owns its
 * own search-query state. Enter resolves the typed text to the best matching
 * top-entity id (or falls back to the raw text as an id); chips filter live by
 * the same query.
 */
export function SeedPickerOverlay({
  topEntities,
  onSelectSeed,
  onShowFullGraph,
  domain,
  fullGraphNodeLimit,
}: SeedPickerOverlayProps) {
  const [seedSearchQuery, setSeedSearchQuery] = useState('');
  const [remoteResults, setRemoteResults] = useState<EntitySearchResult[]>([]);
  // Monotonic request id — lets late responses for stale queries be ignored so
  // out-of-order completions can't clobber fresher results.
  const requestIdRef = useRef(0);

  // Debounced remote seed search. Fires 250ms after the query settles; empty
  // query clears results without a request. Failures fall back silently to the
  // local chip filtering below (no error UI).
  useEffect(() => {
    const query = seedSearchQuery.trim();
    if (!query) {
      // Invalidate any in-flight request too — otherwise a slow response for
      // the previous query could repopulate results after the input cleared.
      requestIdRef.current++;
      setRemoteResults([]);
      return;
    }

    const reqId = ++requestIdRef.current;
    const handle = setTimeout(async () => {
      try {
        const results = await searchEntities({
          query,
          maxResults: SEARCH_MAX_RESULTS,
          domain: domain ?? undefined,
        });
        // Ignore stale responses (a newer query has since fired).
        if (reqId !== requestIdRef.current) return;
        setRemoteResults(results);
      } catch {
        if (reqId !== requestIdRef.current) return;
        // Silent fallback — the chip heuristic still works.
        setRemoteResults([]);
      }
    }, SEARCH_DEBOUNCE_MS);

    return () => clearTimeout(handle);
  }, [seedSearchQuery, domain]);

  return (
    <Card className="w-full max-w-xl shadow-lg">
      <CardHeader className="pb-3">
        <CardTitle className="text-base">Pick a starting entity</CardTitle>
        <p className="text-sm text-muted-foreground">
          Explore the graph around a single entity, or load the full graph.
        </p>
      </CardHeader>
      <CardContent className="space-y-3">
        <Input
          placeholder="Type an entity name or ID to use as seed…"
          value={seedSearchQuery}
          onChange={(e) => setSeedSearchQuery(e.target.value)}
          onKeyDown={(e) => {
            if (e.key !== 'Enter') return;
            const raw = seedSearchQuery.trim();
            if (!raw) return;
            // Prefer the first remote search hit when present — it resolves
            // to a real entity ID the /neighborhood endpoint can expand.
            if (remoteResults.length > 0) {
              onSelectSeed(remoteResults[0].id);
              return;
            }
            // No remote hits: fall back to the local chip heuristic. The
            // /neighborhood endpoint looks up by `seed` ID, so a free-text
            // name won't resolve. Prefer the first suggestion that matches
            // the query (case-insensitive) and fall back to the raw text
            // only if the user really typed an ID not in the picker list.
            const lower = raw.toLowerCase();
            const match =
              topEntities.find(ent => ent.id.toLowerCase() === lower) ??
              topEntities.find(ent => ent.name.toLowerCase() === lower) ??
              topEntities.find(ent => ent.name.toLowerCase().includes(lower));
            onSelectSeed(match ? match.id : raw);
          }}
        />
        {remoteResults.length > 0 && (
          <div>
            <div className="mb-1.5 text-xs text-muted-foreground">
              Search results:
            </div>
            <div className="flex max-h-48 flex-col gap-1 overflow-y-auto">
              {remoteResults.map((r) => (
                <button
                  key={r.id}
                  type="button"
                  onClick={() => onSelectSeed(r.id)}
                  className="flex items-center justify-between gap-2 rounded-md px-2.5 py-1.5 text-left text-sm hover:bg-muted focus-visible:bg-muted focus-visible:outline-none"
                  aria-label={`Use ${r.name} (${r.type}) as seed`}
                >
                  <span className="truncate font-medium">{r.name}</span>
                  <span className="shrink-0 text-xs text-muted-foreground">{r.type}</span>
                </button>
              ))}
            </div>
          </div>
        )}
        {topEntities.length > 0 && (
          <div>
            <div className="mb-1.5 text-xs text-muted-foreground">
              Or pick from the {topEntities.length} most-connected entities:
            </div>
            <div className="flex max-h-48 flex-wrap gap-1.5 overflow-y-auto">
              {topEntities
                .filter(e => !seedSearchQuery || e.name.toLowerCase().includes(seedSearchQuery.toLowerCase()))
                .map((e) => (
                  <Button
                    key={e.id}
                    size="sm"
                    variant="secondary"
                    onClick={() => onSelectSeed(e.id)}
                    className="h-7 text-xs font-normal"
                    title={`${e.type} · degree ${e.degree}`}
                    aria-label={`Use ${e.name} (${e.type}, ${e.degree} connections) as seed`}
                  >
                    {e.name} <span className="ml-1 opacity-60">·{e.degree}</span>
                  </Button>
                ))}
            </div>
          </div>
        )}
        <div className="border-t pt-3">
          <Button variant="outline" size="sm" className="w-full" onClick={onShowFullGraph}>
            Show full graph
            {typeof fullGraphNodeLimit === 'number' && (
              <span className="ml-1 text-muted-foreground">
                (slower · capped at {fullGraphNodeLimit})
              </span>
            )}
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}
