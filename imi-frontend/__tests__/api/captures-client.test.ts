/**
 * Tests for the captures API client (OB1 absorption Phase 1)
 * - fetchCaptures → GET /api/captures (filters as query params)
 * - createCapture → POST /api/captures
 * - reviewCapture → POST /api/captures/{id}/review
 */

jest.mock("@/lib/config", () => ({
  getApiUrl: jest.fn((path: string) => `/api${path}`),
}));

import {
  createCapture,
  fetchCaptures,
  reviewCapture,
} from "@/lib/api/captures";
import { getApiUrl } from "@/lib/config";

const mockedFetch = global.fetch as jest.Mock;
const mockedGetApiUrl = getApiUrl as jest.Mock;

const ok = (body: unknown) => ({
  status: 200,
  ok: true,
  json: jest.fn().mockResolvedValueOnce(body),
});

beforeEach(() => {
  mockedFetch.mockReset();
  mockedGetApiUrl.mockImplementation((path: string) => `/api${path}`);
});

describe("fetchCaptures", () => {
  it("GETs /api/captures and returns the list payload", async () => {
    const payload = {
      captures: [{ id: "cap-1", content: "A thought.", source: "manual" }],
      total: 1,
    };
    mockedFetch.mockResolvedValueOnce(ok(payload));

    const result = await fetchCaptures();

    expect(mockedFetch).toHaveBeenCalledWith(
      "/api/captures",
      expect.objectContaining({ credentials: "include" }),
    );
    expect(result.total).toBe(1);
    expect(result.captures[0].id).toBe("cap-1");
  });

  it("passes filters as query params", async () => {
    mockedFetch.mockResolvedValueOnce(ok({ captures: [], total: 0 }));

    await fetchCaptures({ review_status: "pending", source: "web", limit: 5 });

    const url = mockedFetch.mock.calls[0][0] as string;
    expect(url).toContain("review_status=pending");
    expect(url).toContain("source=web");
    expect(url).toContain("limit=5");
  });
});

describe("createCapture", () => {
  it("POSTs content and returns the capture result", async () => {
    const payload = { success: true, id: "cap-2", deduped: false };
    mockedFetch.mockResolvedValueOnce(ok(payload));

    const result = await createCapture({ content: "New thought." });

    const [url, options] = mockedFetch.mock.calls[0];
    expect(url).toBe("/api/captures");
    expect(options.method).toBe("POST");
    expect(JSON.parse(options.body).content).toBe("New thought.");
    expect(result.id).toBe("cap-2");
  });
});

describe("reviewCapture", () => {
  it("POSTs the action to the review endpoint", async () => {
    mockedFetch.mockResolvedValueOnce(
      ok({ success: true, review_applied: true, gate_response: "allow" }),
    );

    const result = await reviewCapture("cap-1", "confirm", "scott");

    const [url, options] = mockedFetch.mock.calls[0];
    expect(url).toBe("/api/captures/cap-1/review");
    expect(options.method).toBe("POST");
    expect(JSON.parse(options.body)).toEqual({
      action: "confirm",
      actor: "scott",
    });
    expect(result.review_applied).toBe(true);
  });

  it("passes superseded_by for supersede actions", async () => {
    mockedFetch.mockResolvedValueOnce(ok({ success: true }));

    await reviewCapture("cap-1", "supersede", undefined, "cap-2");

    const body = JSON.parse(mockedFetch.mock.calls[0][1].body);
    expect(body.action).toBe("supersede");
    expect(body.superseded_by).toBe("cap-2");
  });
});
