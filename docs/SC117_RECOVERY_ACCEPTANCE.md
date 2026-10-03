# Song PC recovery deployment — 2026-10-03

Runtime source: `383a6fd` (recovery and diagnostics), built on `471edc3`.
The native engine is unchanged from `production-no-mtp-20261003/strata`;
that historical directory name does not describe the active CLI configuration.

## Active service

- `strata-sc117`: active; `strata-orca`: stopped. Both remain linked, not enabled at boot.
- SC117 IQ3_S, alias `qwen3.8-fn`, authenticated `0.0.0.0:8080`.
- Context 262144, INT8 KV, vision on demand, original 56 GiB WSL limit.
- `--no-mtp` removed, `--mtp /mnt/data/strata-deploy/Strata-data/mtp/rt`
  and `--spec-min-p 0.5` restored. Native INFO: `mtp_enabled=1 spec=6 lookup=3`.
  `spec=6` is the verifier capacity including suffix lookup; MTP itself remains
  capped at 4 tokens, matching the CLI `--spec 4`.
  Removing `--no-mtp` also restores the engine's suffix-lookup default (3).
- Configuration: `/mnt/data/strata-deploy/strata-sc117-iq3_s.json`, mirrored in
  `strata-sc117-256k-production.json`. The separate no-MTP JSON remains a backup.
- Start/stop: `systemctl start strata-sc117` / `systemctl stop strata-sc117`.
  Stop it before starting ORCA because they share port 8080.

## Verification

Full regression: 137 tests, 132 passed, 5 skipped. Following the final boolean-schema
refusal guard, all 19 recovery/diagnostics tests passed (includes one additional test).
Together, 133 distinct tests passed; 5 environment-dependent tests remained skipped.
A separate synthetic logging check removed two plain-text characters between API
preparation and HTTP observation: count and structure mismatch flags fired without
persisting the text. No user data was used in testing.

Five live synthetic requests on the active 256K service:

| API | Streaming | Expected/returned tool calls | Stop | Wall seconds |
| --- | --- | --- | --- | --- |
| Messages | yes | 1 / 1 | tool_use | 4.18 |
| Messages | no | 2 / 2 | tool_use | 5.36 |
| Chat Completions | yes | 1 / 1 | tool_calls | 2.85 |
| Chat Completions | no | 1 / 1 | tool_calls | 1.57 |
| Messages, plain answer | yes | 0 / 0 | end_turn | 3.33 |

All HTTP responses completed, with no diagnostic channel/count mismatch flags.
Native stdout observation was attached without errors. Plain text returned 73
characters. The engine reported 347 output tokens, 300 drafts offered, 238 accepted
across the five requests. These are smoke tests, not a comparative performance benchmark.
No generated tool calls were executed. These live replies closed thinking normally,
so recovery was not triggered; malformed-output recovery is covered by the synthetic
native-pipe, parser, and HTTP regression tests. Real-workload recurrence remains possible.

## Logs and rollback

New schema-3 log: `/mnt/data/strata-deploy/logs/output-diagnostics.jsonl`, mode 0600,
4 MiB + 3 backups. Superseded runtime `boundary-diagnostics.jsonl` was deleted
(89,461 bytes); no rotated siblings existed. Two compact redacted failure summaries
are retained in `orca-boundary-20261003` for comparison. No raw prompts, outputs,
arguments, paths, credentials or content hashes are added to diagnostic records.

Recovery decisions, original boundaries, parser/API/HTTP channels, normalized input
shape and engine MTP/context metadata are recorded. See `TOOL_RECOVERY.md` and
`BOUNDARY_DIAGNOSTICS.md` for refusal rules and diagnostic limitations.

Deployment artifacts, test logs, five live results, configuration backups, and
redacted metadata are under `/mnt/data/strata-deploy/recovery-20261003`.
Rollback configuration snapshots use the `.before` suffix; these contain credentials
and must remain private. Restoring a snapshot requires stopping/restarting SC117.
