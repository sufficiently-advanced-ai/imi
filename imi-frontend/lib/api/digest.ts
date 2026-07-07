/**
 * Digest / Brief markdown API clients.
 *
 * These endpoints return text/markdown (not JSON), so they cannot use the
 * shared `fetcher` (which calls response.json()). They follow the same auth
 * and error conventions as `fetcher` in lib/api/index.ts.
 *
 * IMPORTANT: paths must NOT start with /api/ — getApiUrl() already prepends
 * /api, so a leading /api/ would produce a double-prefix (/api/api/...).
 */

import { getApiUrl } from "@/lib/config";

/**
 * Internal helper: fetch a markdown endpoint.
 * - 200 → return text
 * - 404 → return null (not-yet-generated is a normal state)
 * - 401 → redirect to /signin, throw with status=401 (parity with fetcher)
 * - other non-ok → throw with status code in message
 */
async function fetchMarkdown(path: string): Promise<string | null> {
  const response = await fetch(getApiUrl(path), { credentials: "include" });

  if (response.status === 404) {
    return null;
  }

  if (response.status === 401) {
    if (typeof window !== "undefined") {
      window.location.href = "/signin"; // known basePath quirk, do not fix
    }
    const err = new Error("Unauthorized") as Error & { status?: number };
    err.status = 401;
    throw err;
  }

  if (!response.ok) {
    throw new Error(`Request failed: ${response.status}`);
  }

  return response.text();
}

/**
 * Fetch the latest weekly digest as markdown.
 * Backend: GET /api/digest/weekly/latest (app/routes/digest.py:49)
 * Returns null when no digest has been generated yet (404).
 */
export const fetchLatestWeeklyDigest = (): Promise<string | null> =>
  fetchMarkdown("/digest/weekly/latest");
