# Recovering a plain answer after premature reasoning EOS

A model can emit an end-of-turn token without `</think>`. A successful HTTP response can then contain only
reasoning, with neither an answer nor a tool call. This guard asks the model to finish its answer; it does not
relabel reasoning as final content.

Community precedents:

- [Halogen Flash Server #84](https://github.com/peonist-ai/halogen-flash-server/issues/84#issuecomment-5783440989)
  ships reasoning EOS protection in 0.13.4, separately recovering complete calls inside reasoning. It normally
  retains an EOS as text and continues. Its later bounded guard closes reasoning after repeated markers.
- [vLLM #55420](https://github.com/vllm-project/vllm/issues/55420) proposes forcing the reasoning-end boundary
  before allowing an answer. At review time this is an open proposal, not an upstream merged implementation.
- [ds4 #1087](https://github.com/antirez/ds4/issues/1087#issuecomment-5737104740) explains why moving all reasoning
  into content prevents empty text but does not produce a completed or schema-conforming answer.

## Strata adaptation

The Python service uses the existing reasoning-budget continuation mechanism rather than changing the native
sampler or importing code from a different engine. The default is enabled. Set `STRATA_REASONING_EOS_RECOVERY=0`
in the server environment and restart to disable it.

On an actual stop token while the parser is still in reasoning, continue only if there has been no answer,
explicit reasoning end, tool event, or complete/partial tool marker. A buffered boundary or incomplete UTF-8
character also prevents recovery. No recovery is attempted on cancellation, disconnect, engine errors, token
budget exhaustion or an entirely empty generation. Existing complete-tool recovery takes priority. Even
quoted, escaped, malformed or unknown tool markers conservatively prevent the prose continuation.

1. Close the generator and drain its native DONE while holding the service FIFO. Tokens queued after EOS
   (including accepted speculative tokens) are not added to the continuation prompt.
2. Reuse original prompt + generated tokens before EOS. Append the existing fixed reasoning-budget wrap-up
   sentence and `</think>`; omit the premature stop token. Start a normal new GEN pass on the same loaded engine.
3. Allow one continuation, at most 2048 generated tokens and never more than the original remaining request
   budget. Injected tokens and the discarded EOS are counted against that budget. MTP remains enabled; GEN
   restores/replays the prefix through the normal engine mechanism.
4. Stream new answer/tool events normally. Never execute tools in this recovery path. A second empty stop is
   reported as empty in diagnostics; reaching the cap remains `length`, not a successful stop.

Normal requests do not gain an extra generation pass. Recovery adds generation latency and may require some
prefix replay. Already streamed thinking cannot be withdrawn. The generated answer is new text, not a guaranteed
reconstruction of an answer embedded in the reasoning. This guard does not repair malformed tools, infer an
arbitrary boundary within prose, or guarantee the accuracy of the new answer.

## Diagnostics and privacy

Each run has `reasoning_eos_recovery`: fixed policy/decision/outcome codes, enabled flag, attempt count,
injected/continuation token counts, answer character count and tool-call count. An injected `</think>` is counted
under `server_injected`, never under model/native output. Native passes remain visible separately. Timing totals
sum native work across completed passes and keep the original request's prompt/cache attribution.

No user message, history, reasoning text, tool name, argument, path value, key or content hash is stored by this
observer. MarkerStream keeps fixed markers/counts only. Existing private rotating diagnostics remain in use.
Do not turn on STRATA_DEBUG or the API payload monitor when deploying this fix.

## Verification

`python3 -m unittest serve.test_reasoning_eos serve.test_recovery serve.test_diagnostics serve.test_server serve.test_structured serve.test_detok`

The new tests cover prefix replay/EOS removal, one-attempt and token limits, tool recovery precedence, partial
markers, no-tool-schema cases, cancellation/disconnect/error, disabled behavior, request isolation, and privacy.
Both API formats in streaming/nonstreaming modes also run through a real StrataEngine subprocess pipe with
scripted tokens, including trailing speculative tokens after EOS. This is deterministic fault injection, not a
claim of reproducing the model's stochastic failure on demand. Production smoke results are recorded separately.

### Song PC acceptance, 2026-10-03

Deployed code commit: `7ebaacf1a0674cf31046be441beeb8397bb6c22e`; previous code:
`daab7ad649143f85d4127c0affaa691fa84cbc24`. SC117 IQ3_S, `qwen3.8-fn`, context 262144,
MTP enabled (MTP cap 4; native verifier capacity `spec=6`, lookup 3). The existing CUDA binary and model configuration
are retained. Service is manually started (`linked`, not enabled for boot), with authenticated `0.0.0.0:8080`.

- All 161 regression tests passed on Song PC using the deployed virtualenv and actual SC117 tokenizer;
  113.073 seconds, no skips. The Debian run lacked `regex` for three tokenizer tests; the target run resolved
  that environment limitation without installing another project environment.
- Four real API cases passed: Anthropic streaming and OpenAI nonstreaming each returned one requested tool
  call; each also returned nonempty prose after a forced thinking-budget close. Both prose cases recorded
  two native generation passes and one server-injected `</think>`. No tools were executed by the probes.
- The missing-EOS-close failure itself was exercised by deterministic fault injection, including native pipe
  draining. The real model smoke used the existing thinking budget to exercise native continuation; it did
  not reproduce a stochastic premature EOS. This distinction matters: these checks are not a measured
  elimination rate for the user's original failure.
- The live probe initially expected Anthropic's `end_turn` for an OpenAI prose reply. Corrected to `stop` and
  revalidated all four saved responses and their completed diagnostic records. No service fix or repeated
  inference was needed for that test assertion.
- All four records had clean parser/API/transport flags. The eight start/end log records passed the fixed-string
  privacy allowlist, with no unapproved values. All diagnostic files were mode 0600. Synthetic fault-injection
  tests also asserted private prompt and reasoning sentinels were absent from logs.

Several probes queued behind live long-context user requests. Their wall times include queueing and are not
performance benchmark numbers. Restart was performed only after observing an idle service; this cold model
load took about four minutes before health was ready.

Target evidence directory: `/mnt/data/strata-deploy/reasoning-eos-20261003/` (tests.log, live-results.json,
deployment.json, acceptance.json, privacy.json). The service diagnostic log remains
`/mnt/data/strata-deploy/logs/output-diagnostics.jsonl`.
