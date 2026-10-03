# Serving without MTP

`--serve --no-mtp` runs target-only decoding: each decode window commits one
main-model token. It does not load the MTP weights or allocate its KV, bind its
output head, run draft prefill, restore draft KV, or run the draft decoder.
Suffix/prompt-lookup drafting is also disabled. An existing `--mtp DIR` is
ignored and cleared before weight loading, regardless of argument order.

This differs from `--mtp-max-t 1 --suffix-draft 0`: that configuration still
loads and executes the draft layer even though none of its guesses is used.

Keep `--spec T` with T >= 2: it sizes the existing native verifier, which also
processes short prompt segments in batches. It does not allow speculative
output in `--no-mtp` mode. Engine INFO reports `mtp_enabled=0 spec=1 mtp_max=0
lookup=0`; response timings report zero offered and accepted draft tokens.
No new model alias or API format is required. Changing modes restarts the engine.

Live-prefix reuse and conversation checkpoints remain enabled. The separate,
opt-in parked-conversation cache currently serializes draft KV and is not
supported in this mode; a configuration enabling it is rejected before loading.
Use `--conversation-cache-mib 0` (the default). CLI generation outside `--serve`
is not changed; `--no-mtp` there reports an explicit error.

## Song PC deployment

The production model remains `qwen3.8-fn`, SC117 IQ3_S, with a 262144-token
context, INT8 KV, authentication and manual service startup. Project files stay
on `/mnt/data`. The deployment removes the MTP directory and tuning arguments
from its config and adds `--no-mtp`; the independent executable and logs live
under `/mnt/data/strata-deploy/production-no-mtp-20261003/`. MTP model files and
previous binaries are retained only for rollback, not read by this mode.

The historical 32K performance baseline remains separate. This change addresses
the user's request to remove MTP from execution; it does not establish MTP as
the cause of the thinking/tool-call problem tracked in QORA-12.

## Validation

Build the `strata` target and verify rejection of `--no-mtp` without `--serve`,
and of `--serve --no-mtp --conversation-cache-mib 64`. Both must return code 2
with the corresponding diagnostic before loading a model.

Against an idle server started with `--no-mtp`, run:

```sh
python tools/no_mtp_smoke.py --key-file /path/to/private-key --out /path/to/results.json
```

This sends synthetic requests and checks metadata, zero draft counters,
batched prefill, checkpoint reuse, Messages streaming, and one read-tool/result
round trip. The tool result is simulated; no model-selected shell command or
real user file is executed or read. Use `--context` when validating a different
capacity. A short smoke test is not a full-context or tool-quality evaluation.

On Song PC (RTX 4080, SC117 IQ3_S, 256K), the new binary loaded with zero
MTP-head reserve; the expert cache increased from 2471 slots / 4.74 GiB to
3150 slots / 6.03 GiB. The weights resident in pinned host RAM changed from
45.09 to 43.80 GiB. Extra free VRAM was reused for experts, so total GPU memory
usage need not fall by the size of the removed MTP allocation.

The synthetic smoke test passed: both arithmetic requests returned 391 with
zero offered/accepted drafts, the second reused 464 tokens; Messages SSE returned
你好 and a message_stop; a read tool call returned structured tool_use and, after
a synthetic tool_result, the correct code 蓝鲸-7319 with end_turn. These tool
checks disabled thinking and do not establish that the original thinking-mode
failure is fixed. Production has not been tested with a full 256K input.
