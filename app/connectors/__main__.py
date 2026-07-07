"""CLI entry point: python -m app.connectors"""

import argparse
import asyncio
import logging
import os
import sys

from app.connectors.grain import GrainConnector, ingest_request_to_jsonl

logger = logging.getLogger(__name__)


async def run_export(
    token: str,
    since: str,
    until: str | None = None,
    output: str | None = None,
    dry_run: bool = False,
    stdout: bool = False,
) -> int:
    """Run the Grain export. Returns count of recordings processed."""
    connector = GrainConnector(api_key=token)
    try:
        recordings = await connector.list_recordings(
            start_date=since, end_date=until,
        )

        if dry_run:
            print(f"Found {len(recordings)} recordings:")
            for rec in recordings:
                print(f"  {rec['id']}  {rec.get('title', 'Untitled')}  "
                      f"{rec.get('start_datetime', '')}")
            return len(recordings)

        results = []
        for rec_meta in recordings:
            detail = await connector.fetch_recording(rec_meta["id"])
            ingest_req = connector.to_ingest_request(detail)
            results.append(ingest_request_to_jsonl(ingest_req))

        if stdout:
            for line in results:
                print(line)
        elif output:
            with open(output, "w") as f:
                for line in results:
                    f.write(line + "\n")
            logger.info(f"Wrote {len(results)} records to {output}")
        else:
            print(f"Exported {len(results)} recordings (use --output or --stdout)")

        return len(results)
    except PermissionError as e:
        logger.error(f"Authentication failed: {e}")
        return -1
    except ConnectionError as e:
        logger.error(f"Connection error: {e}")
        return -1
    except FileNotFoundError as e:
        logger.error(f"Recording not found: {e}")
        return -1
    finally:
        await connector.close()


def main():
    parser = argparse.ArgumentParser(description="Export Grain recordings to JSONL")
    parser.add_argument(
        "--token",
        default=os.environ.get("GRAIN_API_TOKEN"),
        help="Grain API token (or set GRAIN_API_TOKEN env var — "
             "preferred, avoids leaking the token into shell history)",
    )
    parser.add_argument("--since", required=True, help="Start date (YYYY-MM-DD)")
    parser.add_argument("--until", default=None, help="End date (YYYY-MM-DD)")
    parser.add_argument("--output", default=None, help="Output JSONL file path")
    parser.add_argument("--dry-run", action="store_true", help="List recordings only")
    parser.add_argument("--stdout", action="store_true", help="Write JSONL to stdout")
    args = parser.parse_args()

    if not args.token:
        parser.error("--token required (or set GRAIN_API_TOKEN environment variable)")

    logging.basicConfig(level=logging.INFO)
    count = asyncio.run(run_export(
        token=args.token, since=args.since, until=args.until,
        output=args.output, dry_run=args.dry_run, stdout=args.stdout,
    ))
    sys.exit(0 if count >= 0 else 1)


if __name__ == "__main__":
    main()
