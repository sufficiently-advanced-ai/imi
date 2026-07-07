import React from "react";
import { Badge } from "@/components/ui/badge";
import { User, Clock } from "lucide-react";
import {
  decisionStateBadgeVariant,
  type Decision,
} from "@/lib/api/decisions";

// ---- helpers ----

function formatDate(ts: string | null): string {
  if (!ts) return "";
  try {
    return new Date(ts).toLocaleDateString("en-US", {
      month: "short",
      day: "numeric",
      year: "numeric",
    });
  } catch {
    return "";
  }
}

// ---- component ----

interface DecisionRowProps {
  decision: Decision;
  onClick(): void;
}

export function DecisionRow({ decision, onClick }: DecisionRowProps) {
  return (
    <div
      className="py-3 border-b border-border/40 last:border-b-0 hover:bg-accent/20 transition-colors duration-150 -mx-4 px-4 rounded-sm cursor-pointer"
      role="button"
      tabIndex={0}
      onClick={onClick}
      onKeyDown={(e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          onClick();
        }
      }}
    >
      <div className="flex items-center gap-2 min-w-0">
        {/* State badge */}
        <Badge
          variant={decisionStateBadgeVariant(decision.state)}
          className="text-[10px] px-1.5 py-0 flex-shrink-0"
        >
          {decision.state.toUpperCase()}
        </Badge>

        {/* Content — one-liner truncate */}
        <p className="text-sm text-foreground leading-snug truncate flex-1 min-w-0">
          {decision.content}
        </p>
      </div>

      {/* Meta row */}
      <div className="flex items-center gap-3 mt-1 text-xs text-muted-foreground flex-wrap">
        {decision.owner && (
          <span className="flex items-center gap-1">
            <User className="h-3 w-3 flex-shrink-0" />
            {decision.owner}
          </span>
        )}
        {decision.source_meeting_title && (
          <span className="truncate max-w-[240px]">
            {decision.source_meeting_title}
          </span>
        )}
        {decision.source_timestamp && (
          <span className="flex items-center gap-0.5">
            <Clock className="h-3 w-3 flex-shrink-0" />
            {formatDate(decision.source_timestamp)}
          </span>
        )}
      </div>
    </div>
  );
}
