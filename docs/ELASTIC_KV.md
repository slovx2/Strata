# Elastic KV with the resident expert complement

This is an opt-in, single-GPU CUDA path. It preserves the configured context capacity while mapping KV memory only as requests need it. Expert-cache slots temporarily use the rest. GPU virtual addresses stay fixed, so captured graphs continue to point to the same addresses.

The adapted resident-complement path is selected with:

```
STRATA_KV_GROW=1
STRATA_KV_GROW_COMPLEMENT=1
STRATA_KV_GROW_LEND=1
STRATA_KV_GROW_KEEP_RAM=1
```

Use all four for the Song PC image-capable configuration. `STRATA_KV_GROW_COMPLEMENT` alone is the older experiment: an evicted expert may fall back to its model file. `KEEP_RAM` retains RAM copies for the potential KV donor slots and normal prefill/image lending region. Existing physical-memory headroom checks still apply; this is not a way to bypass a machine's RAM limit. Before relinquishing a KV donor slot, the engine checks its RAM coverage.

A long request quiesces pending transfers, publishes changed residency, and remaps physical chunks from expert slots to KV storage. A later short independent request can return surplus chunks and refill expert slots. The full configured context still needs to fit. If growth cannot safely complete, the engine fails the request/process rather than continuing with invalid mappings.

Image lending and KV growth have separate ownership. Images borrow the cache tail; KV borrows below the prefill lending region. Returning an image loan remaps only its tail, preserving any holes whose physical chunks are still owned by KV. Prefill borrowing refuses layouts that overlap those holes. Refilling returned KV slots skips experts still owned by the image loan.

This changes memory placement, not the router or the expert formulas. As with the existing adaptive cache, moving computation between CPU and GPU can change floating-point rounding and the generated sequence. Byte-level storage tests and functional inference checks do not establish semantic equivalence for every possible request.

Not supported by this path: HIP, multiple GPUs, remote expert caches, native parallel batch slots, streamed KV, or the separate segmented/live-memory cache owner. Default behavior remains full KV allocation at startup. The independent exchange-rotation feature needs its own memory and acceptance checks before being combined with this path.

Validation on Song PC includes real 256K-capacity allocations, repeated long-to-short transitions, image loans and reclamation, native GPU mapping/byte tests, and expert-file/swap counters. See the profiling study for the exact tested binary, workloads, results and limitations. No request contents or expert IDs are needed in the logs: capacity, mapping counts, memory and transfer counters are sufficient for this study.
