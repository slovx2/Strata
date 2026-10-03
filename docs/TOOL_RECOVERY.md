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
The example check is deliberately conservative: any earlier backtick/tilde, or
an example cue in the last 512 reasoning characters, blocks recovery. It cannot
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
