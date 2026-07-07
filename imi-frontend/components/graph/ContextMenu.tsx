'use client';

// Positioned context menu for right-click on graph nodes/edges.
// Renders a small action list anchored at `position` (viewport coords).
// Dismisses on outside click, Escape, or scroll.

import { useEffect, useRef } from 'react';
import { cn } from '@/lib/utils';

export interface ContextMenuItem {
  label: string;
  onSelect: () => void;
  // Visual variant for destructive actions (red text).
  destructive?: boolean;
  // Disabled items are rendered but not clickable.
  disabled?: boolean;
  // Optional icon element (Lucide etc.).
  icon?: React.ReactNode;
}

interface ContextMenuProps {
  position: { x: number; y: number } | null;
  items: ContextMenuItem[];
  onDismiss: () => void;
}

export function ContextMenu({ position, items, onDismiss }: ContextMenuProps) {
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!position) return;
    const handleKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onDismiss();
    };
    const handleClick = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) {
        onDismiss();
      }
    };
    const handleScroll = () => onDismiss();

    window.addEventListener('keydown', handleKey);
    // Use capture so a click anywhere dismisses before the target handles it.
    window.addEventListener('mousedown', handleClick, true);
    window.addEventListener('scroll', handleScroll, true);
    return () => {
      window.removeEventListener('keydown', handleKey);
      window.removeEventListener('mousedown', handleClick, true);
      window.removeEventListener('scroll', handleScroll, true);
    };
  }, [position, onDismiss]);

  if (!position) return null;

  // Clamp to viewport so menu doesn't get clipped at the edges.
  const maxX = typeof window !== 'undefined' ? window.innerWidth - 180 : 1000;
  const maxY = typeof window !== 'undefined' ? window.innerHeight - 200 : 800;
  const left = Math.min(position.x, maxX);
  const top = Math.min(position.y, maxY);

  return (
    <div
      ref={ref}
      role="menu"
      className="fixed z-50 min-w-[180px] rounded-md border bg-popover p-1 text-sm shadow-md"
      style={{ left, top }}
    >
      {items.map((item, idx) => (
        <button
          key={idx}
          type="button"
          role="menuitem"
          disabled={item.disabled}
          onClick={() => {
            if (item.disabled) return;
            item.onSelect();
            onDismiss();
          }}
          className={cn(
            'flex w-full items-center gap-2 rounded-sm px-2 py-1.5 text-left outline-none',
            'hover:bg-accent focus-visible:bg-accent',
            'disabled:pointer-events-none disabled:opacity-50',
            item.destructive && 'text-destructive hover:text-destructive',
          )}
        >
          {item.icon && <span className="h-4 w-4 shrink-0">{item.icon}</span>}
          <span className="truncate">{item.label}</span>
        </button>
      ))}
    </div>
  );
}
