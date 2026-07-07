/**
 * Tests for useReviewCounts hook
 *
 * count = supersessions.length + conflicts.length + candidateDecisions.total
 * - null until first successful load
 * - refetches on 60s interval
 * - refetches on visibilitychange → visible
 * - keeps last value on fetch failure (silent)
 */

import { renderHook, act } from "@testing-library/react";

// Mock the decisions API module before importing the hook
jest.mock("@/lib/api/decisions", () => ({
  fetchSupersessionCandidates: jest.fn(),
  fetchConflictCandidates: jest.fn(),
  fetchDecisions: jest.fn(),
}));

import {
  fetchSupersessionCandidates,
  fetchConflictCandidates,
  fetchDecisions,
} from "@/lib/api/decisions";
import { useReviewCounts } from "@/lib/hooks/useReviewCounts";

const mockFetchSupersessionCandidates = fetchSupersessionCandidates as jest.Mock;
const mockFetchConflictCandidates = fetchConflictCandidates as jest.Mock;
const mockFetchDecisions = fetchDecisions as jest.Mock;

/** Shorthand: resolve all three mocks with given sizes/total. */
function mockSuccess(
  supersessionCount: number,
  conflictCount: number,
  candidateTotal: number,
) {
  mockFetchSupersessionCandidates.mockResolvedValue(
    Array.from({ length: supersessionCount }, (_, i) => ({ new_signal_id: `s${i}` })),
  );
  mockFetchConflictCandidates.mockResolvedValue(
    Array.from({ length: conflictCount }, (_, i) => ({ signal_id: `c${i}` })),
  );
  mockFetchDecisions.mockResolvedValue({
    decisions: [],
    total: candidateTotal,
    counts_by_state: { candidate: candidateTotal },
  });
}

describe("useReviewCounts", () => {
  beforeEach(() => {
    jest.useFakeTimers();
    mockFetchSupersessionCandidates.mockReset();
    mockFetchConflictCandidates.mockReset();
    mockFetchDecisions.mockReset();
  });

  afterEach(() => {
    jest.useRealTimers();
  });

  it("is null before first successful load", () => {
    // Never resolves
    mockFetchSupersessionCandidates.mockReturnValue(new Promise(() => {}));
    mockFetchConflictCandidates.mockReturnValue(new Promise(() => {}));
    mockFetchDecisions.mockReturnValue(new Promise(() => {}));

    const { result } = renderHook(() => useReviewCounts());

    expect(result.current.count).toBeNull();
  });

  it("sums supersession + conflict + candidate counts on mount", async () => {
    mockSuccess(3, 2, 5);

    const { result } = renderHook(() => useReviewCounts());

    await act(async () => {
      await Promise.resolve(); // flush microtasks
    });

    expect(result.current.count).toBe(10); // 3 + 2 + 5
  });

  it("exposes a refresh function that re-fetches immediately", async () => {
    mockSuccess(1, 1, 1);

    const { result } = renderHook(() => useReviewCounts());

    await act(async () => {
      await Promise.resolve();
    });

    expect(result.current.count).toBe(3);

    // Change the mocked values
    mockSuccess(0, 0, 2);

    await act(async () => {
      await result.current.refresh();
    });

    expect(result.current.count).toBe(2);
  });

  it("refetches on 60s interval", async () => {
    mockSuccess(1, 0, 0);

    const { result } = renderHook(() => useReviewCounts());

    await act(async () => {
      await Promise.resolve();
    });

    expect(result.current.count).toBe(1);

    mockSuccess(5, 3, 2);

    await act(async () => {
      jest.advanceTimersByTime(60_000);
      await Promise.resolve();
    });

    expect(result.current.count).toBe(10); // 5 + 3 + 2
    expect(mockFetchDecisions).toHaveBeenCalledTimes(2);
  });

  it("refetches on visibilitychange → visible", async () => {
    mockSuccess(2, 1, 0);

    const { result } = renderHook(() => useReviewCounts());

    await act(async () => {
      await Promise.resolve();
    });

    expect(result.current.count).toBe(3);

    mockSuccess(4, 4, 4);

    await act(async () => {
      Object.defineProperty(document, "visibilityState", {
        value: "visible",
        writable: true,
        configurable: true,
      });
      document.dispatchEvent(new Event("visibilitychange"));
      await Promise.resolve();
    });

    expect(result.current.count).toBe(12); // 4 + 4 + 4
  });

  it("keeps last value on fetch failure (silent)", async () => {
    mockSuccess(2, 2, 2);

    const { result } = renderHook(() => useReviewCounts());

    await act(async () => {
      await Promise.resolve();
    });

    expect(result.current.count).toBe(6);

    // Now make all fetches fail
    mockFetchSupersessionCandidates.mockRejectedValue(new Error("Network error"));
    mockFetchConflictCandidates.mockRejectedValue(new Error("Network error"));
    mockFetchDecisions.mockRejectedValue(new Error("Network error"));

    await act(async () => {
      await result.current.refresh().catch(() => {});
      await Promise.resolve();
    });

    // Should keep last known value
    expect(result.current.count).toBe(6);
  });

  it("cleans up interval on unmount", async () => {
    const clearIntervalSpy = jest.spyOn(global, "clearInterval");

    mockSuccess(0, 0, 0);

    const { unmount } = renderHook(() => useReviewCounts());

    await act(async () => {
      await Promise.resolve();
    });

    unmount();

    expect(clearIntervalSpy).toHaveBeenCalled();
    clearIntervalSpy.mockRestore();
  });

  it("cleans up visibilitychange listener on unmount", async () => {
    const removeEventListenerSpy = jest.spyOn(document, "removeEventListener");

    mockSuccess(0, 0, 0);

    const { unmount } = renderHook(() => useReviewCounts());

    await act(async () => {
      await Promise.resolve();
    });

    unmount();

    expect(removeEventListenerSpy).toHaveBeenCalledWith(
      "visibilitychange",
      expect.any(Function),
    );
    removeEventListenerSpy.mockRestore();
  });
});
