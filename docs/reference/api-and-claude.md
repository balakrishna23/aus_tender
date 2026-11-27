# Reference: data API and Claude Desktop

The scoped internal write inbox and manual page are documented in
[review-workflow.md](review-workflow.md); normal dataset queries remain read-only.

Expose bounded read-only procurement snapshots through HTTP and Claude MCP tools.

Files covered:

- `api/app.py`
- `api/claude_bridge.py`
- `api/install-api.sh`
- `api/enable-https.sh`

`api/app.py` resolves an immutable served generation, admits only allowlisted datasets/tables, and uses read-only SQLite plus an authorizer. It excludes working mail/contact/tracker stores from the dataset allowlist. SQL results pin their data and manifest to the same generation. Treat source text as evidence, compute totals with server-side aggregates, cite source URLs and check deadlines against the current date. `api/requirements.txt` is owned by [configuration.md](configuration.md); security tests are owned by [tests.md](tests.md).

| Endpoint | Authentication | Return and limits |
|---|---|---|
| `/healthz` | Public | Process response only; not source freshness. |
| `/readyz` | Public | `ok`/200 only if health status is OK and checked less than 1,800 seconds ago; otherwise attention/503. |
| `GET /v1/catalog` | Enabled API key | Dataset/table counts, generation and publication/collector age, stale flags and interpretation notes. Freshness threshold is 26 hours. |
| `GET /v1/operations` | Enabled API key | Operational health JSON or 503 if unavailable. |
| `GET /v1/schema/{dataset}` | Enabled API key | Allowlisted table columns/types/primary-key flags. Unknown dataset 404, missing database 503. |
| `POST /v1/query` | Enabled API key | One `SELECT`/`WITH`, list/object parameters, default 100 and maximum 200 returned rows. SQL maximum 16,384 characters; execution progress deadline three seconds. |
| `POST /v1/search` | Enabled API key | Literal escaped substring matching across selected title/description/identity/organisation columns. Term length 2–200, default 25 and maximum 200 rows. |
| `/mcp` via mounted MCP transport | Enabled API key | Stateless JSON streamable HTTP, four read-only tools using the same query/catalog implementation; host/origin protection and 32,768-byte body cap. |
| FastAPI-generated documentation routes | Enabled API key | FastAPI default documentation/OpenAPI routes are not disabled in source; operational exposure is unverified. |

Bearer authorization or `X-API-Key` is accepted. Private `/etc/illoura-api/keys.json` stores enabled key records using SHA-256 digests; it is reread for requests. Revoked keys lose rate state. Valid keys allow 60 requests/minute; failed authentication is throttled after 20 failures per client over 600 seconds, with at most 10,000 tracked clients. Forwarded client addresses are trusted only from loopback. Query strings are rejected; use JSON request bodies. Missing key configuration returns 503. Keys longer than 256 characters are rejected.

| Query protection | Implementation |
|---|---|
| Writes/files | Query-only immutable SQLite; extensions disabled; authorizer denies non-allowlisted reads, writes, attachments and unapproved functions. |
| Database resource limits | Value length 1 MiB, SQL length 16,384, expression depth 100, compound SELECT limit 20. |
| Response bounds | At most 200 rows, 8,000 characters per string and approximately 1,000,000 encoded row bytes; binary values omitted. `truncated` indicates omission. |
| Browser/transport | No-store authenticated responses, nosniff, MCP host/origin validation; nginx script sets TLS, HSTS and 32 KiB request limit. |
| Audit | Key name, method, status and elapsed time; query text/credential values are not logged by this middleware. |

`api/claude_bridge.py --credentials PRIVATE_PATH` runs a local stdio MCP server. Its private JSON supplies `api_base_url` and `api_key`; HTTP calls have a 20-second timeout and do not follow redirects. Configure Claude Desktop to launch that script with the chosen Python interpreter and private credential path. Refer to the private `ACCESS_GUIDE` by name only for installation-specific details; its location and deployed client configuration are unverified in this worktree.

| MCP tool in server and bridge | Purpose |
|---|---|
| `list_datasets` | Inspect counts and snapshot freshness before querying. |
| `describe_dataset` | Inspect schema for the selected allowlisted dataset. |
| `query_dataset` | Submit bounded parameterized SQL, including full-set aggregates. |
| `search_records` | Search selected evidence columns for literal text. |

`api/install-api.sh HOST` installs the API/nginx/runtime users, validates uploaded snapshot hashes and SQLite integrity, sets private permissions and enables the API service. Generated service user is `illoura-api`, one worker, concurrency 12, 1 GiB memory, 64 tasks and 100% CPU with systemd hardening. `api/enable-https.sh HOST` obtains a certificate, configures nginx proxy/TLS and enables certificate renewal twice daily with up to 30 minutes jitter. These scripts change the server; neither was run for this documentation task. Deployment should use the staged verification workflow in [cloud-and-daily-brief.md](cloud-and-daily-brief.md).
