# Iterating without reloading the model

This describes the deployed Song PC source at `7ebaacf` and the benchmark tools built on it. It is an implementation inspection, not a newly implemented hot-reload feature.

## Existing request-time settings

`StrataEngine.sampling_keys()` in `serve/server.py` forwards `sampling['strata_tune']['pcie_frac']` and `spec_min_p` to the resident native engine's GEN command. The native request loop initializes these values from launch defaults for every request, applies request overrides, and updates the expert dispatcher before generation. An override does not persist to the next request when omitted.

The benchmark therefore starts the engine once and changes these values between requests. No model or CUDA context is reloaded. The ordinary OpenAI/Anthropic handlers pass the request sampling dictionary to this path too; the extension is a Strata-specific top-level `strata_tune` object, not an OpenAI-standard setting.

Examples for an existing resident engine:

```python
sampling = {'temperature': 0, 'strata_tune': {'pcie_frac': 0.2}}
for token in engine.generate(prompt_ids, 192, sampling, cancel):
    # Consume transiently; do not persist private conversations.
    pass
```

Changing the launch JSON on disk is different: the existing process does not reread it. A changed `--pcie-frac` launch default takes effect on the next start unless each request explicitly overrides it. CPU worker count, native executable and allocated maximum context still require engine recreation in the existing design.

## Why restarting the API currently reloads the model

The Python API process creates `strata --serve` using anonymous stdin/stdout pipes (`StrataEngine.__init__`). Its normal shutdown calls `engine.close()`, which sends QUIT. The native engine also exits when stdin closes. Song PC's systemd unit uses `KillMode=control-group`, so service restart ends both processes. Merely changing that setting does not provide reconnectable pipes or a safe ownership model.

The parser/recovery classes are imported once into `serve/server.py`. A new parser object is created per request, but it uses the already imported class. Editing a `.py` file does not update it. There is no existing SIGHUP/importlib reload endpoint. Changes to compiled C++ scheduling logic or CUDA kernels similarly do not replace code inside a running engine.

## Practical development options

1. **Parser-only changes: offline replay first.** Feed synthetic token/text fragments to the parser and recovery unit tests. This needs no model at all and makes chunk boundaries, malformed tool markup and missing think delimiters reproducible. Do not capture real conversations as fixtures without explicit authorization.
2. **Limited parser reload: feasible, but new work.** At an idle request boundary, load a versioned parser factory and swap it only after validation; retain old classes for any existing request and roll back on failure. Plain `importlib.reload()` is insufficient: `from ... import ...` references, dependent modules, and base/subclass identities must be replaced consistently. This does not cover arbitrary server or native scheduler changes.
3. **Frequent API iteration: separate engine ownership.** A small stable local broker owns the native child and its pipes; restartable API/parser workers connect over a permission-restricted Unix socket. The broker serializes requests and owns cancellation, token drain, disconnects and engine shutdown. Keep generation state out of the restartable worker where continuity is required; expose no unauthenticated network listener. This is a moderate refactor, not a configuration switch.
4. **Native scheduler/kernel edits: still restart the engine.** Retaining Python or OS file cache cannot retain the old process's GPU allocations and CUDA graphs across a new executable. A dynamic native-plugin/state-migration architecture would be substantially more complex than justified for the current tuning task.

For the current PCIe sweep, option 1 is unnecessary and no architectural change is needed: existing request-time overrides already avoid repeated loading. The production architecture is unchanged by this investigation.
