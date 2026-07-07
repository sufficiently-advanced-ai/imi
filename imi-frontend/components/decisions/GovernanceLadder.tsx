"use client";

import React from "react";
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { cn } from "@/lib/utils";
import type { GovernanceLadder as GovernanceLadderType } from "@/lib/api/decisions";

interface GovernanceLadderProps {
  /** Full governance ladder from DecisionDetail */
  ladder?: GovernanceLadderType;
  /** Shorthand flags when ladder isn't available (list view) */
  canUseAsEvidence?: boolean;
  canUseAsInstruction?: boolean;
  /** Additional class names */
  className?: string;
}

type Step = {
  label: string;
  key: "captured" | "confirmed" | "instruction";
  tooltip: string;
};

const STEPS: Step[] = [
  {
    label: "Captured",
    key: "captured",
    tooltip: "Decision captured from a meeting",
  },
  {
    label: "Evidence",
    key: "confirmed",
    tooltip: "Verified — usable as supporting evidence",
  },
  {
    label: "Instruction",
    key: "instruction",
    tooltip: "Fully governed — usable as an operating instruction",
  },
];

/**
 * Determine which step is currently reached.
 * Returns 0 (captured only), 1 (evidence-grade), or 2 (instruction-grade).
 */
function getReachedStep(
  position?: GovernanceLadderType["position"],
  canUseAsEvidence?: boolean,
  canUseAsInstruction?: boolean,
): number {
  if (position === "instruction") return 2;
  if (position === "evidence") return 1;
  if (position === "blocked") return -1; // all muted

  // Fallback: derive from flags
  if (canUseAsInstruction) return 2;
  if (canUseAsEvidence) return 1;
  return 0;
}

/**
 * Compact 3-step governance ladder indicator.
 * Dots + labels, with Radix Tooltip on hover showing provenance/review status.
 */
export function GovernanceLadder({
  ladder,
  canUseAsEvidence,
  canUseAsInstruction,
  className,
}: GovernanceLadderProps) {
  const position = ladder?.position;
  const blocked = position === "blocked";
  const reachedStep = getReachedStep(
    position,
    ladder?.can_use_as_evidence ?? canUseAsEvidence,
    ladder?.can_use_as_instruction ?? canUseAsInstruction,
  );

  const tooltipLines: string[] = [];
  if (ladder?.provenance_status) {
    tooltipLines.push(`Provenance: ${ladder.provenance_status}`);
  }
  if (ladder?.review_status) {
    tooltipLines.push(`Review: ${ladder.review_status}`);
  }
  if (blocked) {
    tooltipLines.push("Blocked from governance use");
  }
  const tooltipText = tooltipLines.length > 0 ? tooltipLines.join(" · ") : null;

  const indicator = (
    <div className={cn("flex items-center gap-1.5", className)}>
      {STEPS.map((step, i) => {
        const reached = !blocked && i <= reachedStep;
        const isCurrent = !blocked && i === reachedStep;

        return (
          <React.Fragment key={step.key}>
            {/* Step dot */}
            <div
              className={cn(
                "h-2 w-2 rounded-full transition-colors duration-150",
                blocked
                  ? "bg-destructive/30"
                  : reached
                    ? isCurrent
                      ? "bg-primary"
                      : "bg-primary/50"
                    : "bg-muted-foreground/25",
              )}
              aria-label={step.label}
            />
            {/* Connector line (not after last) */}
            {i < STEPS.length - 1 && (
              <div
                className={cn(
                  "h-px w-4 transition-colors duration-150",
                  blocked
                    ? "bg-destructive/20"
                    : reached && i < reachedStep
                      ? "bg-primary/40"
                      : "bg-muted-foreground/20",
                )}
              />
            )}
          </React.Fragment>
        );
      })}

      {/* Step label for current position */}
      <span
        className={cn(
          "ml-1 text-[10px] font-medium leading-none",
          blocked
            ? "text-destructive/60"
            : reachedStep >= 0
              ? "text-muted-foreground"
              : "text-muted-foreground/50",
        )}
      >
        {blocked
          ? "blocked"
          : reachedStep >= 0
            ? STEPS[reachedStep]?.label ?? ""
            : "Captured"}
      </span>
    </div>
  );

  if (!tooltipText) return indicator;

  return (
    <TooltipProvider>
      <Tooltip>
        <TooltipTrigger asChild>
          <div className="cursor-default">{indicator}</div>
        </TooltipTrigger>
        <TooltipContent side="top" className="max-w-xs text-xs">
          {tooltipText}
        </TooltipContent>
      </Tooltip>
    </TooltipProvider>
  );
}
