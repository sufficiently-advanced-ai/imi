#!/usr/bin/env python3
"""Export Littlebird meetings (transcript + metadata) to normalized JSON files.

Talks to Littlebird's remote MCP server (https://mcp.littlebird.ai/mcp) with the
MCP SDK's OAuth client — the first run opens a browser for consent, tokens are
cached in ~/.config/imi/littlebird-oauth.json (0600).

Output: one <littlebird_id>.json per recorded meeting in --out, shaped for
POST /api/ingest (see scripts/littlebird_ingest.py):

    {littlebird_id, title, original_title, start_time, time_source,
     participants, transcript, summary, raw_transcript}

Why the normalization:
  * Littlebird labels the recording user "[You]" — rewritten to --self-name so
    the call is attributed to a real Person entity.
  * Lines are "[Speaker]: text"; rewritten to "Speaker: text" (no [mm:ss]
    markers exist, and a bracketed name would look like a timestamp marker).
  * Meetings without a linked calendar event have no time/attendees in
    Littlebird. start_time then comes from the earliest search-chunk timestamp
    (time_source="transcript_chunk"); participants from speakers + the title.

Usage:
    uv run --with mcp --with httpx scripts/littlebird_export.py --out ~/Data/littlebird-export
"""

import argparse
import asyncio
import json
import os
import re
import threading
import unicodedata
import webbrowser
from datetime import date, datetime, timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from mcp import ClientSession
from mcp.client.auth import OAuthClientProvider
from mcp.client.streamable_http import create_mcp_http_client, streamable_http_client
from mcp.shared.auth import (
    AuthorizationCodeResult,
    OAuthClientInformationFull,
    OAuthClientMetadata,
    OAuthToken,
)

SERVER_URL = "https://mcp.littlebird.ai/mcp"
CALLBACK_PORT = 33418
REDIRECT_URI = f"http://localhost:{CALLBACK_PORT}/callback"
TOKEN_FILE = Path.home() / ".config" / "imi" / "littlebird-oauth.json"

UUID_RE = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
# Speaker labels that are not people.
NON_PERSON_SPEAKERS = {"others", "unknown", "speaker"}
UNIDENTIFIED_SPEAKER = "Unidentified speaker"


# --- OAuth plumbing -------------------------------------------------------


class FileTokenStorage:
    def __init__(self, path: Path):
        self.path = path

    def _load(self) -> dict:
        try:
            return json.loads(self.path.read_text())
        except (FileNotFoundError, json.JSONDecodeError):
            return {}

    def _save(self, data: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2))
        os.chmod(tmp, 0o600)
        tmp.replace(self.path)

    async def get_tokens(self) -> OAuthToken | None:
        t = self._load().get("tokens")
        return OAuthToken.model_validate(t) if t else None

    async def set_tokens(self, tokens: OAuthToken) -> None:
        data = self._load()
        data["tokens"] = tokens.model_dump(mode="json", exclude_none=True)
        self._save(data)

    async def get_client_info(self) -> OAuthClientInformationFull | None:
        c = self._load().get("client_info")
        return OAuthClientInformationFull.model_validate(c) if c else None

    async def set_client_info(self, client_info: OAuthClientInformationFull) -> None:
        data = self._load()
        data["client_info"] = client_info.model_dump(mode="json", exclude_none=True)
        self._save(data)


class _CallbackCatcher:
    """One-shot localhost HTTP server that captures the OAuth redirect."""

    def __init__(self):
        self.result: dict[str, str] = {}
        self.done = threading.Event()
        catcher = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802
                qs = parse_qs(urlparse(self.path).query)
                catcher.result = {k: v[0] for k, v in qs.items()}
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.end_headers()
                self.wfile.write(b"<h3>Littlebird authorized. You can close this tab.</h3>")
                catcher.done.set()

            def log_message(self, *args):
                pass

        self.server = HTTPServer(("localhost", CALLBACK_PORT), Handler)

    async def wait(self) -> AuthorizationCodeResult:
        thread = threading.Thread(target=self.server.handle_request, daemon=True)
        thread.start()
        await asyncio.to_thread(self.done.wait, 300)
        self.server.server_close()
        if "code" not in self.result:
            raise RuntimeError(f"OAuth callback without code: {self.result}")
        return AuthorizationCodeResult(
            code=self.result["code"], state=self.result.get("state"), iss=self.result.get("iss")
        )


def build_auth() -> OAuthClientProvider:
    catcher_holder: dict[str, _CallbackCatcher] = {}

    async def redirect_handler(url: str) -> None:
        catcher_holder["c"] = _CallbackCatcher()
        print(f"\nOpening browser for Littlebird consent:\n  {url}\n")
        webbrowser.open(url)

    async def callback_handler() -> AuthorizationCodeResult:
        return await catcher_holder["c"].wait()

    return OAuthClientProvider(
        server_url=SERVER_URL,
        client_metadata=OAuthClientMetadata(
            client_name="imi littlebird export",
            redirect_uris=[REDIRECT_URI],
            grant_types=["authorization_code", "refresh_token"],
            response_types=["code"],
        ),
        storage=FileTokenStorage(TOKEN_FILE),
        redirect_handler=redirect_handler,
        callback_handler=callback_handler,
    )


async def call(session: ClientSession, tool: str, **args) -> str:
    res = await session.call_tool(tool, args)
    text = "".join(getattr(c, "text", "") for c in res.content)
    # Tools return {"result": "..."} as JSON text.
    try:
        text = json.loads(text).get("result", text)
    except (json.JSONDecodeError, AttributeError):
        pass
    if getattr(res, "isError", False):
        raise RuntimeError(f"{tool} failed: {text[:300]}")
    return text


# --- Parsing ----------------------------------------------------------------


def parse_id_list(text: str) -> list[tuple[str, str]]:
    """(name, id) pairs from the trailing 'Meeting ids' section."""
    return re.findall(rf"^- (.*) \(id: ({UUID_RE})\)$", text, flags=re.M)


def parse_event(detail: str) -> tuple[str | None, str | None, list[str]]:
    """start, end, attendee strings from a GET_MEETING result (linked events only)."""
    m = re.search(r"Event time: (\S+) to (\S+)", detail)
    start, end = (m.group(1), m.group(2)) if m else (None, None)
    a = re.search(r"Event attendees: (.*)", detail)
    attendees = []
    if a and a.group(1).strip() != "N/A":
        attendees = [x.strip() for x in a.group(1).split(",") if x.strip()]
    return start, end, attendees


def parse_summary(detail: str) -> str:
    i = detail.find("Summary:")
    return detail[i + len("Summary:"):].strip() if i >= 0 else ""


def chunk_start_times(search_text: str) -> dict[str, str]:
    """Earliest chunk 'Start timestamp' per meeting id in a SEARCH_MEETINGS result.

    Meeting blocks appear in the same order as the trailing id list.
    """
    ids = [i for _, i in parse_id_list(search_text)] or re.findall(
        rf"^- id: ({UUID_RE})$", search_text, flags=re.M
    )
    body = search_text.split("\nMeeting ids", 1)[0]
    blocks = re.split(r"^#### Meeting name: ", body, flags=re.M)[1:]
    out: dict[str, str] = {}
    for mid, block in zip(ids, blocks):
        stamps = re.findall(r"Start timestamp: (\S+)", block)
        if stamps:
            out[mid] = min(stamps, key=lambda s: datetime.fromisoformat(s))
    return out


def people_from_title(title: str) -> list[str]:
    names = []
    m = re.search(r"\(([^)]+)\)\s*$", title)
    if m and "VIP" in title:
        names.append(m.group(1).strip())
    m = re.match(r"(.+?)'s Personal Calendar", title)
    if m:
        names.append(m.group(1).strip())
    m = re.search(r"\(([^)]+)\)\s*$", title)
    if m and title.startswith("Introduction to"):
        names.append(m.group(1).strip())
    return names


def clean_title(title: str) -> str:
    title = title.strip()
    m = re.match(r"Scott's VIP calendar for VIPs \((.+)\)$", title)
    if m:
        return f"Call with {m.group(1).strip()}"
    m = re.match(r"(.+?)'s Personal Calendar \(.*\)$", title)
    if m:
        return f"Call with {m.group(1).strip()}"
    return title


def normalize_attendee(raw: str, self_name: str, aliases: dict[str, str]) -> str | None:
    if "/" in raw:
        name, email = raw.split("/", 1)
    elif "@" in raw:
        name, email = "", raw
    else:
        name, email = raw, ""
    name = name.strip()
    local = email.split("@")[0].lower()
    if local == "scott" or name.lower() in (self_name.lower(), "scott jennings"):
        return self_name
    if name in aliases:
        return aliases[name]
    return name or None  # bare external email: resolved from the title instead


def _fold(name: str) -> str:
    return unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().lower().strip()


def parse_turns(raw: str) -> list[tuple[str, str]]:
    turns: list[tuple[str, str]] = []
    for line in raw.splitlines():
        if line.startswith("Transcript for meeting "):
            continue
        m = re.match(r"^\[([^\]]+)\]:\s*(.*)$", line)
        if not m:
            if line.strip() and turns:
                turns[-1] = (turns[-1][0], f"{turns[-1][1]} {line.strip()}".strip())
            continue
        turns.append((m.group(1).strip(), m.group(2).strip()))
    return [(s, t) for s, t in turns if t]


def resolve_speaker(label: str, known: list[str], self_name: str, aliases: dict[str, str]) -> str:
    """Map a transcript speaker label onto a known participant name.

    Littlebird labels the same person inconsistently across calls — "Dan" vs
    "Dan Kauppi", "Emma Delone" vs calendar "Emma Deloné" — which would split
    one person into several Person entities.
    """
    low = label.lower()
    if low == "you":
        return self_name
    if low in NON_PERSON_SPEAKERS:
        return UNIDENTIFIED_SPEAKER
    label = aliases.get(label, label)
    for k in known:
        if _fold(k) == _fold(label):
            return k
    if " " not in label:
        firsts = [k for k in known if _fold(k).split()[0] == _fold(label)]
        if len(firsts) == 1:
            return firsts[0]
    return label


async def find_day(session: ClientSession, mid: str, window: tuple[date, date]) -> str | None:
    """Last resort for an undated meeting: list one day at a time inside the
    month window it was found in. Day precision only (noon local, marked
    time_source="list_day")."""
    d, end = window
    while d < end:
        text = await call(
            session, "LB_INTERNAL_LIST_MEETINGS",
            start_date=d.isoformat(), end_date=(d + timedelta(days=1)).isoformat(), limit=200,
        )
        if mid in text:
            # Noon in the local zone, so the offset follows DST.
            return datetime(d.year, d.month, d.day, 12).astimezone().isoformat()
        d += timedelta(days=1)
    return None


# --- Main ---------------------------------------------------------------------


def _alias(value: str) -> tuple[str, str]:
    name, sep, canonical = value.partition("=")
    if not sep or not name.strip() or not canonical.strip():
        raise argparse.ArgumentTypeError(f"expected NAME=Canonical, got {value!r}")
    return name.strip(), canonical.strip()


async def export(args) -> None:
    out = Path(args.out).expanduser()
    out.mkdir(parents=True, exist_ok=True)
    aliases = dict(args.alias or [])

    async with create_mcp_http_client(auth=build_auth()) as http:
        async with streamable_http_client(SERVER_URL, http_client=http) as (read, write, *_):
            async with ClientSession(read, write) as session:
                await session.initialize()

                # LIST_MEETINGS silently caps its page, so walk month windows.
                meetings: dict[str, str] = {}
                windows: dict[str, tuple[date, date]] = {}
                d = date.fromisoformat(args.since).replace(day=1)
                today = date.today()
                while d <= today:
                    nxt = (d + timedelta(days=32)).replace(day=1)
                    text = await call(
                        session, "LB_INTERNAL_LIST_MEETINGS",
                        start_date=d.isoformat(), end_date=nxt.isoformat(), limit=200,
                    )
                    for name, mid in parse_id_list(text):
                        meetings.setdefault(mid, name)
                        windows.setdefault(mid, (d, nxt))
                    d = nxt
                print(f"Found {len(meetings)} recorded meetings")

                for mid, name in meetings.items():
                    dest = out / f"{mid}.json"
                    if dest.exists() and not args.force:
                        print(f"  skip (exported) {name}")
                        continue
                    detail = await call(session, "LB_INTERNAL_GET_MEETING", meeting_id=mid)
                    raw = await call(session, "LB_INTERNAL_GET_MEETING_TRANSCRIPT", meeting_id=mid)
                    start, end, attendees = parse_event(detail)
                    tldr = re.search(r"TLDR: (.*)", detail)
                    tldr = tldr.group(1).split(" Event name:")[0].strip() if tldr else ""

                    time_source = "calendar" if start else None
                    if not start:
                        found = await call(
                            session, "LB_INTERNAL_SEARCH_MEETINGS",
                            query=f"{name} {tldr if tldr != 'N/A' else ''}".strip(), limit=25,
                        )
                        start = chunk_start_times(found).get(mid)
                        time_source = "transcript_chunk" if start else None

                    if not start:
                        start = await find_day(session, mid, windows[mid])
                        time_source = "list_day" if start else None

                    participants = [args.self_name]
                    for p in [normalize_attendee(a, args.self_name, aliases) for a in attendees] + people_from_title(name):
                        if p and all(_fold(p) != _fold(q) for q in participants):
                            participants.append(p)
                    lines = []
                    for label, text in parse_turns(raw):
                        speaker = resolve_speaker(label, participants, args.self_name, aliases)
                        if speaker != UNIDENTIFIED_SPEAKER and speaker not in participants:
                            participants.append(speaker)
                        # Unlabeled turns stay unlabeled: any placeholder name
                        # ("Unidentified speaker") gets extracted as a Person.
                        lines.append(text if speaker == UNIDENTIFIED_SPEAKER else f"{speaker}: {text}")
                    transcript = "\n".join(lines)

                    duration = None
                    if start and end:
                        duration = round(
                            (datetime.fromisoformat(end) - datetime.fromisoformat(start)).total_seconds() / 60
                        )

                    record = {
                        "littlebird_id": mid,
                        "title": clean_title(name),
                        "original_title": name,
                        "start_time": start,
                        "time_source": time_source,
                        "duration_minutes": duration,
                        "participants": participants,
                        "tldr": tldr,
                        "summary": parse_summary(detail),
                        "transcript": transcript,
                        "raw_transcript": raw,
                    }
                    dest.write_text(json.dumps(record, indent=2, ensure_ascii=False))
                    flag = "" if start else "  !! NO START TIME"
                    print(
                        f"  {start or '????'}  {record['title'][:50]:50}  "
                        f"{len(transcript):>7} chars  {len(participants)} ppl{flag}"
                    )


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default="~/Data/littlebird-export")
    ap.add_argument("--since", default="2026-01-01", help="earliest month to scan (ISO date)")
    ap.add_argument("--self-name", default="Scott Jennings", help="replaces Littlebird's [You]")
    ap.add_argument(
        "--alias", action="append", type=_alias, default=None,
        help="NAME=Canonical rewrite for attendee/speaker labels (repeatable)",
    )
    ap.add_argument("--force", action="store_true", help="re-export meetings already on disk")
    asyncio.run(export(ap.parse_args()))


if __name__ == "__main__":
    main()
