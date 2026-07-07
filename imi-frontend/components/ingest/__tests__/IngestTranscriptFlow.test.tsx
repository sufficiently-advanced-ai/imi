import React from "react";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { IngestTranscriptFlow } from "../IngestTranscriptFlow";

// ---------------------------------------------------------------------------
// Mocks
// ---------------------------------------------------------------------------

jest.mock("@/lib/api/ingest", () => ({
  submitIngest: jest.fn(),
  fetchDelta: jest.fn(),
}));

jest.mock("@/components/IngestProgress", () => ({
  IngestProgress: jest.fn(({ onDeltaReady, onFailed }: { jobId: string; onDeltaReady?: () => void; onFailed?: (e: string) => void }) => (
    <div data-testid="ingest-progress">
      <button onClick={onDeltaReady} data-testid="trigger-delta-ready">
        Trigger delta ready
      </button>
      <button onClick={() => onFailed?.("pipeline error")} data-testid="trigger-failed">
        Trigger failed
      </button>
    </div>
  )),
}));

jest.mock("@/components/ui/use-toast", () => ({
  useToast: () => ({ toast: jest.fn() }),
}));

import { submitIngest, fetchDelta } from "@/lib/api/ingest";

const MINIMAL_DELTA_REPORT = {
  job_id: "job-001",
  bot_id: "bot-001",
  meeting_title: "Test meeting",
  generated_at: new Date().toISOString(),
  new_decisions: [],
  proposed_supersessions: [],
  potential_conflicts: [],
  commitments_opened: [],
  commitments_closed: [],
  entities_touched: [],
  counts: {},
};

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe("IngestTranscriptFlow", () => {
  beforeEach(() => {
    jest.clearAllMocks();
  });

  test("disables submit on empty content", () => {
    render(<IngestTranscriptFlow />);
    const submitBtn = screen.getByRole("button", { name: /ingest transcript/i });
    expect(submitBtn).toBeDisabled();
  });

  test("enables submit when content is entered", async () => {
    const user = userEvent.setup();
    render(<IngestTranscriptFlow />);

    const textarea = screen.getByLabelText(/transcript/i);
    await user.type(textarea, "Hello world transcript");

    const submitBtn = screen.getByRole("button", { name: /ingest transcript/i });
    expect(submitBtn).not.toBeDisabled();
  });

  test("submits and shows progress", async () => {
    const user = userEvent.setup();
    (submitIngest as jest.Mock).mockResolvedValue({
      job_id: "job-001",
      status: "accepted",
      poll_url: "/api/ingest/job-001/status",
    });

    render(<IngestTranscriptFlow />);

    const textarea = screen.getByLabelText(/transcript/i);
    await user.type(textarea, "Some meeting transcript content");

    const submitBtn = screen.getByRole("button", { name: /ingest transcript/i });
    await user.click(submitBtn);

    expect(submitIngest).toHaveBeenCalledWith({
      content: "Some meeting transcript content",
      title: undefined,
      source: "transcript",
    });

    await waitFor(() => {
      expect(screen.getByTestId("ingest-progress")).toBeInTheDocument();
    });
  });

  test("calls onComplete when delta is ready", async () => {
    const user = userEvent.setup();
    const onComplete = jest.fn();

    (submitIngest as jest.Mock).mockResolvedValue({
      job_id: "job-001",
      status: "accepted",
      poll_url: "/api/ingest/job-001/status",
    });
    (fetchDelta as jest.Mock).mockResolvedValue(MINIMAL_DELTA_REPORT);

    render(<IngestTranscriptFlow onComplete={onComplete} />);

    // Enter content and submit
    await user.type(screen.getByLabelText(/transcript/i), "Transcript text here");
    await user.click(screen.getByRole("button", { name: /ingest transcript/i }));

    // Wait for IngestProgress to mount
    await waitFor(() => {
      expect(screen.getByTestId("ingest-progress")).toBeInTheDocument();
    });

    // Drive the mocked IngestProgress's onDeltaReady callback
    await user.click(screen.getByTestId("trigger-delta-ready"));

    await waitFor(() => {
      expect(fetchDelta).toHaveBeenCalledWith("job-001");
      expect(onComplete).toHaveBeenCalledWith(MINIMAL_DELTA_REPORT);
    });
  });

  test("onBusyChange fires true on submit and false when delta is ready", async () => {
    const user = userEvent.setup();
    const onBusyChange = jest.fn();

    (submitIngest as jest.Mock).mockResolvedValue({
      job_id: "job-002",
      status: "accepted",
      poll_url: "/api/ingest/job-002/status",
    });
    (fetchDelta as jest.Mock).mockResolvedValue(MINIMAL_DELTA_REPORT);

    render(<IngestTranscriptFlow onBusyChange={onBusyChange} />);

    // Submit
    await user.type(screen.getByLabelText(/transcript/i), "Some transcript content");
    await user.click(screen.getByRole("button", { name: /ingest transcript/i }));

    // busy=true should fire once the job id is set
    await waitFor(() => {
      expect(onBusyChange).toHaveBeenCalledWith(true);
    });

    // Wait for IngestProgress to mount
    await waitFor(() => {
      expect(screen.getByTestId("ingest-progress")).toBeInTheDocument();
    });

    // Drive delta ready
    await user.click(screen.getByTestId("trigger-delta-ready"));

    // busy=false should fire when delta is received
    await waitFor(() => {
      expect(onBusyChange).toHaveBeenCalledWith(false);
    });

    // Verify call order: true then false
    const calls = onBusyChange.mock.calls.map(([v]) => v);
    expect(calls).toEqual([true, false]);
  });

  test("onBusyChange fires false when ingestion fails", async () => {
    const user = userEvent.setup();
    const onBusyChange = jest.fn();

    (submitIngest as jest.Mock).mockResolvedValue({
      job_id: "job-003",
      status: "accepted",
      poll_url: "/api/ingest/job-003/status",
    });

    render(<IngestTranscriptFlow onBusyChange={onBusyChange} />);

    await user.type(screen.getByLabelText(/transcript/i), "Some transcript content");
    await user.click(screen.getByRole("button", { name: /ingest transcript/i }));

    await waitFor(() => {
      expect(onBusyChange).toHaveBeenCalledWith(true);
    });

    await waitFor(() => {
      expect(screen.getByTestId("ingest-progress")).toBeInTheDocument();
    });

    // Drive failure
    await user.click(screen.getByTestId("trigger-failed"));

    await waitFor(() => {
      expect(onBusyChange).toHaveBeenCalledWith(false);
    });
  });
});
