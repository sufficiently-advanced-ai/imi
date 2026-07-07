/**
 * Tests for the Judge activity tab on the Memory page (OB1 absorption Phase 4).
 * Oversight surface: recent judgment events with risk class, decision, and
 * check summaries.
 */

import React from "react";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import MemoryPage from "@/app/(protected)/memory/page";
import { fetchJudgeDecisions } from "@/lib/api/judge";

jest.mock("@/lib/api/captures", () => ({
  fetchCaptures: jest.fn().mockResolvedValue({ captures: [], total: 0 }),
  createCapture: jest.fn(),
  reviewCapture: jest.fn(),
  captureReviewBadgeVariant: jest.fn(() => "warning"),
}));

jest.mock("@/lib/api/agent-memory", () => ({
  fetchReviewQueue: jest.fn().mockResolvedValue({ items: [], total: 0 }),
  reviewRecord: jest.fn(),
}));

jest.mock("@/lib/api/judge", () => ({
  fetchJudgeDecisions: jest.fn(),
  judgeDecisionBadgeVariant: jest.fn(() => "destructive"),
}));

const mockedDecisions = fetchJudgeDecisions as jest.Mock;

beforeEach(() => {
  jest.clearAllMocks();
  mockedDecisions.mockResolvedValue({
    decisions: [
      {
        decision_id: "jd-1",
        action_id: "act-1",
        risk_class: "external_side_effect",
        decision: "block",
        reasoning_summary: "Confirmed constraint forbids mass email.",
        checks: { policy: "fail" },
        memory_used: [],
        memory_written: [],
        runtime_name: "openclaw",
        task_id: "task-7",
        created_at: "2026-07-03T12:00:00+00:00",
      },
    ],
    total: 1,
  });
});

it("lists judgment events with decision and risk class", async () => {
  render(<MemoryPage />);
  await userEvent.click(await screen.findByRole("tab", { name: /judge/i }));

  expect(
    await screen.findByText(/Confirmed constraint forbids mass email/),
  ).toBeInTheDocument();
  expect(screen.getByText("block")).toBeInTheDocument();
  expect(screen.getByText(/external_side_effect/)).toBeInTheDocument();
});

it("shows an empty state without events", async () => {
  mockedDecisions.mockResolvedValue({ decisions: [], total: 0 });
  render(<MemoryPage />);
  await userEvent.click(await screen.findByRole("tab", { name: /judge/i }));
  expect(await screen.findByText(/no judge activity/i)).toBeInTheDocument();
});
