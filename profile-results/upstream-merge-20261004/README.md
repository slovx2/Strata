# Upstream merge smoke and timing regression

See `docs/profiling/UPSTREAM_20261004.md` for method, exclusions and limitations.

- Baseline executable: deployed 0.1.36 from `production-no-mtp-20261003/strata` (historical directory name; MTP is ON).
- Candidate: complete CUDA Release / sm_89 build of this merge with upstream `6f32ec070f23ced9f50e704d854d775da52591ab`.
- Synthetic inputs from `tools/profile_song_pc.py`; fixed 32K cap, `pcie_frac=0.25`, no prefix reuse, no probes, no concurrent requests.
- `tools/validate_upstream_merge.py` runs the same warmups and measured requests for each arm. Measurements are sequential A/B, not an ABBA significance study.
- Numeric results only; no prompts, completions, token IDs, credentials or content hashes saved.
- 4K aggregate improves in this run; 256-token prefill regresses. Production is unchanged. No 256K or image end-to-end acceptance is claimed.
