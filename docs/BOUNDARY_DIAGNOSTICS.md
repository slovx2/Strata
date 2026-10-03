# Redacted inference boundary diagnostics

Set `STRATA_DIAGNOSTIC_LOG` to an absolute JSONL path before starting the Python
server. Its parent directory must already exist. This is separate from
`STRATA_DEBUG` and `api_monitor`: leave both of those disabled because they can
retain real content. No tokenizer, template, sampling or parser behavior changes.

Each authenticated generation request gets a random `X-Strata-Diagnostic-ID`
response header and start/end records. The logs contain only fixed marker strings,
omitted character counts, token counts, parser states/events, API event counts,
HTTP status and allowlisted finish reasons. No request messages, image data,
output prose, tool names/arguments, paths, headers, credentials, content hashes,
arbitrary token sequences or exception messages are written. `stop_token_id`
is only the configured control token that terminated a generation.

The sink writes a 4 MiB active file and retains three rotated files (about
16 MiB total), with mode 0600. One server process should own a log path. Within
a request, keep the last eight engine runs, last 64 marker/omission entries per
source per run, and last 32 state changes. Total marker/event counts and first
marker positions remain available when the tail is truncated. Plain text is
represented by `omitted_chars` and `trailing_omitted_chars`, never excerpts.
Tag prefixes are recognized across token/chunk boundaries; escaped spellings are
counted separately and not interpreted as real protocol markers.

## Reading a failure

- `model_think_end_token_sequences` counts the tokenizer's encoding of
  `</think>` in tokens returned by the engine, before detokenization. The full
  token stream is never retained by diagnostics.
- `model_output.markers` describes text passed to the parser. A think-end token
  sequence without the corresponding text marker points at the detokenization
  boundary. `detokenizer_pending` records an unfinished UTF-8 boundary.
- `server_injected` is separate: a reasoning-budget wrap-up added by the server
  must not be mistaken for a boundary emitted by the model.
- `tool_marker_before_think_end` / `ended_in_reasoning` are observations, not a
  root-cause verdict. A tool marker with no model think-end, ending in reasoning,
  explains why this parser emits reasoning instead of a tool call. It does not
  distinguish weights from template/history or numerical inference issues.
- Compare `parser_events`, `api_prepared` and `http_written`. The latter is
  counted only after a successful socket write and flush. Prepared tool events
  absent from written events point at the HTTP path/disconnect. Written tool
  events mean the server handed them to the socket, not proof Pi received or
  interpreted them; client/network investigation still requires client evidence.
- `finish`, `stop_token_seen`, `parser_finish_called`, `stop_reasons`,
  `disconnected` and `http_status` distinguish EOS, output budget, interrupted
  streams and errors. A start without an end may indicate an active request,
  process termination, rotation or a logging failure; it is not proof of a hang.

Only request completion writes the detailed record (two disk writes per normal
request, no per-token disk I/O). A process crash can therefore lose that request's
detailed evidence. Runtime write failures warn once without dumping payloads or
interrupting inference; an invalid startup path fails startup visibly.

## Song PC

Production uses `/mnt/data/strata-deploy/logs/boundary-diagnostics.jsonl`.
The service's environment drop-in enables this variable and removes
`STRATA_DEBUG`; full API monitoring remains disabled. Keep production at 256K,
MTP disabled and manual startup. Enabling/disabling diagnostics needs a Python
service restart; it does not require rebuilding the CUDA engine.

Run `python -m unittest serve.test_diagnostics serve.test_server
serve.test_structured serve.test_detok` from the repository. The dedicated tests
use an HTTP mock engine to cover missing/present think boundaries, both API
formats and streaming modes, thinking disabled, truncated calls, chunk-split
markers, privacy and bounded retention. Live smoke tests use synthetic prompts;
no model-selected command is executed.

## Native stdout boundary (schema 2)

`engine_pipe` observes C++ `T <id>` lines in the stdout pump, **before** Python
queues them and before cancellation/stop filtering, detokenization or parsing.
The observer is attached after model loading, while the generation FIFO is held,
and removed after the generator drains its DONE line, before the FIFO is released.
It also observes tokens drained after a consumer stops, so a late boundary after
EOS can be distinguished from a delimiter that was never emitted.

The pipe independently counts the think-end token sequence and scans individual
token bytes for fixed ASCII markers. `raw_bytes.chars` and marker offsets here
are byte counts (Latin-1 is used solely for a one-to-one byte scan), unlike the
Unicode character counts at the parser input. No raw token stream/bytes are
persisted. DONE records contain native generated counts, observed pipe counts and
an allowlisted finish reason; keep at most eight, plus total pass counts.
Observation failures are counted without exposing exception text or interrupting
the engine pump. A mock engine has `attached=false`, so zero counts there are not
evidence about native output.

For native requests, compare `engine_pipe.think_end_sequences`,
`engine_pipe.raw_bytes.markers`, `model_think_end_token_sequences`,
`model_output.markers`, parser events, then API/HTTP events. If the delimiter is
absent already at the native pipe and the observation has no errors, the Python
parser did not lose it. This still does not separate model weights, native
numerical computation, sampling, or prompt/template effects. Nonzero pipe counts
with zero downstream counts locate the loss before parsing (check EOS/draining).

Set `STRATA_DIAGNOSTIC_VARIANT=sc117-iq3_s` or `orca-iq3_xxs` to label the actual
loaded weights behind a shared API alias; other values are logged as `other`.
The original redaction, rotation and permissions apply. Tests include a real
scripted subprocess/pipe, both missing/present delimiters, and a delimiter emitted
after EOS to exercise draining. This requires no CUDA rebuild.
