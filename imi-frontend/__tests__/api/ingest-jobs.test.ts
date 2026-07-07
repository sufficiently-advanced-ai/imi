/**
 * Tests for fetchIngestJobs API client
 * GET /api/ingest/jobs — returns job list sorted by created_at desc
 */

import { fetchIngestJobs } from "@/lib/api/ingest";

// Mock the fetcher from the API index
jest.mock("@/lib/api/index", () => ({
  fetcher: jest.fn(),
}));

import { fetcher } from "@/lib/api/index";

const mockedFetcher = fetcher as jest.Mock;

describe("fetchIngestJobs", () => {
  beforeEach(() => {
    mockedFetcher.mockReset();
  });

  it("calls fetcher with /ingest/jobs and returns the job list", async () => {
    const mockJobs = [
      { job_id: "job-1", status: "completed", created_at: "2026-06-12T10:00:00Z", title: "Meeting A" },
      { job_id: "job-2", status: "pending", created_at: "2026-06-12T09:00:00Z", title: null },
    ];
    mockedFetcher.mockResolvedValueOnce(mockJobs);

    const result = await fetchIngestJobs();

    expect(mockedFetcher).toHaveBeenCalledTimes(1);
    expect(mockedFetcher).toHaveBeenCalledWith("/ingest/jobs");
    expect(result).toEqual(mockJobs);
  });

  it("returns an empty array when no jobs exist", async () => {
    mockedFetcher.mockResolvedValueOnce([]);

    const result = await fetchIngestJobs();

    expect(result).toEqual([]);
  });

  it("propagates errors from fetcher", async () => {
    const error = new Error("Network failure");
    mockedFetcher.mockRejectedValueOnce(error);

    await expect(fetchIngestJobs()).rejects.toThrow("Network failure");
  });
});
