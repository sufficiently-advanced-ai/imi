"use client";

import React, { useState } from "react";
import { Button } from "@/components/ui/button";
import { RefreshCw, Download, CheckCircle2 } from "lucide-react";
import {
  exportConstitution,
  getConstitutionDownloadUrl,
} from "@/lib/api/decisions";

// ---- Export button with feedback ----

export function ExportConstitutionButton() {
  const [state, setState] = useState<"idle" | "loading" | "success" | "error">(
    "idle",
  );
  const [errorMsg, setErrorMsg] = useState<string | null>(null);

  const handleExport = async () => {
    setState("loading");
    setErrorMsg(null);
    try {
      await exportConstitution();
      setState("success");
      // Open the download — use getConstitutionDownloadUrl() so subpath deployments work
      window.open(getConstitutionDownloadUrl(), "_blank");
      // Reset after a few seconds
      setTimeout(() => setState("idle"), 4000);
    } catch (err) {
      const msg =
        err instanceof Error ? err.message : "Export failed";
      setErrorMsg(msg);
      setState("error");
      setTimeout(() => setState("idle"), 4000);
    }
  };

  return (
    <div className="flex items-center gap-2">
      <Button
        onClick={handleExport}
        variant="outline"
        size="sm"
        disabled={state === "loading"}
        className="gap-1.5"
      >
        {state === "loading" ? (
          <RefreshCw className="h-3.5 w-3.5 animate-spin" />
        ) : state === "success" ? (
          <CheckCircle2 className="h-3.5 w-3.5 text-green-600" />
        ) : (
          <Download className="h-3.5 w-3.5" />
        )}
        {state === "success"
          ? "Exported"
          : state === "error"
            ? "Failed"
            : "Export Constitution"}
      </Button>
      {state === "error" && errorMsg && (
        <span className="text-xs text-destructive max-w-[200px] truncate">
          {errorMsg}
        </span>
      )}
    </div>
  );
}
