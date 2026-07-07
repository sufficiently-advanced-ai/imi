/**
 * Tests for the Review Queue tab on the Memory page (OB1 absorption Phase 2).
 *
 * The queue is the human gate of the trust ladder: pending captures and
 * agent memories, with confirm/evidence/reject actions via the unified
 * /api/memories review endpoint.
 */

import React from "react";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import MemoryPage from "@/app/(protected)/memory/page";
import { fetchReviewQueue, reviewRecord } from "@/lib/api/agent-memory";

jest.mock("@/lib/api/captures", () => ({
  fetchCaptures: jest.fn().mockResolvedValue({ captures: [], total: 0 }),
  createCapture: jest.fn(),
  reviewCapture: jest.fn(),
  captureReviewBadgeVariant: jest.fn(() => "warning"),
}));

jest.mock("@/lib/api/agent-memory", () => ({
  fetchReviewQueue: jest.fn(),
  reviewRecord: jest.fn(),
}));

const mockedQueue = fetchReviewQueue as jest.Mock;
const mockedReview = reviewRecord as jest.Mock;

const QUEUE_ITEMS = [
  {
    id: "mem-1",
    record_kind: "agent_memory",
    content: "Lesson: batch embedding calls.",
    summary: "Lesson: batch embedding calls.",
    memory_type: "lesson",
    runtime_name: "openclaw",
    task_id: "task-1",
    provenance_status: "generated",
    created_at: "2026-07-03T12:00:00+00:00",
  },
  {
    id: "cap-1",
    record_kind: "capture",
    content: "A pending capture.",
    summary: null,
    memory_type: null,
    runtime_name: null,
    task_id: null,
    provenance_status: "imported",
    created_at: "2026-07-03T11:00:00+00:00",
  },
];

beforeEach(() => {
  jest.clearAllMocks();
  mockedQueue.mockResolvedValue({ items: QUEUE_ITEMS, total: 2 });
  mockedReview.mockResolvedValue({
    success: true,
    review_applied: true,
    gate_response: "allow",
  });
});

async function openQueueTab() {
  render(<MemoryPage />);
  await userEvent.click(
    await screen.findByRole("tab", { name: /review queue/i }),
  );
  await screen.findByText(/batch embedding calls/i);
}

it("lists pending records across kinds with kind badges", async () => {
  await openQueueTab();
  expect(screen.getByText(/A pending capture/)).toBeInTheDocument();
  expect(screen.getByText("agent_memory")).toBeInTheDocument();
  expect(screen.getByText("capture")).toBeInTheDocument();
  expect(screen.getByText("lesson")).toBeInTheDocument();
});

it("confirm action posts to the unified review endpoint and refreshes", async () => {
  await openQueueTab();
  const confirmButtons = screen.getAllByRole("button", { name: /confirm/i });
  fireEvent.click(confirmButtons[0]);

  await waitFor(() =>
    expect(mockedReview).toHaveBeenCalledWith("mem-1", "confirm"),
  );
  await waitFor(() => expect(mockedQueue).toHaveBeenCalledTimes(2));
});

it("shows an empty state when nothing is pending", async () => {
  mockedQueue.mockResolvedValue({ items: [], total: 0 });
  render(<MemoryPage />);
  await userEvent.click(
    await screen.findByRole("tab", { name: /review queue/i }),
  );
  expect(await screen.findByText(/nothing pending/i)).toBeInTheDocument();
});
