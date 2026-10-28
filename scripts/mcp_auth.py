#!/usr/bin/env python3
"""MCP authentication admin (ADR-010): passphrase hashes, static tokens, OAuth grants.

Secrets
-------
    python scripts/mcp_auth.py hash-passphrase        # prompts; prints MCP_OAUTH_PASSPHRASE_HASH=...
    python scripts/mcp_auth.py new-token laptop       # prints the token once + its MCP_STATIC_TOKENS entry

Neither command touches the running app: put the printed line in ``.env``
(``MCP_STATIC_TOKENS`` is comma-separated ``name:sha256``) and restart.
Inside the container::

    docker exec -it imi-app python scripts/mcp_auth.py hash-passphrase

OAuth grants (MCP_AUTH=oauth)
-----------------------------
    python scripts/mcp_auth.py list-clients
    python scripts/mcp_auth.py revoke --client <client_id>   # sign one connector out
    python scripts/mcp_auth.py revoke --all                  # sign every connector out

These read and write the app database (``DATABASE_PATH``, override with
``--db``). Revocation takes effect on the next request; no restart needed.
Static tokens are revoked by removing them from ``MCP_STATIC_TOKENS``.
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import json
import os
import re
import secrets
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.services.mcp_oauth import OAuthStore, hash_passphrase, hash_token  # noqa: E402

_TOKEN_NAME = re.compile(r"^[A-Za-z0-9._-]{1,48}$")


def _cmd_hash_passphrase(args: argparse.Namespace) -> int:
    if args.stdin:
        passphrase = sys.stdin.readline().rstrip("\n")
    else:
        passphrase = getpass.getpass("Owner passphrase: ")
        if passphrase != getpass.getpass("Again: "):
            print("Passphrases differ.", file=sys.stderr)
            return 1
    if len(passphrase) < 12:
        print("Use at least 12 characters: this guards a public sign-in page.", file=sys.stderr)
        return 1
    print(f"MCP_OAUTH_PASSPHRASE_HASH={hash_passphrase(passphrase)}")
    return 0


def _cmd_new_token(args: argparse.Namespace) -> int:
    if not _TOKEN_NAME.match(args.name):
        print("Token names are 1-48 of A-Z a-z 0-9 . _ -", file=sys.stderr)
        return 1
    token = f"imi_st_{secrets.token_urlsafe(32)}"
    print(f"Token (shown once; the client sends 'Authorization: Bearer <token>'):\n  {token}")
    print(f"Add to MCP_STATIC_TOKENS (comma-separated) and restart:\n  {args.name}:{hash_token(token)}")
    return 0


def _db_path(args: argparse.Namespace) -> str:
    if args.db:
        return args.db
    from app.config import settings

    return settings.DATABASE_PATH


async def _with_store(args: argparse.Namespace, fn):
    store = OAuthStore()
    await store.open(_db_path(args))
    try:
        return await fn(store)
    finally:
        await store.close()


def _cmd_list_clients(args: argparse.Namespace) -> int:
    clients = asyncio.run(_with_store(args, lambda s: s.list_clients()))
    print(json.dumps(clients, indent=2))
    return 0


def _cmd_revoke(args: argparse.Namespace) -> int:
    if not args.all and not args.client:
        print("Pass --client <client_id> or --all.", file=sys.stderr)
        return 1
    removed = asyncio.run(
        _with_store(args, lambda s: s.revoke_client(None if args.all else args.client))
    )
    print(f"Removed {removed} code/token rows.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("hash-passphrase", help="hash the OAuth owner passphrase")
    p.add_argument("--stdin", action="store_true", help="read the passphrase from stdin")
    p.set_defaults(func=_cmd_hash_passphrase)

    p = sub.add_parser("new-token", help="mint a static bearer token")
    p.add_argument("name", help="label for the client (shows in the audit actor)")
    p.set_defaults(func=_cmd_new_token)

    p = sub.add_parser("list-clients", help="list registered OAuth clients")
    p.add_argument("--db", help="SQLite path (default: DATABASE_PATH)")
    p.set_defaults(func=_cmd_list_clients)

    p = sub.add_parser("revoke", help="revoke OAuth grants")
    p.add_argument("--client", help="client_id to sign out")
    p.add_argument("--all", action="store_true", help="sign out every OAuth client")
    p.add_argument("--db", help="SQLite path (default: DATABASE_PATH)")
    p.set_defaults(func=_cmd_revoke)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
