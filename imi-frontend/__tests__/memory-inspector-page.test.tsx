/**
 * Tests for the memory inspector page (OB1 absorption Phase 5).
 * The trust surface: record + governance position, audit timeline, usage
 * stats, lineage. Deleted records still render their audit history.
 */

import React from "react";
import { render, screen } from "@testing-library/react";

import MemoryInspectorPage from "@/app/(protected)/memory/[id]/page";
import { fetchInspector } from "@/lib/api/agent-memory";

jest.mock("next/navigation", () => ({
  useParams: jest.fn(() => ({ id: "cap-1" })),
}));

jest.mock("@/lib/api/agent-memory", () => ({
  fetchInspector: jest.fn(),
}));

const mockedInspector = fetchInspector as jest.Mock;

const INSPECTOR = {
  schema_version: "imi.memory.inspector.v1",
  record_id: "cap-1",
  record_kind: "capture",
  record: {
    id: "cap-1",
    content: "We standardized on FastAPI.",
    provenance_status: "user_confirmed",
    review_status: "confirmed",
    can_use_as_instruction: true,
    can_use_as_evidence: true,
    created_at: "2026-07-03T12:00:00+00:00",
  },
  audit_history: [
    {
      action: "capture",
      gate_response: null,
      actor: "scott",
      reasoning: "captured from source=manual",
      created_at: "2026-07-03T12:00:00+00:00",
    },
    {
      action: "confirm",
      gate_response: "allow",
      actor: "scott",
      reasoning: "action=confirm gate=allow",
      created_at: "2026-07-03T13:00:00+00:00",
    },
  ],
  usage: {
    times_returned: 5,
    times_used: 3,
    times_ignored: 1,
    last_returned_at: "2026-07-03T14:00:00+00:00",
  },
  judge_usage: [
    { decision_id: "jd-1", decision: "block", used_as: "instruction", task_id: "t-1" },
  ],
  lineage: [{ record_id: "cap-1", relation: "self" }],
  influence: {
    can_use_as_instruction: true,
    can_use_as_evidence: true,
    position: "instruction",
    superseded_by: null,
  },
};

beforeEach(() => {
  jest.clearAllMocks();
  mockedInspector.mockResolvedValue(INSPECTOR);
});

it("renders the four inspector questions", async () => {
  render(<MemoryInspectorPage />);
  // why it exists
  expect(await screen.findByText(/We standardized on FastAPI/)).toBeInTheDocument();
  expect(screen.getByText(/user_confirmed/)).toBeInTheDocument();
  // what created it — audit timeline
  expect(screen.getByText(/action=confirm gate=allow/)).toBeInTheDocument();
  // how it was used
  expect(screen.getByText(/Returned 5/i)).toBeInTheDocument();
  expect(screen.getByText(/Used 3/i)).toBeInTheDocument();
  // what it can influence
  expect(screen.getByText("instruction")).toBeInTheDocument();
});

it("renders deleted records from the audit trail", async () => {
  mockedInspector.mockResolvedValue({
    ...INSPECTOR,
    record: null,
    influence: { ...INSPECTOR.influence, position: "blocked" },
  });
  render(<MemoryInspectorPage />);
  expect(await screen.findByText(/record was deleted/i)).toBeInTheDocument();
  expect(screen.getByText(/action=confirm gate=allow/)).toBeInTheDocument();
});
