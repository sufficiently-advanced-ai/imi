import React from "react";
import { act, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { IngestProgress } from "../IngestProgress";
import type { IngestSSEEvent, PhaseOutcome } from "@/lib/api/ingest";

let emit: (event: IngestSSEEvent) => void = () => {};

jest.mock("@/lib/api/ingest", () => ({
  fetchIngestStatus: jest.fn(),
  createIngestEventSource: jest.fn(
    (_jobId: string, onEvent: (event: IngestSSEEvent) => void) => {
      emit = onEvent;
      return { close: jest.fn() };
    },
  ),
}));

const FAILED: PhaseOutcome = {
  phase: "PROMOTE_SIGNALS",
  outcome: "failed",
  detail: "signal promotion: RuntimeError: LLM overloaded",
  retryable: true,
};
const OK: PhaseOutcome = { phase: "CLASSIFY", outcome: "ok", detail: null, retryable: false };
const WARNING = `PROMOTE_SIGNALS: ${FAILED.detail}`;

describe("IngestProgress", () => {
  test("a clean run shows no warning state", () => {
    render(<IngestProgress jobId="job-ok" />);
    act(() => {
      emit({
        type: "ingest_complete",
        result: {},
        status: "completed",
        phase_outcomes: [OK],
        warnings: [],
        timestamp: "t",
      });
    });
    expect(screen.queryByTestId("ingest-warnings")).not.toBeInTheDocument();
  });

  test("completed_with_warnings shows a warning with per-phase details on expand", async () => {
    const user = userEvent.setup();
    render(<IngestProgress jobId="job-warn" />);

    act(() => {
      emit({
        type: "ingest_phase",
        phase: "PROMOTE_SIGNALS",
        status: "completed",
        phases_completed: ["CLASSIFY", "PROMOTE_SIGNALS"],
        outcome: "failed",
        detail: FAILED.detail,
        retryable: true,
        phase_outcomes: [OK, FAILED],
        warnings: [WARNING],
        timestamp: "t",
      });
    });
    act(() => {
      emit({
        type: "ingest_complete",
        result: {},
        status: "completed_with_warnings",
        phase_outcomes: [OK, FAILED],
        warnings: [WARNING],
        timestamp: "t",
      });
    });

    expect(screen.getByLabelText("Completed with a warning")).toBeInTheDocument();
    const toggle = screen.getByRole("button", { name: /completed with warnings \(1\)/i });
    expect(screen.queryByText(/LLM overloaded/)).not.toBeInTheDocument();

    await user.click(toggle);
    expect(screen.getByText(/LLM overloaded/)).toBeInTheDocument();
    expect(screen.getByText(/retryable/)).toBeInTheDocument();
  });
});
