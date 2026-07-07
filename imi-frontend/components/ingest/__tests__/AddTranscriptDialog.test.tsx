import React from "react";
import { render, screen, act } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { AddTranscriptDialog } from "../AddTranscriptDialog";

// ---------------------------------------------------------------------------
// Mocks
// ---------------------------------------------------------------------------

// Capture the onBusyChange prop so tests can drive busy state
let capturedOnBusyChange: ((busy: boolean) => void) | undefined;

jest.mock("../IngestTranscriptFlow", () => ({
  IngestTranscriptFlow: jest.fn(
    ({ onBusyChange }: { onBusyChange?: (busy: boolean) => void }) => {
      capturedOnBusyChange = onBusyChange;
      return <div data-testid="ingest-transcript-flow" />;
    }
  ),
}));

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe("AddTranscriptDialog", () => {
  beforeEach(() => {
    capturedOnBusyChange = undefined;
    jest.restoreAllMocks();
  });

  it("opens sheet from default trigger", async () => {
    const user = userEvent.setup();
    render(<AddTranscriptDialog />);

    const trigger = screen.getByRole("button", { name: /add transcript/i });
    await user.click(trigger);

    // Sheet is open: the SheetTitle heading is visible
    expect(screen.getByRole("heading", { name: /add transcript/i })).toBeInTheDocument();
    expect(screen.getByTestId("ingest-transcript-flow")).toBeInTheDocument();
  });

  it("close while busy prompts confirm and stays open on decline", async () => {
    const user = userEvent.setup();
    jest.spyOn(window, "confirm").mockReturnValue(false);

    render(<AddTranscriptDialog />);

    // Open sheet
    await user.click(screen.getByRole("button", { name: /add transcript/i }));
    expect(screen.getByRole("heading", { name: /add transcript/i })).toBeInTheDocument();

    // Signal busy state
    act(() => {
      capturedOnBusyChange?.(true);
    });

    // Try to close via the Sheet's built-in close button (the X button)
    const closeButton = screen.getByRole("button", { name: /close/i });
    await user.click(closeButton);

    expect(window.confirm).toHaveBeenCalledTimes(1);
    // Sheet should still be open (confirm returned false)
    expect(screen.getByRole("heading", { name: /add transcript/i })).toBeInTheDocument();
  });

  it("close while busy proceeds on confirm", async () => {
    const user = userEvent.setup();
    jest.spyOn(window, "confirm").mockReturnValue(true);

    render(<AddTranscriptDialog />);

    // Open sheet
    await user.click(screen.getByRole("button", { name: /add transcript/i }));
    expect(screen.getByRole("heading", { name: /add transcript/i })).toBeInTheDocument();

    // Signal busy state
    act(() => {
      capturedOnBusyChange?.(true);
    });

    // Try to close via the Sheet's built-in close button
    const closeButton = screen.getByRole("button", { name: /close/i });
    await user.click(closeButton);

    expect(window.confirm).toHaveBeenCalledTimes(1);
    // Sheet should now be closed — heading is gone
    expect(screen.queryByRole("heading", { name: /add transcript/i })).not.toBeInTheDocument();
  });

  it("close while idle does not prompt", async () => {
    const user = userEvent.setup();
    const confirmSpy = jest.spyOn(window, "confirm");

    render(<AddTranscriptDialog />);

    // Open sheet
    await user.click(screen.getByRole("button", { name: /add transcript/i }));
    expect(screen.getByRole("heading", { name: /add transcript/i })).toBeInTheDocument();

    // Do NOT signal busy — default is idle (busyRef.current = false)

    // Close via the X button
    const closeButton = screen.getByRole("button", { name: /close/i });
    await user.click(closeButton);

    expect(confirmSpy).not.toHaveBeenCalled();
    expect(screen.queryByRole("heading", { name: /add transcript/i })).not.toBeInTheDocument();
  });
});
