# Recovering calls in unclosed thinking

When a thinking reply contains `<tool_call>` but ends normally without `</think>`,
the service holds the candidate tail in memory and validates it before emitting
structured tool calls. Both Messages and Chat Completions use the same parser,
streaming or collected. The API returns calls; the client executes its tools.

Recovery requires only complete calls plus whitespace through the end of the
reply, tools declared by the request, unique parameter keys, and valid JSON-schema
arguments. All calls must pass before any are emitted. Remote schema retrieval
is disabled. `jsonschema==4.25.1` is required for recovery; if unavailable, the tail
remains reasoning with `validator_unavailable`. No values are invented.

A real `</think>` takes precedence: everything preceding it remains reasoning.
Recovery is refused on length limits, cancellation, error, examples/quotes,
incomplete XML, unknown tools, invalid arguments or mixed prose after calls.
Guard policy 2 tracks whether the candidate is currently inside a fenced or
inline code block; earlier closed code and ordinary tildes do not block recovery.
Example cues are checked on the current/nearest nonempty line, rather than the
whole recent reasoning tail. An ambiguous quote prefix still blocks recovery.
The current context line is bounded to 8192 characters; overflow refuses recovery. It cannot
perfectly distinguish an unmarked example from an intended call. Candidate
buffering is capped at 256 Ki characters and 16 calls; exceeding a limit leaves
the output as reasoning. This can produce false negatives rather than inventing
an operation. Rejected output is preserved, not silently deleted.

No repair is attempted for a plain answer trapped in thinking, stray closing
markers, escaped XML, or an unfinished ordinary call after an explicit boundary.
Those cases remain visible through the redacted diagnostics. This is a format
mitigation, not a correction to weights, quantization, CUDA or repetitive output.
Repeated identical recovered calls are counted, not automatically deduplicated:
two calls can be intentional, and the client retains responsibility for execution.

The approach was cross-checked against TensorFold v0.3.6.2's `split_thinking` in
[src/tensorfold/server/text.py](https://github.com/ashhart/TensorFold/blob/v0.3.6.2/src/tensorfold/server/text.py)
and [vLLM #35687](https://github.com/vllm-project/vllm/pull/35687). This implementation
adds strict validation and waits for normal EOS instead of immediately treating
any call opener as an implicit close. It does not rewrite earlier streamed text.

## Validation

`python -m unittest serve.test_recovery serve.test_diagnostics serve.test_server serve.test_detok`

Synthetic tests split delimiters at every boundary, check two calls and unique
IDs, later real closure, schema errors, examples, truncated/cancelled replies,
bounded buffers, both API formats and streaming modes, native stdout observation,
redaction, and structured thinking/tool history. Synthetic tool calls are never
executed. Passing these checks cannot establish real-workload model reliability.


## Guard policy 2 diagnostics

`recovery.guard` contains only booleans: code-fence state, inline-code state,
nearby example cue, quote cue and context limit, plus the policy version. Reasons
are now distinct (`inside_fenced_code`, `inside_inline_code`, `nearby_example_cue`,
`quoted_call_context`, `context_limit`). No surrounding text is logged.

On a normal EOS, candidate syntax/schema validation runs even when the context
guard refuses recovery. `candidate_validation` reports a fixed code and
`candidate_call_count` reports the number of validated calls. This distinguishes
an overbroad context refusal from incomplete XML or invalid arguments without
retaining those arguments. On non-normal finishes validation is not attempted.
