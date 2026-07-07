import React from "react";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ReviewQueue } from "../ReviewQueue";

jest.mock("@/lib/api/decisions", () => ({
  fetchSupersessionCandidates: jest.fn(),
  fetchConflictCandidates: jest.fn(),
  confirmConflictCandidate: jest.fn(),
  dismissConflictCandidate: jest.fn(),
  fetchDecisions: jest.fn(),
  reviewDecision: jest.fn(),
}));
jest.mock("@/lib/api/ingest", () => ({
  confirmCandidate: jest.fn(),
  dismissCandidate: jest.fn(),
}));

import {
  fetchSupersessionCandidates,
  fetchConflictCandidates,
  dismissConflictCandidate,
  fetchDecisions,
  reviewDecision,
} from "@/lib/api/decisions";
import { confirmCandidate } from "@/lib/api/ingest";

const SUPERSESSION = {
  new_signal_id: "sig-new",
  new_content: "PostgreSQL selected for Apollo",
  old_signal_id: "sig-old",
  old_content: "MySQL chosen as primary store",
  matched_entities: ["Apollo"],
  reason: "same entities, newer evidence",
  confidence: 0.87,
  proposed_at: "2026-06-12T10:00:00Z",
};

const CONFLICT = {
  signal_id: "sig-a",
  signal_content: "Demos will show the graph view",
  other_signal_id: "sig-b",
  other_content: "No graph visualizations in demos",
  rationale: "directly contradictory demo policy",
  confidence: 0.74,
  speakers: ["Scott", "Chris"],
  proposed_at: "2026-06-12T11:00:00Z",
};

const CANDIDATE_DECISION = {
  id: "dec-1",
  content: "Use Postgres everywhere",
  state: "candidate",
  owner: "alice",
  client_id: null,
  source_meeting_title: "Arch sync",
  source_timestamp: "2026-06-10T10:00:00Z",
};

beforeEach(() => {
  jest.clearAllMocks();
  (fetchSupersessionCandidates as jest.Mock).mockResolvedValue([SUPERSESSION]);
  (fetchConflictCandidates as jest.Mock).mockResolvedValue([CONFLICT]);
  (fetchDecisions as jest.Mock).mockResolvedValue({
    decisions: [CANDIDATE_DECISION],
    total: 1,
    counts_by_state: { candidate: 1 },
  });
});

test("renders both sections with candidate content", async () => {
  render(<ReviewQueue />);
  expect(await screen.findByText(/proposed supersessions/i)).toBeInTheDocument();
  expect(screen.getByText(/potential conflicts/i)).toBeInTheDocument();
  expect(screen.getByText(/PostgreSQL selected for Apollo/)).toBeInTheDocument();
  expect(screen.getByText(/No graph visualizations in demos/)).toBeInTheDocument();
});

test("confirm on a supersession row calls confirmCandidate and shows chip", async () => {
  (confirmCandidate as jest.Mock).mockResolvedValue({ confirmed: true });
  const onActioned = jest.fn();
  render(<ReviewQueue onActioned={onActioned} />);
  const confirmBtns = await screen.findAllByRole("button", { name: /confirm/i });
  await userEvent.click(confirmBtns[0]);
  await waitFor(() =>
    expect(confirmCandidate).toHaveBeenCalledWith({
      new_signal_id: "sig-new",
      old_signal_id: "sig-old",
    }),
  );
  expect(await screen.findByText(/confirmed/i)).toBeInTheDocument();
  expect(onActioned).toHaveBeenCalledWith("supersession", "confirm");
});

test("dismiss on a conflict row calls dismissConflictCandidate", async () => {
  (dismissConflictCandidate as jest.Mock).mockResolvedValue({ dismissed: true });
  render(<ReviewQueue />);
  const dismissBtns = await screen.findAllByRole("button", { name: /dismiss/i });
  await userEvent.click(dismissBtns[1]);
  await waitFor(() =>
    expect(dismissConflictCandidate).toHaveBeenCalledWith({
      signal_id: "sig-a",
      other_signal_id: "sig-b",
    }),
  );
});

test("API failure reverts the row and shows error", async () => {
  (confirmCandidate as jest.Mock).mockRejectedValue(new Error("boom"));
  render(<ReviewQueue />);
  const confirmBtns = await screen.findAllByRole("button", { name: /confirm/i });
  await userEvent.click(confirmBtns[0]);
  expect(await screen.findByText(/boom/)).toBeInTheDocument();
  expect(screen.getAllByRole("button", { name: /confirm/i }).length).toBeGreaterThan(0);
});

test("empty state when all three lists are empty", async () => {
  (fetchSupersessionCandidates as jest.Mock).mockResolvedValue([]);
  (fetchConflictCandidates as jest.Mock).mockResolvedValue([]);
  (fetchDecisions as jest.Mock).mockResolvedValue({
    decisions: [],
    total: 0,
    counts_by_state: {},
  });
  render(<ReviewQueue />);
  expect(await screen.findByText(/no pending candidates/i)).toBeInTheDocument();
});

test("renders unreviewed decisions section with content", async () => {
  render(<ReviewQueue />);
  expect(await screen.findByText(/unreviewed decisions \(1\)/i)).toBeInTheDocument();
  expect(screen.getByText(/Use Postgres everywhere/)).toBeInTheDocument();
});

test("approve calls reviewDecision with confirm and fires onActioned", async () => {
  (reviewDecision as jest.Mock).mockResolvedValue({ reviewed: true, new_state: "active" });
  const onActioned = jest.fn();
  render(<ReviewQueue onActioned={onActioned} />);
  const approveBtn = await screen.findByRole("button", { name: /approve/i });
  await userEvent.click(approveBtn);
  await waitFor(() =>
    expect(reviewDecision).toHaveBeenCalledWith("dec-1", { action: "confirm" }),
  );
  expect(await screen.findByText(/approved/i)).toBeInTheDocument();
  expect(onActioned).toHaveBeenCalledWith("decision", "confirm");
});

test("evidence only calls reviewDecision with evidence_only and shows chip", async () => {
  (reviewDecision as jest.Mock).mockResolvedValue({
    reviewed: true,
    new_state: "active",
  });
  render(<ReviewQueue />);
  const evidenceBtn = await screen.findByRole("button", {
    name: /evidence only/i,
  });
  await userEvent.click(evidenceBtn);
  await waitFor(() =>
    expect(reviewDecision).toHaveBeenCalledWith("dec-1", {
      action: "evidence_only",
    }),
  );
  expect(await screen.findByText(/^evidence only$/i)).toBeInTheDocument();
});

test("decision review failure reverts the row and shows error", async () => {
  (reviewDecision as jest.Mock).mockRejectedValue(new Error("transition denied"));
  render(<ReviewQueue />);
  const approveBtn = await screen.findByRole("button", { name: /approve/i });
  await userEvent.click(approveBtn);
  expect(await screen.findByText(/transition denied/)).toBeInTheDocument();
  expect(
    screen.getByRole("button", { name: /approve/i }),
  ).toBeInTheDocument();
});

test("reject calls reviewDecision with reject action", async () => {
  (reviewDecision as jest.Mock).mockResolvedValue({ reviewed: true, new_state: "rejected" });
  render(<ReviewQueue />);
  const rejectBtn = await screen.findByRole("button", { name: /^reject$/i });
  await userEvent.click(rejectBtn);
  await waitFor(() =>
    expect(reviewDecision).toHaveBeenCalledWith("dec-1", { action: "reject" }),
  );
});
