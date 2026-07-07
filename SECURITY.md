# Security Policy

## Reporting a vulnerability

Please report security vulnerabilities privately via
[GitHub private vulnerability reporting](https://github.com/sufficiently-advanced-ai/imi/security/advisories/new)
("Report a vulnerability" on the repo's Security tab). Do **not** open a public
issue for anything you believe is exploitable.

You can expect an acknowledgment within a few days. Please include reproduction
steps and the deployment configuration involved (auth mode, exposed ports,
connectors enabled).

## Scope notes for self-hosters

imi is designed to run **private-by-default**:

- The stock `docker-compose.yml` binds all ports to `127.0.0.1`.
- `AUTH_MODE=none` (the default) performs **no authentication** — it is intended
  for single-user, localhost or private-network (e.g. VPN/Tailscale) deployments.
  Do not expose an `AUTH_MODE=none` instance to the public internet.
- The GitHub webhook endpoint accepts a `WEBHOOK_SECRET` but relies on the
  bot-commit prefix and branch rules for loop protection; treat the webhook URL
  as sensitive.
- API keys and tokens live only in `.env` (gitignored). Never commit them.

## Supported versions

Security fixes land on `main`. There are no maintained release branches yet;
run a recent `main`.
