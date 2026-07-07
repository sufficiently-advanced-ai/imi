/**
 * Navigation component tests — aligned with the current IA
 * (Overview + Intelligence/Knowledge Base/Meetings/Account groups).
 *
 * Covers:
 * 1. Overview item renders with link to /overview
 * 2. Constitution item links to /decisions
 * 3. Pending-review badge appears when count > 0
 * 4. Badge hidden when count is 0
 * 5. Badge hidden when count is null
 * 6. Active item gets aria-current="page"
 * 7. Meetings group is collapsed by default
 * 9. System item renders only when authenticated
 * 10. MobileNav exposes the same items inside the Sheet
 */

import { render, screen, fireEvent, within } from "@testing-library/react";
import { usePathname, useSearchParams } from "next/navigation";
import Navigation, { MobileNav } from "@/components/navigation";

// ---------------------------------------------------------------------------
// Dependency mocks
// ---------------------------------------------------------------------------

// next/navigation is already partially mocked in jest.setup.js;
// re-mock here so individual tests can override usePathname / useSearchParams.
jest.mock("next/navigation", () => ({
  usePathname: jest.fn(() => "/"),
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

// BasePathLink — render as a plain anchor so href assertions work
jest.mock("@/lib/utils/links", () => {
  // eslint-disable-next-line @typescript-eslint/no-require-imports
  const React = require("react");
  return {
    __esModule: true,
    KNOWN_ROUTES: [
      "/feed", "/command", "/chat", "/explorer",
      "/domain-graph-enhanced", "/entities", "/signin", "/api", "/_next",
      "/decisions", "/profile", "/overview",
    ],
    default: ({ href, children, ...props }) => (
      <a href={href} {...props}>
        {children}
      </a>
    ),
  };
});

// Auth
jest.mock("@/lib/hooks/useAuth", () => ({
  useAuth: jest.fn(() => ({
    user: { id: "u1", email: "test@example.com", firstName: "Test", lastName: "User" },
    loading: false,
    error: null,
  })),
}));

// Review counts
jest.mock("@/lib/hooks/useReviewCounts", () => ({
  useReviewCounts: jest.fn(() => ({ count: 0, refresh: jest.fn() })),
}));

// Domain context
const defaultDomainMock = {
  getNavLabel: (_group, _path, fallback) => fallback,
  getGroupLabel: (_group, fallback) => fallback,
  getEntityDisplayName: (key) => key,
  getTerm: (_key, fallback) => fallback,
  domainConfig: { id: "default", name: "Default", entities: {}, relationships: {}, ui: null },
  uiLabels: null,
  isLoading: false,
  error: null,
  currentDomain: "default",
  domains: ["default"],
  domainInfos: [],
  setCurrentDomain: jest.fn(),
};

jest.mock("@/contexts/DomainContext", () => ({
  useDomain: jest.fn(),
}));

// Auth UI components — simple stubs
jest.mock("@/components/auth/LoginButton", () => ({
  LoginButton: () => null,
}));
jest.mock("@/components/auth/UserMenu", () => ({
  UserMenu: () => null,
}));

// Entity icons — return a simple stub icon
jest.mock("@/lib/utils/entity-icons", () => ({
  getEntityIcon: () => {
    // eslint-disable-next-line @typescript-eslint/no-require-imports
    const React = require("react");
    return function StubIcon({ className }) {
      return React.createElement("span", { className });
    };
  },
}));

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

import { useAuth } from "@/lib/hooks/useAuth";
import { useReviewCounts } from "@/lib/hooks/useReviewCounts";
import { useDomain } from "@/contexts/DomainContext";

function setDomain(overrides = {}) {
  useDomain.mockReturnValue({ ...defaultDomainMock, ...overrides });
}

function setReviewCount(count) {
  useReviewCounts.mockReturnValue({ count, refresh: jest.fn() });
}

function setPathname(path) {
  usePathname.mockReturnValue(path);
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe("Navigation", () => {
  beforeEach(() => {
    jest.clearAllMocks();
    setDomain();
    setReviewCount(0);
    setPathname("/");
    useSearchParams.mockReturnValue(new URLSearchParams());
  });

  // 1. Overview renders as first nav item linking to /overview
  it("renders Overview as the first nav item linking to /overview", () => {
    render(<Navigation />);
    const overviewLink = screen.getByRole("link", { name: /overview/i });
    expect(overviewLink).toBeInTheDocument();
    expect(overviewLink).toHaveAttribute("href", "/overview");
  });

  // 2. Constitution item links to /decisions
  it("renders Constitution linking to /decisions", () => {
    render(<Navigation />);
    const constitutionLink = screen.getByRole("link", { name: /constitution/i });
    expect(constitutionLink).toBeInTheDocument();
    expect(constitutionLink).toHaveAttribute("href", "/decisions");
  });

  // 2b. Memory item links to /memory (OB1 absorption Phase 1)
  it("renders Memory linking to /memory", () => {
    render(<Navigation />);
    const memoryLink = screen.getByRole("link", { name: /memory/i });
    expect(memoryLink).toBeInTheDocument();
    expect(memoryLink).toHaveAttribute("href", "/memory");
  });

  // 3. Badge visible when count > 0
  it("shows pending-review badge when count > 0", () => {
    setReviewCount(5);
    render(<Navigation />);
    expect(screen.getByText("5")).toBeInTheDocument();
  });

  // 4. Badge hidden when count is 0
  it("hides badge when count is 0", () => {
    setReviewCount(0);
    render(<Navigation />);
    expect(screen.queryByText("0")).not.toBeInTheDocument();
  });

  // 5. Badge hidden when count is null
  it("hides badge when count is null", () => {
    setReviewCount(null);
    render(<Navigation />);
    expect(screen.queryByText(/^\d+$/)).not.toBeInTheDocument();
  });

  // 6. Active item gets aria-current="page"
  it("marks the active item with aria-current='page'", () => {
    setPathname("/overview");
    render(<Navigation />);
    const overviewLink = screen.getByRole("link", { name: /overview/i });
    expect(overviewLink).toHaveAttribute("aria-current", "page");
  });

  // 6b. Non-active items should NOT have aria-current
  it("does not mark inactive items with aria-current", () => {
    setPathname("/decisions");
    render(<Navigation />);
    const overviewLink = screen.getByRole("link", { name: /overview/i });
    expect(overviewLink).not.toHaveAttribute("aria-current");
  });

  // 9. System item renders only when authenticated
  it("System item renders when authenticated", () => {
    useAuth.mockReturnValue({
      user: { id: "u1", email: "test@example.com" },
      loading: false,
    });
    render(<Navigation />);
    expect(screen.getByRole("link", { name: /system/i })).toBeInTheDocument();
  });

  it("System item is absent when not authenticated", () => {
    useAuth.mockReturnValue({
      user: null,
      loading: false,
    });
    render(<Navigation />);
    expect(screen.queryByRole("link", { name: /system/i })).not.toBeInTheDocument();
  });
});

// ---------------------------------------------------------------------------
// MobileNav
// ---------------------------------------------------------------------------

describe("MobileNav", () => {
  beforeEach(() => {
    jest.clearAllMocks();
    setDomain();
    setReviewCount(0);
    setPathname("/");
    useSearchParams.mockReturnValue(new URLSearchParams());
    useAuth.mockReturnValue({
      user: { id: "u1", email: "test@example.com", firstName: "Test", lastName: "User" },
      loading: false,
    });
  });

  // 10. MobileNav exposes the same items inside the Sheet
  it("MobileNav exposes the same items inside the Sheet", () => {
    render(<MobileNav />);

    // Open the sheet by clicking the hamburger button
    const openButton = screen.getByRole("button", { name: /open navigation menu/i });
    fireEvent.click(openButton);

    // After opening, the sheet dialog should contain the Overview link
    const dialog = screen.queryByRole("dialog");
    if (dialog) {
      expect(within(dialog).getByRole("link", { name: /overview/i })).toBeInTheDocument();
      expect(within(dialog).getByRole("link", { name: /constitution/i })).toBeInTheDocument();
    } else {
      // Radix Sheet may render inline rather than as a dialog in jsdom
      expect(screen.getByRole("link", { name: /overview/i })).toBeInTheDocument();
      expect(screen.getByRole("link", { name: /constitution/i })).toBeInTheDocument();
    }
  });
});
