import React from "react";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { DecisionRow } from "../DecisionRow";
import type { Decision } from "@/lib/api/decisions";

// Minimal Decision fixture — fills every required field
function makeDecision(overrides: Partial<Decision> = {}): Decision {
  return {
    id: "dec-1",
    content: "Use PostgreSQL as the primary database",
    state: "active",
    state_reason: null,
    age_days: 10,
    review_status: null,
    provenance_status: null,
    can_use_as_evidence: true,
    can_use_as_instruction: true,
    owner: "alice",
    owner_id: null,
    client_id: null,
    source_meeting_id: null,
    source_meeting_title: "Arch sync",
    source_timestamp: "2026-06-10T10:00:00Z",
    superseded_by: null,
    tenant_id: null,
    metadata: {},
    ...overrides,
  };
}

describe("DecisionRow", () => {
  it("renders decision content", () => {
    render(<DecisionRow decision={makeDecision()} onClick={jest.fn()} />);
    expect(
      screen.getByText("Use PostgreSQL as the primary database"),
    ).toBeInTheDocument();
  });

  it("renders owner when present", () => {
    render(<DecisionRow decision={makeDecision({ owner: "alice" })} onClick={jest.fn()} />);
    expect(screen.getByText("alice")).toBeInTheDocument();
  });

  it("renders state badge", () => {
    render(<DecisionRow decision={makeDecision({ state: "active" })} onClick={jest.fn()} />);
    expect(screen.getByText("ACTIVE")).toBeInTheDocument();
  });

  it("renders candidate state badge", () => {
    render(<DecisionRow decision={makeDecision({ state: "candidate" })} onClick={jest.fn()} />);
    expect(screen.getByText("CANDIDATE")).toBeInTheDocument();
  });

  it("renders stale state badge", () => {
    render(<DecisionRow decision={makeDecision({ state: "stale" })} onClick={jest.fn()} />);
    expect(screen.getByText("STALE")).toBeInTheDocument();
  });

  it("renders source meeting title when present", () => {
    render(
      <DecisionRow
        decision={makeDecision({ source_meeting_title: "Arch sync" })}
        onClick={jest.fn()}
      />,
    );
    expect(screen.getByText("Arch sync")).toBeInTheDocument();
  });

  it("renders formatted source date when source_timestamp is present", () => {
    render(
      <DecisionRow
        decision={makeDecision({ source_timestamp: "2026-06-10T10:00:00Z" })}
        onClick={jest.fn()}
      />,
    );
    // Formatted as "Jun 10, 2026"
    expect(screen.getByText(/Jun 10, 2026/)).toBeInTheDocument();
  });

  it("fires onClick when clicked", async () => {
    const onClick = jest.fn();
    render(<DecisionRow decision={makeDecision()} onClick={onClick} />);
    await userEvent.click(screen.getByRole("button"));
    expect(onClick).toHaveBeenCalledTimes(1);
  });

  it("fires onClick on Enter key", async () => {
    const onClick = jest.fn();
    render(<DecisionRow decision={makeDecision()} onClick={onClick} />);
    screen.getByRole("button").focus();
    await userEvent.keyboard("{Enter}");
    expect(onClick).toHaveBeenCalledTimes(1);
  });

  it("fires onClick on Space key", async () => {
    const onClick = jest.fn();
    render(<DecisionRow decision={makeDecision()} onClick={onClick} />);
    screen.getByRole("button").focus();
    await userEvent.keyboard(" ");
    expect(onClick).toHaveBeenCalledTimes(1);
  });

  it("renders nothing for owner when owner is null", () => {
    render(<DecisionRow decision={makeDecision({ owner: null })} onClick={jest.fn()} />);
    // "alice" should not appear
    expect(screen.queryByText("alice")).not.toBeInTheDocument();
  });

  it("renders nothing for source when source_meeting_title is null", () => {
    render(
      <DecisionRow
        decision={makeDecision({ source_meeting_title: null, source_timestamp: null })}
        onClick={jest.fn()}
      />,
    );
    expect(screen.queryByText("Arch sync")).not.toBeInTheDocument();
  });
});
