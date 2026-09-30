/**
 * Meetings page: lists meetings with purpose, key points and signal counts,
 * searches through the API, and opens the meeting viewer on click.
 */

import { render, screen, fireEvent, waitFor, act } from "@testing-library/react";
import MeetingsPage from "@/app/(protected)/meetings/page";
import { fetchMeetingHistoryList, fetchMeetingHistoryStats } from "@/lib/api/meetings";

jest.mock("@/lib/api/meetings", () => ({
  fetchMeetingHistoryList: jest.fn(),
  fetchMeetingHistoryStats: jest.fn(),
}));

jest.mock("@/contexts/DomainContext", () => ({
  useDomain: () => ({ getNavLabel: (_g: string, _p: string, fallback: string) => fallback }),
}));

jest.mock("@/components/meetings/MeetingViewer", () => ({
  __esModule: true,
  default: ({ botId, open }: { botId: string; open?: boolean }) =>
    open ? <div data-testid="viewer">{botId}</div> : null,
}));

const listMock = fetchMeetingHistoryList as jest.Mock;
const statsMock = fetchMeetingHistoryStats as jest.Mock;

const item = (over: Record<string, unknown> = {}) => ({
  id: "obs-1",
  bot_id: "ingest-new",
  title: "Northwind planning",
  start_time: "2026-09-28T17:30:00+00:00",
  time_source: "explicit",
  participants: ["Sarah Chen", "David Kim"],
  attendee_count: 2,
  purpose: "Plan the Northwind work.",
  key_points: ["Northwind is next"],
  summarized: true,
  has_transcript: true,
  lane: "record",
  signal_counts: { decision: 1, action_item: 2, key_point: 0, insight: 0 },
  ...over,
});

beforeEach(() => {
  listMock.mockReset();
  statsMock.mockReset();
  statsMock.mockResolvedValue({
    total_meetings: 2, meetings_with_transcripts: 2, meetings_summarized: 1,
    total_signals: 3, first_meeting: null, last_meeting: null,
  });
  listMock.mockResolvedValue({
    items: [item(), item({ bot_id: "ingest-old", title: "Globex check in", summarized: false,
      purpose: null, key_points: [], signal_counts: { decision: 0, action_item: 0, key_point: 0, insight: 0 } })],
    total: 2, next_cursor: null, page_size: 50,
  });
});

test("renders meetings with purpose, key points and counts", async () => {
  render(<MeetingsPage />);
  expect(await screen.findByText("Northwind planning")).toBeInTheDocument();
  expect(screen.getByText("Plan the Northwind work.")).toBeInTheDocument();
  expect(screen.getByText("Northwind is next")).toBeInTheDocument();
  expect(screen.getByText("1 decisions, 2 actions")).toBeInTheDocument();
  expect(screen.getByText("Transcript only")).toBeInTheDocument();
  expect(await screen.findByText("2 meetings · 1 summarized · 3 signals")).toBeInTheDocument();
});

test("clicking a meeting opens the viewer", async () => {
  render(<MeetingsPage />);
  fireEvent.click(await screen.findByText("Globex check in"));
  expect(screen.getByTestId("viewer")).toHaveTextContent("ingest-old");
});

test("search queries the API", async () => {
  render(<MeetingsPage />);
  await screen.findByText("Northwind planning");
  fireEvent.change(screen.getByLabelText("Search meetings"), { target: { value: "globex" } });
  await waitFor(() =>
    expect(listMock).toHaveBeenLastCalledWith(expect.objectContaining({ q: "globex" })),
  );
});

test("a stale search response does not replace the latest results", async () => {
  let resolveOld: (v: unknown) => void = () => {};
  render(<MeetingsPage />);
  await screen.findByText("Northwind planning");
  listMock.mockImplementationOnce(() => new Promise((r) => { resolveOld = r; }));
  listMock.mockResolvedValueOnce({
    items: [item({ bot_id: "g", title: "Globex only" })], total: 1, next_cursor: null, page_size: 50,
  });
  fireEvent.change(screen.getByLabelText("Search meetings"), { target: { value: "glo" } });
  await waitFor(() => expect(listMock).toHaveBeenLastCalledWith(expect.objectContaining({ q: "glo" })));
  fireEvent.change(screen.getByLabelText("Search meetings"), { target: { value: "globex" } });
  expect(await screen.findByText("Globex only")).toBeInTheDocument();
  await act(async () => {
    resolveOld({ items: [item({ bot_id: "o", title: "Old query row" })], total: 1, next_cursor: null, page_size: 50 });
  });
  expect(screen.queryByText("Old query row")).not.toBeInTheDocument();
  expect(screen.getByText("Globex only")).toBeInTheDocument();
});
