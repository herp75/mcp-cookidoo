# Security policy

## Reporting a vulnerability

Please **do not open a public issue**. Report vulnerabilities privately through
GitHub: *Security* tab → *Report a vulnerability* (private security advisory).
You should get an answer within a week. Never paste your Cookidoo credentials,
tokens or cookies into an issue, advisory or log excerpt.

## Supported versions

Only the latest release receives security fixes.

## Threat model in short

- **This server holds your Cookidoo password.** Cookidoo has no OAuth for third-party
  apps, so the server logs in with your real credentials. Use a unique password for
  Cookidoo.
- **Run it locally over stdio** (the default). The MCP client starts it as a child
  process; nothing listens on the network.
- **HTTP mode** (`--transport http`) binds to `127.0.0.1` by default. Binding to any other
  host requires `COOKIDOO_MCP_TOKEN` (at least 32 random characters), which every request
  must send as `Authorization: Bearer <token>`. The server refuses to start otherwise.
  Only use it on a trusted network; it does not terminate TLS itself, so put it behind a
  TLS reverse proxy if it leaves the machine. `/health` returns only `{"status":"ok"}`.
  Note: `fastmcp run … --transport http` bypasses these checks; use `server.py` instead.
- **TLS certificate verification is always on** for connections to Cookidoo. There is no
  switch to disable it, and CI fails on code that would.
- **Secrets are redacted** from logs and from error messages returned to the model
  (password, tokens, `Authorization` headers, cookies). Login tokens stay in memory.
- **Recipe content is untrusted input.** Recipe names, steps, notes and collection names
  come from Cookidoo and from other users. Tools return them as data; a malicious recipe
  could still try to instruct the model (prompt injection). Keep your MCP client's
  approval prompt on for write tools.
- **Input validation:** IDs are checked against `^[A-Za-z0-9_-]{1,64}$` before they are put
  into a URL; list inputs are capped.
- **Supply chain:** dependencies are capped and locked (`uv.lock`), audited with
  `pip-audit`, updated through Dependabot; GitHub Actions are pinned by commit SHA.
