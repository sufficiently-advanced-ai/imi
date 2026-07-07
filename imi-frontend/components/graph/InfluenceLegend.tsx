'use client';

import React from 'react';
import { useTheme } from 'next-themes';
import { Card, CardContent } from '@/components/ui/card';
import { STANCE_COLORS, STANCE_ORDER, STANCE_LABELS } from '@/lib/graph-constants';

/**
 * Compact legend for the domain graph's "Influence Map" view mode. Explains the
 * encodings that the EnhancedCytoscapeGraphV2 component applies in that mode:
 *   - node color  = stance (champion → blocker)
 *   - node size   = influence (high/medium/low)
 *   - solid edge  = reports to (formal org chart)
 *   - dashed edge = influences (informal power)
 *
 * Render it as an overlay near the graph canvas; it is intentionally
 * self-contained so the page only needs to conditionally mount it.
 */
export function InfluenceLegend() {
  const { resolvedTheme } = useTheme();
  const isDark = resolvedTheme === 'dark';
  // Mirror the accent used for informal edges in buildStylesheet().
  const accent = isDark ? '#a78bfa' : '#7c3aed';
  const edgeBase = isDark ? '#8c8599' : '#5a5366';

  return (
    <Card className="pointer-events-none w-56 bg-background/90 shadow-sm backdrop-blur">
      <CardContent className="space-y-3 p-3 text-xs">
        <div>
          <div className="mb-1.5 font-medium text-foreground">Stance</div>
          <div className="space-y-1">
            {STANCE_ORDER.map((stance) => (
              <div key={stance} className="flex items-center gap-2">
                <span
                  className="inline-block h-3 w-3 shrink-0 rounded-full"
                  style={{ backgroundColor: STANCE_COLORS[stance] }}
                />
                <span className="text-muted-foreground">{STANCE_LABELS[stance]}</span>
              </div>
            ))}
          </div>
        </div>

        <div>
          <div className="mb-1.5 font-medium text-foreground">Size</div>
          <div className="flex items-center gap-2 text-muted-foreground">
            <span className="inline-block h-2 w-2 shrink-0 rounded-full bg-muted-foreground" />
            <span className="inline-block h-3.5 w-3.5 shrink-0 rounded-full bg-muted-foreground" />
            <span>influence (low → high)</span>
          </div>
        </div>

        <div>
          <div className="mb-1.5 font-medium text-foreground">Edges</div>
          <div className="space-y-1.5 text-muted-foreground">
            <div className="flex items-center gap-2">
              <svg width="28" height="8" viewBox="0 0 28 8" className="shrink-0">
                <line x1="0" y1="4" x2="28" y2="4" stroke={edgeBase} strokeWidth="2" />
              </svg>
              <span>reports to</span>
            </div>
            <div className="flex items-center gap-2">
              <svg width="28" height="8" viewBox="0 0 28 8" className="shrink-0">
                <line
                  x1="0"
                  y1="4"
                  x2="28"
                  y2="4"
                  stroke={accent}
                  strokeWidth="2"
                  strokeDasharray="6 4"
                />
              </svg>
              <span>influences</span>
            </div>
          </div>
        </div>
      </CardContent>
    </Card>
  );
}
