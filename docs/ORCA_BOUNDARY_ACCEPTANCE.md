# ORCA boundary comparison on Song PC (2026-10-03)

Production was switched from SC117 IQ3_S to the existing Orca Uncensored
IQ3_XXS pack, retaining `qwen3.8-fn`, port 8080, authentication, 262144 context,
INT8 KV, and target-only decoding (`mtp_enabled=0`, `spec=1`, `lookup=0`).
The native executable is unchanged. Python source is `9e16aec`.

`strata-orca` is the active manual-start service; `strata-sc117` is stopped and
preserved. Both are linked units without boot enablement. ORCA uses a separate
config at `/mnt/data/strata-deploy/orca-boundary-20261003/orca-256k-no-mtp.json`.
Its config uses automatic prefill, the current fused-prefill environment and
vision on demand. Previous ORCA configurations remain available. To switch back,
stop `strata-orca` before starting `strata-sc117`; both bind the same port.

The schema-2 diagnostics are enabled for both service definitions, with actual
weight labels (`orca-iq3_xxs` / `sc117-iq3_s`). They use the same rotating, redacted
JSONL file; original schema-1 records remain distinguishable. No full DEBUG or
API monitor is enabled. The original failing SC117 diagnostic record was copied
to the comparison directory before further requests.

## Validation

The target environment ran 126 unit/integration tests: all non-skipped tests
passed, five were skipped by their environment gates. Tests include an actual
scripted subprocess to verify pipe counting before queueing and draining after
EOS. No GPU arithmetic or parser policy was changed.

Four real Anthropic Messages SSE requests ran with thinking enabled and a 2048
output cap. Inputs and file names were synthetic; no model-selected tool ran.
The long tests use synthetic manuscript-review background sized via count_tokens,
not private dialogue replay. The second long input shares the first one's prefix.

| Case | Input tokens | Native / pipe / service tokens | Client tool blocks | End reason |
| --- | ---: | --- | ---: | --- |
| Short read | 370 | 100 / 100 / 100 | 1 | tool_use |
| Short two reads | 375 | 77 / 77 / 77 | 2 | tool_use |
| Long read | 128317 | 189 / 189 / 189 | 1 | tool_use |
| Long two reads | 128307 | 100 / 100 / 100 | 2 | tool_use |

All four had one think-end sequence at the native pipe and service token boundary,
one decoded `</think>`, zero observer errors, matching complete tool-call events,
and a completed HTTP stream. No missing-boundary anomaly was reproduced.

Artifacts and the exact synthetic probe are in
`/mnt/data/strata-deploy/orca-boundary-20261003/` (`probe.py`, `short-results.json`,
`long-results.json`, `tests.log`). No credentials or model output prose are saved
in those result files. The service remains available for real Pi use.

This is a small diagnostic sample, not evidence of a zero failure rate or a fix.
The original failure involved a real 128489-token history that was deliberately
not retained. ORCA IQ3_XXS and SC117 IQ3_S also differ in quantization and weight
preparation, so this comparison does not isolate a single cause. A new failure
with schema-2 logs can distinguish a marker absent at the native stdout boundary
from a marker lost later in Python; absence at native stdout still does not
separate weights from native computation, sampling or prompt/template effects.
