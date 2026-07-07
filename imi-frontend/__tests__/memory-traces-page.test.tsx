/**
 * Tests for the recall traces page (OB1 absorption Phase 5).
 * Debugger: what agents asked, what was returned, what they used/ignored.
 */

import React from "react";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import MemoryTracesPage from "@/app/(protected)/memory/traces/page";
import { fetchRecallTrace, fetchRecallTraces } from "@/lib/api/agent-memory";

jest.mock("@/lib/api/agent-memory", () => ({
  fetchRecallTraces: jest.fn(),
  fetchRecallTrace: jest.fn(),
}));

const mockedList = fetchRecallTraces as jest.Mock;
const mockedDetail = fetchRecallTrace as jest.Mock;

beforeEach(() => {
  jest.clearAllMocks();
  mockedList.mockResolvedValue({
    traces: [
      {
        request_id: "req-1",
        query: "what framework do we use",
        authority: "evidence",
        surface: "agent_recall",
        runtime_name: "openclaw",
        task_id: "task-1",
        created_at: "2026-07-03T12:00:00+00:00",
        items: null,
      },
    ],
    total: 1,
  });
  mockedDetail.mockResolvedValue({
    request_id: "req-1",
    query: "what framework do we use",
    items: [
      {
        record_id: "cap-1",
        record_kind: "capture",
        rank: 0,
        similarity: 0.91,
        ranking_score: 1.2,
        used: true,
        ignored_reason: null,
      },
      {
        record_id: "mem-2",
        record_kind: "agent_memory",
        rank: 1,
        similarity: 0.72,
        ranking_score: 0.8,
        used: false,
        ignored_reason: "off-topic",
      },
    ],
  });
});

it("lists traces and expands to ranked items with usage marks", async () => {
  render(<MemoryTracesPage />);
  expect(await screen.findByText(/what framework do we use/)).toBeInTheDocument();

  await userEvent.click(screen.getByText(/what framework do we use/));

  expect(await screen.findByText("cap-1")).toBeInTheDocument();
  expect(screen.getByText(/sim 0\.91/)).toBeInTheDocument();
  expect(screen.getByText(/ignored — off-topic/)).toBeInTheDocument();
});

it("shows an empty state without traces", async () => {
  mockedList.mockResolvedValue({ traces: [], total: 0 });
  render(<MemoryTracesPage />);
  expect(await screen.findByText(/no recall traces/i)).toBeInTheDocument();
});
