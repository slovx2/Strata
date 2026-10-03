# Recovery context guard correction — 2026-10-03

Request `90bc45c7523748ad9511a5d5e7dccb60` ended at 12:39:38 UTC (20:39:38 UTC+8).
It emitted 621 tokens out of a 16384-token budget and stopped normally. Native
stdout, service tokens and DONE counts agreed. Neither native observation nor
detokenized text contained `</think>`. There was one pair each of tool/function/
parameter markers. No disconnect or observation error occurred.

The recovery decision was `example_or_quote_context`: a 726-character candidate
was buffered and then returned unchanged to reasoning, with no structured tool
call. Prepared and written APIs agreed on `end_turn`. This was a recovery refusal,
not transport loss. Tag pairing alone does not prove valid arguments/schema.
The preceding eight requests included two successful missing-close recoveries.

The original guard was overbroad: any earlier backtick or tilde permanently
blocked recovery, even after closed inline code or a closed fenced block. It also
searched the entire last 512 characters for example cues and grouped several
refusal conditions under one code. The redacted record cannot reveal which exact
condition fired; no raw request/output was retained and no exact replay is claimed.

Guard policy 2 tracks current fenced/inline code state across chunks. Closed code
and ordinary tildes no longer taint subsequent calls. Example cues are limited to
the current or nearest nonempty line. Ambiguous quote prefixes and actually open
code still refuse recovery. Context memory is bounded and never serialized.

Logs now expose separate boolean guard flags and fixed refusal codes. Normal-EOS
candidates also undergo full syntax/schema validation even if a context guard
refuses them: logs state whether validation passed and how many complete calls it
found. Actual arguments, tool names and surrounding text remain omitted.

Validation covers every two-part chunk split and one-character streaming for
closed code followed by an independent call, closed fences, tilde text, earlier
example discussion, nested backtick widths, still-open code, context bounds and
privacy. Existing EOS-only, later-real-close, schema, API and native-pipe tests
remain in place. Synthetic tool calls are never executed.
