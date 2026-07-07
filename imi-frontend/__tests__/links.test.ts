/**
 * Guard test for lib/utils/links.tsx — imports the REAL module (no jest.mock).
 *
 * Background: a navigation refactor imported `KNOWN_ROUTES` from this module.
 * If the constant is only a function-local `const` and not exported, the named
 * import resolves to `undefined` at runtime, so `for (const r of KNOWN_ROUTES)`
 * throws "KNOWN_ROUTES is not iterable", crashing every page in the (protected)
 * layout. navigation.test.tsx mocks this module and supplies its own
 * KNOWN_ROUTES, so it never exercises the real export — exactly the gap that let
 * this class of bug ship in the open-core mirror (imi).
 *
 * These assertions deliberately do NOT mock @/lib/utils/links — they verify
 * the real module's public contract so the missing-export regression can't
 * come back silently.
 */

import { KNOWN_ROUTES } from "@/lib/utils/links";

describe("lib/utils/links — KNOWN_ROUTES export contract", () => {
  it("exports KNOWN_ROUTES as a non-empty iterable array", () => {
    expect(Array.isArray(KNOWN_ROUTES)).toBe(true);
    expect(KNOWN_ROUTES.length).toBeGreaterThan(0);
    // `for (const route of KNOWN_ROUTES)` must not throw — this is the exact
    // operation that crashed the protected layout when the export was missing.
    expect(() => {
      for (const _route of KNOWN_ROUTES) {
        void _route;
      }
    }).not.toThrow();
  });

  it("every route is an absolute path string", () => {
    for (const route of KNOWN_ROUTES) {
      expect(typeof route).toBe("string");
      expect(route.startsWith("/")).toBe(true);
    }
  });

  it("includes the redesigned top-level routes", () => {
    for (const route of ["/overview", "/decisions", "/profile"]) {
      expect(KNOWN_ROUTES).toContain(route);
    }
  });
});
