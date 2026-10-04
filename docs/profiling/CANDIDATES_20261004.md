Candidate study on Song PC — 2026-10-04

Frozen baseline: `5bf523a9c0a5dfaf1cb14eaa3d4924c3f33cdf50` (upstream 0.1.39 merge).
Hardware: RTX 4080 16 GB, i5-13600KF, WSL 56 GB limit. Model: SC117 IQ3_S,
Configured MTP spec 4 (effective verify maximum 6 because suffix draft adds two;
MTP maximum remains 4), INT8 KV, `pcie_frac=0.25`. Experimental combinations are not production defaults.

Method

- Each candidate starts from the frozen baseline, not the previous candidate.
- Independent synthetic requests from networking, Go, Chinese fiction, mathematics,
  science, and multilingual structured data; no private prompts or completions saved.
- 32K capacity for standard tests. 256K for allocation changes and final production
  configuration validation. Capacity is not actual prompt length.
- Fixed 192 generated-token limit, temperature zero, prefix reuse disabled, fixed
  warmup and workload order. Fresh engine per arm. Save generated count and MTP
  acceptance counters; identical limits do not imply identical speculative work.
- First screen: two repetitions per input, baseline bookends. The CPU screen
  showed about 9% decode and 10% prefill drift, so code trials add intermediate
  baseline controls. GPU clock/temperature are recorded in the later trials.
  This WSL kernel omits `/proc/<pid>/io`; process I/O is unavailable, never zero.
  System disk counters and engine expert-file counters remain available; no thermal cause is inferred from the first screen alone. Confirm selected
  combination with ABBA, not a one-shot gain claim.
- No compilation during measured requests. No concurrency or multi-turn tests.
- Timing/counters only. The runner keeps optional output comparisons in memory;
  persisted data contains booleans, no token IDs, output hashes or user content.
- Decode throughput uses `emitted/decode_ms` (the engine times the first output too); prefill uses prompt tokens/prompt_ms.
- Compilation restores changed source files with fresh mtimes to prevent reuse of
  object files compiled against a previous candidate. Every binary has a SHA256.

Candidates and admission gates

| Candidate | Isolation / gate |
|---|---|
| CPU workers 4/6/8 vs 9 | Same binary and all other args |
| P-core affinity | Same binary, 9 workers; check whether WSL exposes useful topology |
| AVX2 gather | `STRATA_IQ256_GATHER=1`; prefill fusion was already enabled |
| #764 delayed admission | Raw PR not admitted: eviction updates host_res, but GPU d_res still maps an overwritten slot. Verifier does not refresh d_res per window. Real resident_plan kernel reproduction added. Test adapted single-GPU serial variant only, publishing evictions before another verify. Default lag remains 1. |
| #378 elastic KV | Raw PR explicitly excludes resident_cpu_experts, which --resident-experts enables. Also conflicts with the fork's image LEND allocator. Isolated opt-in complement adaptation permits the existing GGUF fallback for evicted experts; compare matched 256K controls with image lending disabled and count file reads. This is an adapted prototype, not the unchanged PR. |
| #783 GPU fusions | Resolve verifier conflicts, preserve fork diagnostics. Native parity tests before model benchmark. |
| #789 prefill overlap | Port alone; test short input and 4K paths separately |
| #733 CPU pipeline | Preserve current q2_native_kernels checks when extracting helpers; real-model parity test; overlaps #500 |
| #500 CPU quantization phase | Preserve current q2_native_kernels checks; independent alternative to #733 |
| #699 startup readahead | Distinguish load time from steady-state throughput; no cold-start claim without controlled eviction |
| Own cache churn | Existing swap cap / opt-in hysteresis, each independently; no expert-ID logs |
| Own fetch geometry | Actual fetch kernel with synthetic mapped RAM; byte equality plus timings, then model test if promising |
| Own copy/compute overlap | Separate CUDA stream, fork/join events, single group; default off; verify byte/numerical correctness |
| Own complement THP | Opt-in aligned prefaulted RAM + CUDA registration; reuse registered-allocation cleanup; inspect actual AnonHugePages |
| Own multistream diagnostics | Preserve default shared-expert overlap; GPU wait reported after subtracting other active GPU intervals |

Deferred broad designs (#726 full live memory policy and #689 broad MTP rewrite)
are not dependencies of these experiments. They do not provide an isolated,
applicable benefit sufficient to justify bundling them into the study.

Decision: keep the approved 0.1.39 baseline defaults. Preserve the fetch-size knob as opt-in only; archive the other prototypes for research.

Validation completed before performance screening

- Own experiment binary: 69 native tests passed; fetch byte checks passed at
  96/192/384/768 blocks. Real resident-plan kernel reproduces the stale mapping
  hazard and verifies eviction invalidation; this is not a full-model corruption
  reproduction.
- #699, #789, #500, #783: 69 native tests passed per build.
- #733: 69 native tests passed, asset-dependent pipeline test skipped in CTest;
  explicitly rerun with SC117 assets: 810/810 bitwise parity checks passed.
- Adapted #378: 70 native tests passed.
- Known baseline exclusions: ple_parity needs absent fixed assets;
  expert_multi_test needs unsupported AVX-512 S2 kernels. Neither is a pass.
- First code-screen attempt had zero measured requests: the new optional
  `/proc/<pid>/io` read failed because this WSL omits the interface. Cleanup
  closed the engine normally. A retry was cancelled before requests after
  identifying that cause; code-screen-v2 handles unavailable counters.

Fetch microbenchmark (not model throughput): rotating 256 MiB pinned host arena,
2 MiB blobs, ten warmups and 100 measured transfers per size. Interleaved repeat
384,96,96,384,192,96 confirmed 16-blob transfers at 2.874–2.887 ms for 384
blocks versus 1.927–2.181 ms for 96; every byte check passed.

Initial code screen (not confirmation)

All 17 arms completed. Seven independent fixtures, two measured passes, 192-token
limits; only two fixtures warmed before measurement. Values below include both
passes. First-pass filesystem reads and baseline drift prevent treating small
differences as established effects.

| Arm | Prefill token/s | Decode token/s |
|---|---:|---:|
| baseline-code-start | 1577.32 | 49.21 |
| pr-699 | 1556.59 | 47.53 |
| pr-789 | 1480.20 | 46.94 |
| pr-500 | 1450.43 | 50.05 |
| pr-733 | 1445.09 | 49.77 |
| baseline-community-mid | 1409.94 | 46.66 |
| pr-783 | 1417.80 | 49.27 |
| baseline-code-mid | 1394.32 | 48.84 |
| own-disabled | 1385.96 | 47.64 |
| adapt-lag-2 | 1367.85 | 47.09 |
| adapt-gain-3 | 1354.67 | 49.77 |
| adapt-cap-32 | 1277.68 | 48.71 |
| baseline-own-mid | 1419.85 | 48.07 |
| complement-thp | 1469.89 | 49.75 |
| fetch-overlap | 1503.49 | 50.47 |
| fetch-blocks-96 | 1488.11 | 50.65 |
| baseline-code-end | 1419.24 | 48.29 |

The THP arm actually registered the entire 41.43 GiB complement, with 23.65 GiB
reported as AnonHugePages. This is not a claim that all pinned memory uses huge
pages. Delayed admission increased system disk reads and showed no speedup.
No arm reported expert-file reads or Linux swap-out, but this does not establish
zero other filesystem I/O or zero Windows memory pressure.

Targeted #789 short-prefill follow-up

Fresh baseline and #789 engines; all fixtures warmed, then four independent
64-output-token requests at each length. Normal automatic prefill scheduling.
Prefill token/s: 256 input: 331.9 -> 321.2; 1K: 848.2 -> 777.2;
2K: 1321.6 -> 1222.3; 4K: 1700.2 -> 1662.6. No observed benefit; not retained.
The single chronological A/B does not establish a universal regression. CPU/GPU
mixed prefill is active below 4K in this configuration.

Fixed-path correctness gate

Baseline versus own fetch96 + fetch overlap + THP (gain3 is inactive with swaps0).
Both disable CPU prefill, fix prefill chunks at4096, request expert-cache3000
(the layout reports3896 actual slots in both), and disable adaptive swaps.
Seven synthetic fixtures, two repetitions,128-token output limit, all warmed.
All14 candidate sequences match their baseline sequence exactly; both arms
repeat7/7 exactly. No output/token IDs are persisted. MTP acceptance is identical
(0.687). Decode43.90 ->46.11 token/s (+5.02%), prefill1541.39 ->1525.35
(-1.04%). This supports the transport/allocator correctness gate; it is not
a semantic quality benchmark or the final production performance comparison.

Adapted #378 at256K capacity

Both controls disable vision_on_demand solely for this isolated direct-engine
text test. All six fixtures warmed, two measured passes,192-token output limit;
actual inputs256,4096 (four domains), and32768 tokens.

| | Baseline | Adapted elastic KV |
|---|---:|---:|
| Initial expert slots |2471|4107|
| Prefill token/s |1684.49|1691.41|
| Decode token/s |42.64|48.38|
| Mean request seconds |9.415|8.845|
| Mean TTFT seconds |4.963|4.932|
| Expert hits / lookups |0.543|0.657|
| Expert file MB |0|41.5|
| Whole-system data-disk read MiB |91.5|1169.2|

The prototype started at16384 KV cells, grew to40960, yielded163 expert slots,
and trimmed to8192 after a short request, returning163 slots. The entire41.68GiB
complement was pinned. Decode +13.5%, mean request time -6.1% in this single A/B.
Domain effects vary: Go40.28->53.44 token/s, mathematics38.63->39.10.
System disk reads cannot all be attributed to the engine, and summed I/O service
time is not wall-time waiting. No256K-token input was run;32768 is the longest.

Promising research result, excluded from the production combination: the adapted
resident complement can fall back to GGUF reads, and the elastic KV allocator
still excludes the image LEND backend. Unifying ownership, grow/trim, image loans,
and failure recovery requires a separate change and broader correctness tests.
Do not silently disable image support to deploy this prototype.

Counting clarification: the serve engine starts decode timing before generating
the first output (src/program/generate.cpp: first_window starts with the final
prompt token). All final decode rates therefore use emitted/decode_ms, matching
the engine. An earlier interim script subtracted one; corrected summaries leave
raw times untouched. This changes192-token rates by0.524%,128-token rates by0.787%,
and64-token rates by1.587%, without changing equal-length A/B ratios.

Balanced combination confirmation (A B C C B A,32K capacity)

All fixtures warmed before each arm's two192-token passes. Each arm reloads a
fresh engine. A=baseline; B=fetch96 + independent fetch stream; C=B + THP +
admission gain3. Metrics below aggregate equal work-length requests across both
loads of each arm.

| Group | Prefill token/s | Decode token/s | Mean native TTFT s | Mean request s |
|---|---:|---:|---:|---:|
| baseline | 1594.34 | 50.84 | 2.282 | 6.005 |
| transport | 1573.98 | 51.17 | 2.311 | 6.018 |
| transport-memory | 1573.27 | 51.88 | 2.313 | 5.959 |

B: decode+0.66%, mean request time+0.23% (slower). C: decode+2.05%,
mean request time-0.76%. C's two loads took244.9/246.9s versus A197.0/212.4s.
Not a controlled cold-start test, but no end-to-end gain justifies enabling the
complex combination here. A runs51.47/50.22; B50.70/51.65; C52.78/51.00 token/s.
MTP acceptance also differs: A.690, B.678, C.682. These small gains are unstable;
retain neither fetch overlap nor THP/gain3 as enabled production optimizations.
The simplest launch-size-only candidate gets a separate256K ABBA check with
normal vision lending, MTP and INT8 KV asserted, before a final decision.

Routing counters: hits/lookups follows the native cache diagnostic but excludes
PCIe/peer offloads from its denominator. The final JSON summaries additionally
provide routed_vram_fraction, routed_cpu_fraction and routed_offload_fraction
using lookups+offloaded. They describe routed expert assignments, not total model
FLOPs, arithmetic time, GPU utilization, or CPU/GPU hardware idle.


Final 256K production-configuration confirmation (A B B A)

This compares default384 with fetch96 alone, using the minimal candidate binary,
normal image lending enabled, SC117, MTP and INT8 KV. Each arm starts fresh and
warms all seven fixtures; two measured192-token requests per fixture.

| Group | Prefill token/s | Decode token/s | Mean native TTFT s | Mean request s |
|---|---:|---:|---:|---:|
| baseline384 |1455.80|41.09|2.508|7.116|
| fetch96 only |1456.89|41.55|2.492|7.063|

Decode+1.12%, wall-0.74%. Individual arms: A1=40.44, B1=40.49,
B2=42.66, A2=41.75token/s. This is smaller than the between-load variation;
no stable production speedup is established. Keep384 as the default, leave
STRATA_FETCH_BLOCKS unset in production. The opt-in launch-size knob is retained
for future hardware-specific experiments, with identical default math/geometry.
Its independently built binary passes69 native tests with the same two baseline
exclusions. Neither overlap, THP, delayed admission nor gain changes are retained
in production source.

During B2's measured requests the whole-system Linux pswpout counter rose by99710
pages (about389.5MiB with4KiB pages), versus zero in the other three arms. This
is a system counter, not proof that Strata caused it. It is another uncontrolled
memory-pressure factor; do not claim stable optimization from this small delta.
This also supersedes any earlier observation of zero Linux swap during the32K
screen. Windows memory pressure cannot be ruled out by Linux counters.

Across the seven throughput/correctness phases:560 measured requests, excluding
warmups and separate instrumented captures. Max actual input32768 tokens;256K
is configured capacity, not a full256K input quality/performance validation.

The pre-existing api_monitor=false and content-free diagnostics must remain.
Experimental data contains synthetic-workload timings/counters and comparison
booleans, not stored prompts, completions, token IDs, expert IDs or content hashes.


Reproduction and audit artifacts

`candidate-study-20261004/` beside this document contains per-arm/per-domain numeric
summaries, grouped final results, exact PR source revisions, and binary/patch
hashes. The separately delivered study archive contains the exact tested patches,
plans, historical runner revisions, sanitized request counters and diagnostic
captures. Historical runners are evidence: use tools/benchmark_candidates.py for
new runs and tools/summarize_candidates.py for the corrected aggregate rates.
Example after the production service is stopped:

```sh
python tools/benchmark_candidates.py --config ENGINE_ONLY_CONFIG --plan PLAN_JSON \
  --out /mnt/data/NEW_STUDY --warm-all --repeats 2 --tokens 192
python tools/summarize_candidates.py /mnt/data/NEW_STUDY/results.json --out SUMMARY_JSON
```

Plans pin native binaries and supply context, flags and environment independently.
Use the engine-only benchmark config; do not distribute production credentials.
Do not run two engines, build code or run other tests concurrently with timing.
The default profile tool now uses only pcie_frac0.25 unless explicitly overridden.


Multistream diagnostic follow-up (separate instrumented binary)

Both captures use the same own experimental binary with overlap0, gain1.5, lag1,
THP unset, normal shared-expert overlap, and blocks384 versus96. Context32K,
three domains/six measured requests,18 sampled windows per arm; same sampled
41 emitted/50 verified tokens and1636.35MiB transfers. Numeric archives contain
no payloads. Median window37.57->39.06ms, recorded GPU compute union14.93->14.95ms,
transfer union7.03->5.87ms, exposed verify wait4.16->5.66ms. Per-metric medians
are not additive. Adaptive timing includes warmups:159 batches in each arm;
backup mean5.07->5.40ms, admission-copy wait7.62->7.72ms, RAM commit6.81->8.72ms,
residency upload0.079->0.088ms. These overlapping stages are not an additive
critical-path accounting.

Do not hide the outlier: fetch96 request5 has a424.37ms window, including389.73ms
in layer0's GPU wait for CPU results, while the traced CPU expert_pool span is
0.86ms. The data does not identify the synchronization/runtime scheduling or
instrumentation cause. Total exposed-wait/window ratios are11.42% versus44.35%,
the latter dominated by this tail. They are not whole-request GPU hardware idle:
draft and other GPU work are not fully traced. This diagnostic capture does not
establish a throughput regression or improvement; use the uninstrumented ABBA.
The historical timeline website remains unchanged, with its original stamp schema.


Deployment and validation

The approved baseline5bf523a, with matching Python service and native binary,
was installed as an immutable release on the data disk. Native SHA256
9c831e895c4e7b467f38af9eb6e2e35e92f6f5f05e9c9568424d06562ba0b4e9
matches both final256K baseline arms. The production binary is the frozen baseline,
not the optional fetch-knob build; experimental knobs are not enabled.
The prior config remains unchanged and backed up; a systemd drop-in selects the
release. RetainsSC117/MTP/INT8KV/262144/PCIe0.25/images/authenticated0.0.0.0:8080,
api_monitor=false, and content-free diagnostics. No Windows/WSL memory changes.

Real-service smoke checks passed: health/model/context/images/auth,Tailscale address,
unauthenticated rejection,authenticated model list,Chat text,Messages streaming,
Messages structured tool call,image color,and text after image lending.
Service was stopped afterward to restore its initially inactive/manual state;
autostart remains linked, not enabled. Start with systemctl start strata-sc117.

Earlier Python service discovery ran313 tests:302 passed,7 skipped,and4 failed
only because the local test interpreter lacked regex. Those same4 tests passed
in the production venv after deployment staging (before enabling the release).
The stats tool adds3 passing regression tests; compilation and whitespace checks
passed. The setup run covered258 tests,1 skipped and the remainder passed. Known native exclusions
remain exclusions, not passes. No CI result is asserted by this report.

Release root: /mnt/data/strata-deploy/releases/0.1.39-baseline-20261004.
The systemd override70-approved-release.conf points there. Rollback after stopping
strata-sc117: remove only that override symlink and daemon-reload; the previous
unit/config/binary remain available. Never distribute config.json or its backup:
they contain the existing API secret. deployment-summary.json is safe numeric/
boolean evidence and contains no key or generated response content.
