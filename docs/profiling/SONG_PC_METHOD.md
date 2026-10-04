# Song PC profiling protocol

This experiment locates bottlenecks in the deployed SC117 IQ3_S engine on Song PC. It does not change production settings or apply speculative optimizations. The production service must already be stopped; the foreground runner closes every child engine on completion. Results and runtime logs belong on `/mnt/data`.

## Reproduce

Run `tools/profile_song_pc.py` from a checkout with the deployment's Python under `direct-env.sh`, supplying `--config /mnt/data/strata-deploy/strata-sc117-iq3_s.json --out /mnt/data/strata-deploy/profile-YYYYMMDD`. The config is read, its API key discarded, and a separate native engine launched without an HTTP listener. All three arms use the original executable, MTP policy, expert adaptation, vision reservation and precision. Only context capacity (32768), prefix caching (disabled), and the documented measurement switches change.

The native engine's `StrataEngine` interface is the same one used by the server. Measurements exclude HTTP, network and tokenizer overhead. The warmup and fixture order are matched; the second repetition reverses domain order. An uninstrumented final arm checks drift in three representative 4K workloads. Model-load time is separate. No operating-system caches are forcibly dropped.

## Workloads

All inputs are synthetic. Six approximately 4096-token inputs cover Go code, networking, Chinese narrative, mathematics, English science and multilingual JSON. Networking also has approximately 256- and 16384-token variants. Each request is independent, greedy, thinking-disabled, with a 192-token output ceiling; measured actual output counts and termination are retained. No long conversation benchmark is used.

The six domains are intended to vary routing, not guaranteed to cause misses. Actual expert hit counts and PCIe expert counts establish whether misses and transfers occurred. Repetitions share an adaptive expert cache but never reuse a conversation prefix. Domain/order effects must be reported rather than interpreted as controlled cold-cache measurements.

An additional counterbalanced sweep sets request-local `strata_tune.pcie_frac` to 0, 0.25, 0.55 and 1, in forward then reverse order on three domains. This affects the split of nonresident decode experts between CPU and GPU. It does not allocate a different GPU cache. Routing/outputs can change with numerical paths; this is an application throughput comparison, not a teacher-forced identical-token microbenchmark. Do not automatically promote a setting based on two trials.

## Counter denominator correction

`src/core/expert_source.cpp` increments `cache_hits` for resident entries and `cache_refused` only for CPU entries. Entries assigned to PCIe bypass both counters. The native `hits/lookups` ratio is therefore not the share of all routing served from VRAM, and rises artificially when more work moves to PCIe. The parser preserves that ratio only as `native_hit_ratio_pct` and computes the true routed-entry denominator as `(drafts_offered + decode_windows) * 48 * 10`. For original baseline requests without window counters, it reports bounds allowing up to 5 extra accepted tokens at the output cap (the deployed verifier capacity is 6). Displayed baseline percentages are midpoints of those bounds, explicitly estimates.

`--arms sweep --pcie-fracs 0.0,0.25,0.55` repeats the transfer-policy experiment without GPU timestamp probes. It enables only the host timing report, whose counters the existing native loop already accumulates. This supplies exact window counts with no added GPU kernels.

## What the timers mean

- The printed prefill `host staging` counter is cumulative across requests; the parser differences it within each engine lifetime before displaying it.
- `STRATA_PREFILL_TIMING`: CUDA events on the compute stream. Stage intervals include host starvation and waits for copies; `wait copy` is exposed waiting, not total DMA busy time. CPU and copy-engine activity overlaps this timeline.
- `STRATA_DECODE_TIMING`: request-local wall-clock counters for verification, commit/emission and drafting. Verification also reports host wait, planning and expert-pool execution. These are nested counters, not independent additive costs.
- `STRATA_VERIFY_PROFILE`: GPU global-timer stamps inside the actual captured verifier graph. GDN and QSA stages are aggregated separately. `waitA` includes waiting for the host plan, `waitB` waiting for staged experts, `waitCPU` waiting for CPU results. `PCIe grp` is GPU computation of the transferred group, not the transfer duration. Their intervals include dependencies, not pure arithmetic.
- Verifier counters may include short-prompt windows. The parser marks a GPU profile decode-only only when its window count matches the decode counter. It must not silently attribute prefill windows to decode.
- These probes aggregate layers by type, not by individual layer ID. Dividing a GDN sum by 36 or QSA sum by 12 produces a type-average, not the slowest layer. The head is reported in the GDN bucket and must be excluded from a per-layer average.
- MTP makes a verification window different from an output token. Report milliseconds per window and accepted/emitted tokens per window; divide by that yield for milliseconds per output token.
- `--stage-timing` and `--gpu-only-full` are not used: changing graph execution or removing experts changes the workload.

GPU utilization from nvidia-smi includes active wait/spin kernels; a high percentage is not proof that arithmetic units are saturated.

Detailed timers themselves perturb execution. Use uninstrumented throughput for headline speed; compare matched before/profile/after arms before assigning fine-grained percentages. No claim of exact standalone CPU/GPU/PCIe resource utilization should be made from overlapping stage timers.

## Privacy and acceptance

The runner reads no real conversations. Saved request results contain counts, fixed fixture identifiers, numeric timings and whitelisted native diagnostic lines. Generated text is decoded transiently to check nonempty output, then discarded; no output IDs, text or content hashes are persisted. Synthetic inputs are included for reproducibility. Credentials are excluded from saved configs.

A successful run checks 32768 context, no prefix reuse, nonempty generated output, foreground child cleanup and unchanged production config. Resource sampling records device utilization, clocks, power, temperature, memory, swap, faults and disk counters. Those are system-wide, not exact per-process attribution; loading and request intervals are distinguished.
