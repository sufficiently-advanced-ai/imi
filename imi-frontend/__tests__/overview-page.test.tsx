/**
 * Tests for the Overview home page (PR-D, Task D4).
 *
 * Covers:
 *  - header description from decision stats headline
 *  - active decisions rendered as rows; row click opens detail dialog
 *  - review card links to /decisions?filter=review and shows the count
 *  - latest intake shows newest job + delta counts
 *  - digest card renders markdown; "No digest yet." on null
 *  - cards degrade independently (one fetch rejecting doesn't blank others)
 */

import React from "react";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";

import OverviewPage from "@/app/(protected)/overview/page";

import {
  fetchDecisions,
  fetchDecisionStats,
} from "@/lib/api/decisions";
import { fetchIngestJobs, fetchDelta } from "@/lib/api/ingest";
import {
  fetchLatestWeeklyDigest,
} from "@/lib/api/digest";
import { useReviewCounts } from "@/lib/hooks/useReviewCounts";
import { useDomain } from "@/contexts/DomainContext";

// ---- mocks ----

jest.mock("@/lib/api/decisions", () => ({
  fetchDecisions: jest.fn(),
  fetchDecisionStats: jest.fn(),
  // DecisionRow imports this from the same module.
  decisionStateBadgeVariant: jest.fn(() => "success"),
}));

jest.mock("@/lib/api/ingest", () => ({
  fetchIngestJobs: jest.fn(),
  fetchDelta: jest.fn(),
}));

jest.mock("@/lib/api/digest", () => ({
  fetchLatestWeeklyDigest: jest.fn(),
}));

jest.mock("@/lib/hooks/useReviewCounts", () => ({
  useReviewCounts: jest.fn(),
}));

jest.mock("@/contexts/DomainContext", () => ({
  useDomain: jest.fn(),
}));

// Heavy child components — mock minimally.
jest.mock("@/components/ingest/AddTranscriptDialog", () => ({
  AddTranscriptDialog: () => (
    <div data-testid="add-transcript-dialog">Add transcript</div>
  ),
}));

jest.mock("@/components/decisions/ExportConstitutionButton", () => ({
  ExportConstitutionButton: () => (
    <div data-testid="export-constitution">Export</div>
  ),
}));

jest.mock("@/components/DeltaReportCard", () => ({
  DeltaReportCard: () => <div data-testid="delta-report-card">Delta</div>,
}));

jest.mock("@/components/MarkdownViewer", () => ({
  __esModule: true,
  default: ({ content }: { content: string }) => (
    <div data-testid="markdown-viewer">{content}</div>
  ),
}));

jest.mock("@/components/decisions/DecisionDetailDialog", () => ({
  DecisionDetailDialog: ({
    decisionId,
    open,
  }: {
    decisionId: string | null;
    open: boolean;
  }) => (
    <div
      data-testid="decision-detail-dialog"
      data-decision-id={decisionId ?? ""}
      data-open={open ? "true" : "false"}
    />
  ),
}));

// ---- typed mock helpers ----

const mockFetchDecisions = fetchDecisions as jest.Mock;
const mockFetchDecisionStats = fetchDecisionStats as jest.Mock;
const mockFetchIngestJobs = fetchIngestJobs as jest.Mock;
const mockFetchDelta = fetchDelta as jest.Mock;
const mockFetchLatestWeeklyDigest = fetchLatestWeeklyDigest as jest.Mock;
const mockUseReviewCounts = useReviewCounts as jest.Mock;
const mockUseDomain = useDomain as jest.Mock;

// ---- fixtures ----

function makeDecision(id: string, content: string) {
  return {
    id,
    content,
    state: "active",
    state_reason: null,
    age_days: 1,
    review_status: null,
    provenance_status: null,
    can_use_as_evidence: true,
    can_use_as_instruction: true,
    owner: "Alice",
    owner_id: null,
    client_id: null,
    source_meeting_id: null,
    source_meeting_title: "Kickoff",
    source_timestamp: "2026-06-01T00:00:00Z",
    superseded_by: null,
    tenant_id: null,
    metadata: {},
  };
}

const STATS = {
  meetings: 4,
  decisions: 12,
  counts_by_state: { active: 6 },
  stale: 1,
  superseded: 2,
  headline: "12 decisions across 4 meetings",
};

function setHappyDefaults() {
  mockFetchDecisionStats.mockResolvedValue(STATS);
  mockFetchDecisions.mockResolvedValue({
    decisions: [
      makeDecision("d1", "Use Postgres"),
      makeDecision("d2", "Ship weekly"),
      makeDecision("d3", "No on-call rotation"),
    ],
    total: 3,
    counts_by_state: { active: 3 },
  });
  mockFetchIngestJobs.mockResolvedValue([
    {
      job_id: "job-old",
      status: "completed",
      created_at: "2026-06-01T00:00:00Z",
      title: "Old meeting",
    },
    {
      job_id: "job-new",
      status: "completed",
      created_at: "2026-06-10T00:00:00Z",
      title: "Newest meeting",
    },
  ]);
  mockFetchDelta.mockResolvedValue({
    job_id: "job-new",
    bot_id: "b",
    meeting_title: "Newest meeting",
    generated_at: "2026-06-10T00:00:00Z",
    new_decisions: [{ signal_id: "s1" }, { signal_id: "s2" }],
    proposed_supersessions: [{ new_signal_id: "s3" }],
    potential_conflicts: [],
    commitments_opened: [],
    commitments_closed: [],
    entities_touched: [],
    counts: {},
  });
  mockFetchLatestWeeklyDigest.mockResolvedValue("## This week\n- Did stuff");
  mockUseReviewCounts.mockReturnValue({ count: 4, refresh: jest.fn() });
}

beforeEach(() => {
  jest.clearAllMocks();
  setHappyDefaults();
});

describe("Overview page", () => {
  it("renders the stats headline as the header description", async () => {
    render(<OverviewPage />);
    expect(
      await screen.findByText("12 decisions across 4 meetings"),
    ).toBeInTheDocument();
  });

  it("renders active decisions as rows and opens the detail dialog on click", async () => {
    render(<OverviewPage />);

    const row = await screen.findByText("Use Postgres");
    // Dialog starts closed.
    let dialog = screen.getByTestId("decision-detail-dialog");
    expect(dialog).toHaveAttribute("data-open", "false");

    fireEvent.click(row);

    dialog = screen.getByTestId("decision-detail-dialog");
    expect(dialog).toHaveAttribute("data-open", "true");
    expect(dialog).toHaveAttribute("data-decision-id", "d1");
  });

  it("review card shows the count and links to /decisions?filter=review", async () => {
    render(<OverviewPage />);

    expect(await screen.findByText("4")).toBeInTheDocument();
    const link = screen.getByRole("link", { name: /review now/i });
    expect(link).toHaveAttribute("href", "/decisions?filter=review");
  });

  it("review card shows the caught-up state when count is 0", async () => {
    mockUseReviewCounts.mockReturnValue({ count: 0, refresh: jest.fn() });
    render(<OverviewPage />);
    expect(await screen.findByText("All caught up")).toBeInTheDocument();
  });

  it("latest intake shows the newest job and its delta counts", async () => {
    render(<OverviewPage />);

    expect(await screen.findByText("Newest meeting")).toBeInTheDocument();
    expect(screen.queryByText("Old meeting")).not.toBeInTheDocument();

    expect(await screen.findByText("2 new decisions")).toBeInTheDocument();
    expect(screen.getByText("1 supersessions")).toBeInTheDocument();
    expect(screen.getByText("0 conflicts")).toBeInTheDocument();

    // fetchDelta is called for the newest completed job.
    expect(mockFetchDelta).toHaveBeenCalledWith("job-new");
  });

  it("digest card renders markdown content", async () => {
    render(<OverviewPage />);
    const viewers = await screen.findAllByTestId("markdown-viewer");
    expect(
      viewers.some((v) => v.textContent?.includes("Did stuff")),
    ).toBe(true);
  });

  it("digest card shows 'No digest yet.' on null", async () => {
    mockFetchLatestWeeklyDigest.mockResolvedValue(null);
    render(<OverviewPage />);
    expect(await screen.findByText("No digest yet.")).toBeInTheDocument();
  });

  it("cards degrade independently: constitution fetch rejecting does not blank the others", async () => {
    mockFetchDecisions.mockRejectedValue(new Error("boom"));
    render(<OverviewPage />);

    // Constitution card shows its quiet error state.
    expect(
      await screen.findByText(/Couldn't load decisions right now/i),
    ).toBeInTheDocument();

    // Other cards still render their data.
    expect(await screen.findByText("Newest meeting")).toBeInTheDocument();
    expect(await screen.findByText("4")).toBeInTheDocument();
    await waitFor(() => {
      const viewers = screen.queryAllByTestId("markdown-viewer");
      expect(
        viewers.some((v) => v.textContent?.includes("Did stuff")),
      ).toBe(true);
    });
  });
});
