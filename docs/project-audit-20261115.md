# Project reliability audit on 15 November 2026

The production morning workflow is configured to run automatically. Two independently reproduced defects were corrected and deployed: incomplete validation of claimed-success source reports, and ambiguous Gmail Sent address matching. The audit also checked the live runtime, data integrity, API, deployment consistency, schedules and external alert configuration.

This is a bounded engineering audit, not a guarantee that no future defect or upstream outage can occur. No additional email was sent, no buyer or partner outreach was enabled, no timer enablement state changed, and no GitHub push or merge was performed.

## Corrections

### Contradictory source reports could pass a success shortcut

The routine coordinator previously accepted an exit-zero report with eight entries, a feed field and a truthy success flag before validating the actual source identities and statuses. Composition did not require the exact eight identities. The clean-report paths in publication, composition and sending could also accept a failed or invalid feed when the summary said success.

The correction validates all eight named sources and the feed independently. The feed must be a nonempty string without the runner's failure marker. A clean run requires the literal boolean `true` and no source problems. Named unrelated source gaps retain the existing approval policy; BuyICT failure and unclear or incomplete whole runs remain blocked.

Changed implementation: `routine/run_once.py`, `routine/scripts/compose_checked_brief.py`, `routine/scripts/send_gmail.py`, `deploy/illoura_cloud_ops.py`. Regression fixtures demonstrate duplicate/missing source identities, failed or malformed feeds, and a falsely successful report with crawl failures. Valid fixture defaults now use real source identities rather than empty rows.

### Gmail Sent matching could falsely establish or miss delivery

The low-level sender previously compared sender and recipient headers using case-sensitive substring checks. A different address containing the expected address could match, while an uppercase equivalent could fail. It also relied on the search query without independently requiring the message's `SENT` label.

The correction parses complete email addresses, compares them case-insensitively and requires `SENT`. Tests verify display names, uppercase addresses, address-prefix lookalikes and a draft returned by a fake search. An unresolved attempt remains unresolved without matching proof. These tests use mocked Google sessions; they do not deliver email.

## Verification

| Check | Result and scope |
|---|---|
| Main Windows suite after fixes | 725 tests, OK, 23 platform-specific skips |
| Local assistant suite | 114 tests, OK; no local model enabled |
| Linux staged operations | 126 tests, OK |
| Linux staged routine | 44 tests, OK, including Linux coordinator tests |
| Linux staged collectors | 99 tests, OK; collectors unchanged by this audit |
| Linux staged API | 15 tests, OK, using API framework and isolated data |
| Working SQLite databases | All 17 discovered databases returned `ok` from `PRAGMA quick_check` |
| Deployed source comparison | All 20 checked operations, routine and API files match LF-normalized local source |
| Public API process | `/healthz` returned 200 |
| Unauthenticated catalog | Returned 401 |
| Authenticated catalog and schema | Returned 200 |
| Authenticated aggregate query | Returned 200; full-table opportunity count 19,165 at the pre-rehearsal snapshot |
| Public readiness | Returned 503 because the earlier 15 November brief was missed; this is expected attention, not a claim of healthy delivery |
| nginx configuration and services | Configuration valid; nginx and API active |
| Clock | NTP synchronized |
| Disk | About 141.3 GB available of 165.3 GB visible filesystem capacity at inspection |
| Credential files | API key registry 0640, Gmail token 0600, mail configuration 0640; contents not exposed |
| Reboot | No reboot-required marker |
| Runtime dependency consistency | Core and API virtual environments both passed `pip check` under their appropriate service identities |
| Automatic snapshots | Two available snapshots, 13 and 14 November UTC, both `Success` |
| Lightsail email contact | AWS now reports `Valid`; earlier pending status is resolved |
| External CloudWatch alarm | Actions enabled, missing data treated as breaching; current `ALARM` reflects missed-brief readiness |
| Independent watcher execution | EventBridge rule enabled every five minutes, Lambda active with successful last update; 24 readiness metric datapoints in the inspected two-hour window |
| AWS plan | Free plan ACTIVE; remaining credits not remeasured |
| Static repository check | 192 tracked Python files parsed with no syntax failures; new document file links resolve; no tracked database, `.env`, PEM or KEY file candidates found |

The suites overlap; these rows must not be summed as a unique test count. The API query count is a dated snapshot, not a live portal count or a promise that every record remains open.

## Automatic operation

The daily brief timer is enabled and active, with its next collection trigger at 06:15 Sydney on 16 November, corresponding to 00:45 India time. Intended delivery is 08:15 Sydney, or 02:45 India time on that date. Health and certificate-renewal timers are enabled. The redundant standalone collection timer remains disabled. No legacy server-side scraper, BuyICT monitor or planning timer was installed or unexpectedly active.

Collection has four bounded retries after pauses of 5, 20, 40 and 60 minutes. Composition has one five-minute retry. Retry starts are limited to 09:30 Sydney and work is bounded by the noon cutoff. Uncertain email sends are reconciled rather than blindly retried. Existing external monitoring continues independently of this audit session.

The old failed daily-brief and health service results were not reset to create an artificial green status. The actual next scheduled run and confirmed receipt remain the necessary delivery test.

## Deployment and rehearsal evidence

Routine installation preserved replaced files in `/var/backups/illoura/routine-20261115T150905Z`. Operations installation ran its staged suite and preserved timer enablement states. An independent post-install SHA-256 comparison found no mismatches in the 20 checked files.

The full no-send collection and publication rehearsal completed after both installations. Its final result and fresh composition check are recorded below.

Full rehearsal `daily_sweep_20261115_204537` completed and published generation `generation-20261115T152714Z-d469f577`. All eight sources reported: seven OK and NSW PARTIAL for its bounded notice history; the exact NSW gap was approved. Essential evidence completed with exit zero. Saved rows included 131 ATMs, 676 plans, 135 BuyICT requests, 62 ACT rows and 249 NSW rows. A fresh no-send composition selected 8 requests and dropped 0; composition and desktop/mobile layout checks passed. No email was sent. The coordinator also completed its normal isolated operations restore drill before publication.

Private-safe evidence is retained in the private workspace under `reports/audit_project_20261115/`: baseline and post-fix test logs, Linux staging results, installation results, runtime inventory, hashes, API smoke checks and collection status. Real mail addresses, host addresses and credential values are excluded from committed documentation.

## Remaining limits and dependencies

1. The missed 15 November edition was not resent. A rehearsal and preview cannot establish the next unattended delivery. Verify the 16 November send from its receipt and mailbox evidence.
2. NSW notice collection remains capped at five pages per notice type. History beyond that cap is explicitly partial. Current opportunity coverage and notice-history coverage are separate; the approved NSW gap does not cancel the BuyICT-only brief.
3. The Gmail OAuth application remains in pilot/testing under the owner's existing decision. The token may expire around 19 November at 20:46 India time. Publishing the OAuth app or reauthorization is still required before that affects later delivery. No consent or application publishing change was made here.
4. Unknown BuyICT totals without explicit pagination evidence deliberately fail closed. Guessing completeness from old IDs would hide newly added records.
5. Portal layout changes, rate limits, upstream outages, quota changes, credit exhaustion and externally revoked credentials cannot be eliminated by unit tests. The configured recovery paths reduce impact and monitoring identifies attention conditions.
6. Local Sheets/Excel/BDM integrations were exercised through their regression suites, not by publishing new sheets, sending optional BDM mail or activating legacy outreach. Optional model benchmarks were not run because local AI is disabled.
7. Linux staging and live API smoke checks do not simulate a server power failure at every write boundary or perform a complete whole-machine disaster restore. The normal coordinator's isolated operations restore drill and the successful snapshots provide narrower recovery evidence.
8. Operations installation is not transactional in the same way as API deployment: it stages/tests and installs fixed files but has no automatic unhealthy-code rollback. The independently checked installed hashes prove this deployment's file identity, not that every possible interrupted deployment can recover automatically.
9. New commits remain local until a separately authorized push. Existing remote CI results do not cover unpushed changes.

## Graduate documentation

[The graduate guide](graduate-guide.md) explains the business purpose, terminology, eight sources, technologies, parsing, identity handling, SQLite, classification, workflow, email, API/MCP, optional research/AI, deployment and troubleshooting in 26 chapters. The existing [reference index](reference/README.md) supplies file-level details for the remaining helpers and configuration.
