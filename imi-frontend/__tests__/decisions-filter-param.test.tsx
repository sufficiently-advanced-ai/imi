/**
 * Tests for D5: decisions page ?filter= URL param support.
 *
 * Covers:
 *  - ?filter=review opens with the Review queue visible (ReviewQueue rendered)
 *  - invalid ?filter= value falls back to 'all' (FilterBar shows 'All' as active)
 *  - no param defaults to 'all'
 */

import React from "react";
import { render, screen } from "@testing-library/react";

import DecisionsPage from "@/app/(protected)/decisions/page";

import {
  fetchDecisions,
  fetchDecisionStats,
} from "@/lib/api/decisions";
import { useReviewCounts } from "@/lib/hooks/useReviewCounts";
import { useSearchParams } from "next/navigation";

// ---- mocks ----

jest.mock("@/lib/api/decisions", () => ({
  fetchDecisions: jest.fn(),
  fetchDecisionStats: jest.fn(),
  decisionStateBadgeVariant: () => "secondary",
}));

jest.mock("@/lib/hooks/useReviewCounts", () => ({
  useReviewCounts: jest.fn(),
}));

// next/navigation — we'll override useSearchParams per-test
jest.mock("next/navigation", () => ({
  usePathname: jest.fn(() => "/decisions"),
  useSearchParams: jest.fn(() => new URLSearchParams()),
  useRouter: jest.fn(() => ({
    push: jest.fn(),
    replace: jest.fn(),
    prefetch: jest.fn(),
    back: jest.fn(),
    forward: jest.fn(),
    refresh: jest.fn(),
  })),
}));

// Heavy child components — stub to keep renders cheap
jest.mock("@/components/decisions/ReviewQueue", () => ({
  ReviewQueue: ({ onActioned }: { onActioned: () => void }) => (
    <div data-testid="review-queue" onClick={onActioned}>
      Review Queue
    </div>
  ),
}));

jest.mock("@/components/decisions/GovernanceLadder", () => ({
  GovernanceLadder: () => <div data-testid="governance-ladder" />,
}));

jest.mock("@/components/decisions/DecisionDetailDialog", () => ({
  DecisionDetailDialog: ({
    open,
  }: {
    decisionId: string | null;
    open: boolean;
  }) => <div data-testid="decision-detail-dialog" data-open={String(open)} />,
}));

jest.mock("@/components/decisions/ExportConstitutionButton", () => ({
  ExportConstitutionButton: () => (
    <div data-testid="export-constitution">Export</div>
  ),
}));

jest.mock("@/components/ui/page-container", () => ({
  PageContainer: ({ children }: { children: React.ReactNode }) => (
    <div>{children}</div>
  ),
}));

jest.mock("@/contexts/DomainContext", () => ({
  useDomain: jest.fn(() => ({
    getNavLabel: (_group: string, _path: string, fallback: string) => fallback,
    getGroupLabel: (_group: string, fallback: string) => fallback,
    getEntityDisplayName: (key: string) => key,
    domainConfig: null,
    uiLabels: null,
    isLoading: false,
    error: null,
  })),
}));

// ---- typed mock helpers ----

const mockFetchDecisions = fetchDecisions as jest.Mock;
const mockFetchDecisionStats = fetchDecisionStats as jest.Mock;
const mockUseReviewCounts = useReviewCounts as jest.Mock;
const mockUseSearchParams = useSearchParams as jest.Mock;

// ---- fixtures ----

function setHappyDefaults() {
  mockFetchDecisions.mockResolvedValue({
    decisions: [],
    total: 0,
    counts_by_state: {},
  });
  mockFetchDecisionStats.mockResolvedValue({
    meetings: 0,
    decisions: 0,
    counts_by_state: {},
    stale: 0,
    superseded: 0,
    headline: "0 decisions",
  });
  mockUseReviewCounts.mockReturnValue({ count: 3, refresh: jest.fn() });
}

beforeEach(() => {
  jest.clearAllMocks();
  setHappyDefaults();
});

describe("DecisionsPage ?filter= param", () => {
  it("?filter=review renders the ReviewQueue (review filter active)", async () => {
    mockUseSearchParams.mockReturnValue(new URLSearchParams("filter=review"));

    render(<DecisionsPage />);

    // The Review filter renders the ReviewQueue component
    expect(await screen.findByTestId("review-queue")).toBeInTheDocument();
    // The standard list cards are NOT shown when review filter is active
    expect(screen.queryByTestId("governance-ladder")).not.toBeInTheDocument();
  });

  it("invalid ?filter= value falls back to 'all' (no review queue)", async () => {
    mockUseSearchParams.mockReturnValue(
      new URLSearchParams("filter=not_a_real_filter"),
    );

    render(<DecisionsPage />);

    // 'all' filter does NOT show the ReviewQueue
    // Wait for async loads to settle (EmptyState renders when no decisions)
    expect(
      await screen.findByText(/no decisions match|no decisions yet/i),
    ).toBeInTheDocument();
    expect(screen.queryByTestId("review-queue")).not.toBeInTheDocument();
  });

  it("no param defaults to 'all' (no review queue shown)", async () => {
    mockUseSearchParams.mockReturnValue(new URLSearchParams());

    render(<DecisionsPage />);

    expect(
      await screen.findByText(/no decisions match|no decisions yet/i),
    ).toBeInTheDocument();
    expect(screen.queryByTestId("review-queue")).not.toBeInTheDocument();
  });
});
