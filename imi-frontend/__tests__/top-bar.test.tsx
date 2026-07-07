import React from "react";
import { render, screen } from "@testing-library/react";
import { TopBar } from "@/components/top-bar";

// ---------------------------------------------------------------------------
// Mocks
// ---------------------------------------------------------------------------

jest.mock("@/components/navigation", () => ({
  MobileNav: () => <div data-testid="mobile-nav" />,
}));

jest.mock("@/components/ingest/AddTranscriptDialog", () => ({
  AddTranscriptDialog: () => (
    <button data-testid="add-transcript-dialog">Add transcript</button>
  ),
}));

jest.mock("@/components/theme-toggle", () => ({
  ThemeToggle: () => <div data-testid="theme-toggle" />,
}));

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe("TopBar", () => {
  it("renders without crashing", () => {
    render(<TopBar />);
    expect(screen.getByRole("banner")).toBeInTheDocument();
  });

  it("renders the MobileNav wrapper", () => {
    render(<TopBar />);
    expect(screen.getByTestId("mobile-nav")).toBeInTheDocument();
  });

  it("renders the AddTranscriptDialog trigger", () => {
    render(<TopBar />);
    expect(screen.getByTestId("add-transcript-dialog")).toBeInTheDocument();
  });

  it("renders the ThemeToggle", () => {
    render(<TopBar />);
    expect(screen.getByTestId("theme-toggle")).toBeInTheDocument();
  });

  it("MobileNav is inside the md:hidden container", () => {
    const { container } = render(<TopBar />);
    const mobileNavWrapper = container.querySelector(".md\\:hidden");
    expect(mobileNavWrapper).toBeInTheDocument();
    expect(mobileNavWrapper).toContainElement(screen.getByTestId("mobile-nav"));
  });
});
