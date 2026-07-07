/**
 * Tests for the Memory page (OB1 absorption Phase 1 — capture loop UI).
 *
 * Covers:
 *  - fetched captures rendered with content + governance badge
 *  - quick capture submits createCapture and refreshes the list
 *  - dedup result surfaces a "already captured" notice
 *  - inline review action delegates to reviewCapture
 */

import React from "react";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";

import MemoryPage from "@/app/(protected)/memory/page";

import {
  createCapture,
  fetchCaptures,
  reviewCapture,
} from "@/lib/api/captures";

jest.mock("@/lib/api/captures", () => ({
  fetchCaptures: jest.fn(),
  createCapture: jest.fn(),
  reviewCapture: jest.fn(),
  captureReviewBadgeVariant: jest.fn(() => "warning"),
}));

const mockedFetchCaptures = fetchCaptures as jest.Mock;
const mockedCreateCapture = createCapture as jest.Mock;
const mockedReviewCapture = reviewCapture as jest.Mock;

const CAPTURE = {
  id: "cap-1",
  content: "We standardized on FastAPI for all new services.",
  source: "manual",
  source_id: null,
  summary: null,
  tags: [],
  enrichment: { type: "decision", topics: ["architecture"] },
  related_record_ids: [],
  provenance_status: "imported",
  review_status: "pending",
  can_use_as_evidence: true,
  can_use_as_instruction: false,
  superseded_by: null,
  valid_from: null,
  valid_to: null,
  tenant_id: null,
  created_at: "2026-07-03T12:00:00+00:00",
};

beforeEach(() => {
  jest.clearAllMocks();
  mockedFetchCaptures.mockResolvedValue({ captures: [CAPTURE], total: 1 });
  mockedCreateCapture.mockResolvedValue({
    success: true,
    id: "cap-2",
    deduped: false,
    enrichment: {},
    vector_indexed: true,
    committed: true,
    capture: { ...CAPTURE, id: "cap-2", content: "Fresh thought." },
  });
  mockedReviewCapture.mockResolvedValue({
    success: true,
    review_applied: true,
    gate_response: "allow",
  });
});

it("renders fetched captures with content and review status", async () => {
  render(<MemoryPage />);
  expect(
    await screen.findByText(/We standardized on FastAPI/),
  ).toBeInTheDocument();
  expect(screen.getByText(/pending/i)).toBeInTheDocument();
});

it("submits a quick capture and refreshes the list", async () => {
  render(<MemoryPage />);
  await screen.findByText(/We standardized on FastAPI/);

  fireEvent.change(screen.getByPlaceholderText(/capture a thought/i), {
    target: { value: "Fresh thought." },
  });
  fireEvent.click(screen.getByRole("button", { name: /^capture$/i }));

  await waitFor(() =>
    expect(mockedCreateCapture).toHaveBeenCalledWith(
      expect.objectContaining({ content: "Fresh thought." }),
    ),
  );
  // list refreshed after create (initial + refresh)
  await waitFor(() => expect(mockedFetchCaptures).toHaveBeenCalledTimes(2));
});

it("shows a dedup notice when the capture already exists", async () => {
  mockedCreateCapture.mockResolvedValueOnce({
    success: true,
    id: "cap-1",
    deduped: true,
    enrichment: {},
    vector_indexed: false,
    committed: false,
    capture: CAPTURE,
  });

  render(<MemoryPage />);
  await screen.findByText(/We standardized on FastAPI/);

  fireEvent.change(screen.getByPlaceholderText(/capture a thought/i), {
    target: { value: "We standardized on FastAPI for all new services." },
  });
  fireEvent.click(screen.getByRole("button", { name: /^capture$/i }));

  expect(await screen.findByText(/already captured/i)).toBeInTheDocument();
});

it("review action delegates to reviewCapture", async () => {
  render(<MemoryPage />);
  await screen.findByText(/We standardized on FastAPI/);

  fireEvent.click(screen.getByRole("button", { name: /confirm/i }));

  await waitFor(() =>
    expect(mockedReviewCapture).toHaveBeenCalledWith("cap-1", "confirm"),
  );
});
