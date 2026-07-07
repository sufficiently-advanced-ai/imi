/**
 * Tests for digest/brief markdown API clients
 * - fetchLatestWeeklyDigest → GET /api/digest/weekly/latest (text/markdown)
 */

// jest.setup.js already sets global.fetch = jest.fn(), so we just reset it.
// We need to mock @/lib/config so getApiUrl is predictable.
jest.mock("@/lib/config", () => ({
  getApiUrl: jest.fn((path: string) => `/api${path}`),
}));

import { fetchLatestWeeklyDigest } from "@/lib/api/digest";
import { getApiUrl } from "@/lib/config";

const mockedFetch = global.fetch as jest.Mock;
const mockedGetApiUrl = getApiUrl as jest.Mock;

describe("fetchLatestWeeklyDigest", () => {
  beforeEach(() => {
    mockedFetch.mockReset();
    mockedGetApiUrl.mockImplementation((path: string) => `/api${path}`);
  });

  it("returns markdown text on 200", async () => {
    const markdown = "# Weekly Digest\n\nSome content here.";
    mockedFetch.mockResolvedValueOnce({
      status: 200,
      ok: true,
      text: jest.fn().mockResolvedValueOnce(markdown),
    });

    const result = await fetchLatestWeeklyDigest();

    expect(mockedFetch).toHaveBeenCalledWith("/api/digest/weekly/latest", {
      credentials: "include",
    });
    expect(result).toBe(markdown);
  });

  it("returns null on 404", async () => {
    mockedFetch.mockResolvedValueOnce({
      status: 404,
      ok: false,
      text: jest.fn(),
    });

    const result = await fetchLatestWeeklyDigest();

    expect(result).toBeNull();
  });

  it("redirects to /signin and throws on 401", async () => {
    const originalLocation = window.location;
    // Allow assignment by deleting and redefining
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    delete (window as any).location;
    window.location = { href: "" } as Location;

    mockedFetch.mockResolvedValueOnce({
      status: 401,
      ok: false,
      text: jest.fn(),
    });

    await expect(fetchLatestWeeklyDigest()).rejects.toMatchObject({
      message: "Unauthorized",
      status: 401,
    });
    expect(window.location.href).toBe("/signin");

    // Restore
    window.location = originalLocation;
  });

  it("throws on other non-ok responses", async () => {
    mockedFetch.mockResolvedValueOnce({
      status: 500,
      ok: false,
      text: jest.fn(),
    });

    await expect(fetchLatestWeeklyDigest()).rejects.toThrow("Request failed: 500");
  });
});
