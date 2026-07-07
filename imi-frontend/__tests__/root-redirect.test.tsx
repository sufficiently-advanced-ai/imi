/**
 * Tests for D6: root page redirects to ./overview/ instead of ./feed/.
 *
 * Covers:
 *  - window.location.replace is called with './overview/'
 *  - the old './feed/' target is NOT called
 */

import React from "react";
import { render } from "@testing-library/react";

// Spy on window.location.replace before importing the module so React's
// useEffect fires with our spy in place.  jsdom provides window.location as a
// non-configurable object, so we replace the whole property with a writable
// copy then restore it afterwards.

const originalLocation = window.location;

beforeAll(() => {
  Object.defineProperty(window, "location", {
    configurable: true,
    writable: true,
    value: { ...originalLocation, replace: jest.fn() },
  });
});

afterAll(() => {
  Object.defineProperty(window, "location", {
    configurable: true,
    writable: true,
    value: originalLocation,
  });
});

beforeEach(() => {
  (window.location.replace as jest.Mock).mockClear();
  // Advance timers so the 100ms setTimeout fallback also fires
  jest.useFakeTimers();
});

afterEach(() => {
  jest.useRealTimers();
});

// Import AFTER setting up the spy so that the module-level setTimeout (which
// runs synchronously during import when typeof window !== 'undefined') captures
// our mock.
// eslint-disable-next-line @typescript-eslint/no-require-imports
const Home = require("@/app/page").default;

describe("Root page redirect", () => {
  it("calls window.location.replace with './overview/'", () => {
    render(<Home />);
    // Advance timers to trigger the 100 ms fallback
    jest.advanceTimersByTime(200);

    expect(window.location.replace).toHaveBeenCalledWith("./overview/");
  });

  it("does NOT redirect to './feed/'", () => {
    render(<Home />);
    jest.advanceTimersByTime(200);

    const calls = (window.location.replace as jest.Mock).mock.calls.flat();
    expect(calls).not.toContain("./feed/");
  });
});
