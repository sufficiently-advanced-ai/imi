/**
 * Regression tests for useAuth (PR #5 review):
 * - error state must clear when a later poll succeeds (recovered session
 *   otherwise still looks failed to consumers like ProtectedLayout)
 * - in-flight /auth/me fetch must be aborted on unmount
 */

import { renderHook, act } from "@testing-library/react";
import { useAuth } from "@/lib/hooks/useAuth";

const okResponse = (body: unknown) => ({
  ok: true,
  status: 200,
  json: async () => body,
});

const originalFetch = global.fetch;

afterEach(() => {
  global.fetch = originalFetch;
  jest.useRealTimers();
  jest.restoreAllMocks();
});

test("clears error when a later poll succeeds", async () => {
  jest.useFakeTimers();
  jest.spyOn(console, "error").mockImplementation(() => {});
  const fetchMock = jest
    .fn()
    .mockRejectedValueOnce(new Error("network down"))
    .mockResolvedValue(
      okResponse({ id: "u1", email: "u@example.com", first_name: "U", last_name: "One" })
    );
  global.fetch = fetchMock as unknown as typeof fetch;

  const { result } = renderHook(() => useAuth());

  await act(async () => {});
  expect(result.current.error).toBeTruthy();
  expect(result.current.user).toBeNull();

  await act(async () => {
    jest.advanceTimersByTime(4 * 60 * 1000);
  });
  expect(result.current.user?.id).toBe("u1");
  expect(result.current.error).toBeNull();
});

test("aborts the in-flight request on unmount", () => {
  let capturedSignal: AbortSignal | null | undefined;
  global.fetch = jest.fn((_url: unknown, opts?: RequestInit) => {
    capturedSignal = opts?.signal;
    return new Promise(() => {}); // never settles
  }) as unknown as typeof fetch;

  const { unmount } = renderHook(() => useAuth());
  unmount();

  expect(capturedSignal).toBeDefined();
  expect(capturedSignal?.aborted).toBe(true);
});
