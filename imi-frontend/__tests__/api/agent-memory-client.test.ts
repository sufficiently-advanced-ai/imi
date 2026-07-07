/**
 * Tests for the agent-memory API client (OB1 absorption Phase 2)
 * - fetchReviewQueue → GET /api/memories/review
 * - reviewRecord → POST /api/memories/{id}/review (kind auto-resolved server-side)
 */

jest.mock("@/lib/config", () => ({
  getApiUrl: jest.fn((path: string) => `/api${path}`),
}));

import { fetchReviewQueue, reviewRecord } from "@/lib/api/agent-memory";

const mockedFetch = global.fetch as jest.Mock;

const ok = (body: unknown) => ({
  status: 200,
  ok: true,
  json: jest.fn().mockResolvedValueOnce(body),
});

beforeEach(() => {
  mockedFetch.mockReset();
});

describe("fetchReviewQueue", () => {
  it("GETs /api/memories/review and returns items", async () => {
    const payload = {
      items: [
        {
          id: "mem-1",
          record_kind: "agent_memory",
          content: "Batch calls.",
          memory_type: "lesson",
          provenance_status: "generated",
          created_at: "2026-07-03T12:00:00+00:00",
        },
      ],
      total: 1,
    };
    mockedFetch.mockResolvedValueOnce(ok(payload));

    const result = await fetchReviewQueue();

    expect(mockedFetch.mock.calls[0][0]).toBe("/api/memories/review");
    expect(result.items[0].record_kind).toBe("agent_memory");
  });

  it("passes kind filter", async () => {
    mockedFetch.mockResolvedValueOnce(ok({ items: [], total: 0 }));
    await fetchReviewQueue({ kind: "capture" });
    expect(mockedFetch.mock.calls[0][0]).toContain("kind=capture");
  });
});

describe("reviewRecord", () => {
  it("POSTs the action to the unified review endpoint", async () => {
    mockedFetch.mockResolvedValueOnce(
      ok({ success: true, review_applied: true, gate_response: "allow" }),
    );

    const result = await reviewRecord("mem-1", "confirm", "scott");

    const [url, options] = mockedFetch.mock.calls[0];
    expect(url).toBe("/api/memories/mem-1/review");
    expect(options.method).toBe("POST");
    expect(JSON.parse(options.body)).toEqual({ action: "confirm", actor: "scott" });
    expect(result.review_applied).toBe(true);
  });
});
