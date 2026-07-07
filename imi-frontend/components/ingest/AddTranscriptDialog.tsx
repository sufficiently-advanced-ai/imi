"use client";

import React, { useCallback, useRef, useState } from "react";
import { Plus } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  Sheet,
  SheetContent,
  SheetHeader,
  SheetTitle,
  SheetTrigger,
} from "@/components/ui/sheet";
import { IngestTranscriptFlow } from "./IngestTranscriptFlow";
import type { DeltaReport } from "@/lib/api/ingest";

// ---------------------------------------------------------------------------
// AddTranscriptDialog
//
// A right-side Sheet wrapping IngestTranscriptFlow.
//
// Radix Dialog (and therefore Sheet) unmounts content on close by default.
// That is intentional here: once the sheet closes the progress view resets,
// but the ingest JOB itself keeps running on the server.  forceMount is NOT
// used because Radix's DismissableLayer sets document.body.style.pointerEvents
// = 'none' while a modal is mounted — with forceMount and a closed sheet the
// body pointer-events lock is never released, making the whole app unclickable.
//
// Accidental-close guard: IngestTranscriptFlow signals busy state via
// onBusyChange.  While busy, onOpenChange intercepts a close attempt and
// prompts the user before closing.
// ---------------------------------------------------------------------------

export interface AddTranscriptDialogProps {
  /** Override the default trigger button. */
  trigger?: React.ReactNode;
  /** Called with the delta report once ingestion completes. */
  onComplete?: (report: DeltaReport) => void;
}

export function AddTranscriptDialog({ trigger, onComplete }: AddTranscriptDialogProps) {
  const [open, setOpen] = useState(false);
  const busyRef = useRef(false);

  const handleBusyChange = useCallback((busy: boolean) => {
    busyRef.current = busy;
  }, []);

  const handleOpenChange = useCallback((next: boolean) => {
    if (!next && busyRef.current) {
      const confirmed = window.confirm(
        "A transcript is still processing. The job will keep running on the server, but progress display will reset. Close anyway?"
      );
      if (!confirmed) return;
    }
    setOpen(next);
  }, []);

  const defaultTrigger = (
    <Button size="sm" variant="outline">
      <Plus className="h-4 w-4 mr-2" />
      Add transcript
    </Button>
  );

  return (
    <Sheet open={open} onOpenChange={handleOpenChange}>
      <SheetTrigger asChild>
        {trigger ?? defaultTrigger}
      </SheetTrigger>
      <SheetContent
        side="right"
        className="w-full sm:max-w-xl overflow-y-auto"
      >
        <SheetHeader>
          <SheetTitle>Add transcript</SheetTitle>
        </SheetHeader>
        <div className="px-4 pb-6">
          <IngestTranscriptFlow
            onComplete={onComplete}
            onBusyChange={handleBusyChange}
          />
        </div>
      </SheetContent>
    </Sheet>
  );
}
