"""Conservative end-of-turn recovery; no model text or argument values in diagnostics."""
import json
import re

from serve.frontend import (OutputParser, Event, ToolCall, THINK_END, CALL_START, CALL_END,
                            FUNC_END, PARAM_END, param_end)
from serve.diagnostics import MarkerStream


class ReasoningEosGuard:
    """One bounded continuation for a plain, unterminated reasoning turn.

    Inspired by halogen-flash-server #84 and vLLM #55420. Never turn a
    reasoning buffer into an answer, or continue past a possible tool call.
    This observer retains fixed markers/counts only, not the generated prose.
    """
    ANSWER_LIMIT = 2048

    def __init__(self, enabled=True):
        self.enabled = enabled
        self.markers = MarkerStream()
        self.content = False
        self.tool = False
        self.data = {'policy': 1, 'enabled': enabled, 'attempts': 0,
                     'decision': 'not_needed', 'injected_tokens': 0,
                     'continuation_tokens': 0, 'answer_chars': 0,
                     'tool_calls': 0, 'outcome': 'not_attempted'}

    def feed(self, text, events):
        self.markers.feed(text)
        for ev in events:
            self.content |= ev.kind == 'content' and bool(ev.text.strip())
            self.tool |= ev.kind.startswith('tool')
            if self.data['attempts']:
                if ev.kind == 'content':
                    self.data['answer_chars'] += len(ev.text)
                if ev.kind == 'tool_call':
                    self.data['tool_calls'] += 1

    def decide(self, parser, finish, cancel, pending_bytes, remaining, injected):
        if finish != 'stop' or parser.state != 'reasoning':
            return False
        reason = 'continue'
        if cancel:
            reason = 'cancelled'
        elif not self.enabled:
            reason = 'disabled'
        elif self.data['attempts']:
            reason = 'attempt_limit'
        elif self.content or self.tool or self.markers.counts.get('</think>'):
            reason = 'answer_or_boundary_seen'
        elif parser.candidate or any('tool_call' in m or 'function' in m or 'parameter' in m
                                     for m in self.markers.counts):
            reason = 'tool_marker_seen'
        elif parser.buf or parser.pending or self.markers.pending or pending_bytes:
            reason = 'partial_boundary'
        elif not self.markers.chars:
            reason = 'empty_generation'
        elif remaining <= injected:
            reason = 'no_answer_room'
        self.data['decision'] = reason
        return reason == 'continue'

    def finish(self, finish):
        if self.data['attempts']:
            self.data['outcome'] = ('tool_call' if self.data['tool_calls'] else
                                    'answer' if self.content else 'empty') if finish == 'stop' else finish


class RecoveryRejected(Exception):
    pass


def strict_calls(text, schemas):
    """Consume ONLY complete calls and whitespace. Validate all before emitting any.

    Unlike the ordinary parser, recovery must not repair XML, accept duplicate keys,
    silently coerce malformed JSON, or fetch remote JSON-schema references.
    """
    try:
        from jsonschema import validators
        from referencing import Registry
        from referencing.exceptions import NoSuchResource
    except ImportError:
        raise RecoveryRejected('validator_unavailable') from None

    def no_remote(uri):
        raise NoSuchResource(ref=uri)

    calls = []
    text = text.strip()
    while text:
        if len(calls) >= 16:
            raise RecoveryRejected('too_many_calls')
        if not text.startswith(CALL_START):
            raise RecoveryRejected('trailing_or_mixed_text')
        text = text[len(CALL_START):].lstrip()
        match = re.match(r'<function=([^<>\s]+)>', text)
        if not match:
            raise RecoveryRejected('malformed_function')
        name = match[1]
        if name not in schemas:
            raise RecoveryRejected('unknown_tool')
        schema = schemas[name].get('parameters')
        if schema is None:
            schema = {'type': 'object'}
        if not isinstance(schema, dict) or not isinstance(schema.get('properties', {}), dict):
            raise RecoveryRejected('schema_unverifiable')
        props = schema.get('properties', {})
        text = text[match.end():].lstrip()
        args = {}
        while text.startswith('<parameter='):
            match = re.match(r'<parameter=([^<>\s]+)>', text)
            if not match:
                raise RecoveryRejected('malformed_parameter')
            key = match[1]
            if key in args:
                raise RecoveryRejected('duplicate_parameter')
            text = text[match.end():]
            end = param_end(text)
            if end < 0:
                raise RecoveryRejected('incomplete_parameter')
            value = text[:end]
            # Preserve the template's single framing newline, exactly like parse_tool_call.
            if value.startswith('\n'):
                value = value[1:]
            if value.endswith('\n'):
                value = value[:-1]
            property_schema = props.get(key, {})
            declared = property_schema.get('type') if isinstance(property_schema, dict) else None
            if declared == 'string':
                args[key] = value
            else:
                try:
                    def unique(pairs):
                        result = {}
                        for k, v in pairs:
                            if k in result:
                                raise ValueError('duplicate')
                            result[k] = v
                        return result
                    def bad_constant(_):
                        raise ValueError('nonfinite')
                    args[key] = json.loads(value, object_pairs_hook=unique, parse_constant=bad_constant)
                except ValueError:
                    raise RecoveryRejected('invalid_parameter_json') from None
            text = text[end + len(PARAM_END):].lstrip()
        if not text.startswith(FUNC_END):
            raise RecoveryRejected('missing_function_end')
        text = text[len(FUNC_END):].lstrip()
        if not text.startswith(CALL_END):
            raise RecoveryRejected('missing_call_end')
        text = text[len(CALL_END):].lstrip()
        try:
            cls = validators.validator_for(schema)
            cls.check_schema(schema)
            validator = cls(schema, registry=Registry(retrieve=no_remote))
            if next(validator.iter_errors(args), None) is not None:
                raise RecoveryRejected('schema_validation_failed')
        except RecoveryRejected:
            raise
        except Exception:
            raise RecoveryRejected('schema_unverifiable') from None
        calls.append(ToolCall(name=name, arguments=args))
    return calls


class RecoveryContext:
    """Track code delimiters at the candidate, not whether code ever appeared.

    Only the current bounded line and the previous nonempty line are kept in
    memory. Neither is exposed by snapshot(). Unsupported/oversized context
    fails closed. This is a conservative Markdown heuristic, not intent detection.
    """
    MAX_LINE = 8192

    def __init__(self):
        self.line = ''
        self.previous = ''
        self.fence = None
        self.inline = 0
        self.overflow = False

    @staticmethod
    def advance(line, fence, inline, complete):
        start = re.match(r' {0,3}(`{3,}|~{3,})(.*)$', line)
        if fence:
            if complete and start and start[1][0] == fence[0] and len(start[1]) >= fence[1] and not start[2].strip():
                return None, 0
            return fence, inline
        if not inline and start:
            return (start[1][0], len(start[1])), 0
        for ticks in re.finditer(r'(?<!\\)`+', line):
            width = len(ticks[0])
            if not inline:
                inline = width
            elif inline == width:
                inline = 0
        return None, inline

    def feed(self, text):
        for part in text.splitlines(keepends=True):
            full = self.line + part
            if len(full) > self.MAX_LINE:
                self.overflow = True
            self.line = full[:self.MAX_LINE]
            if part.endswith(('\n', '\r')):
                line = self.line.rstrip('\r\n')
                self.fence, self.inline = self.advance(line, self.fence, self.inline, True)
                if line.strip():
                    self.previous = line[-512:]
                self.line = ''

    def snapshot(self):
        fence, inline = self.advance(self.line, self.fence, self.inline, False)
        nearby = self.line.strip() or self.previous.strip()
        # A previous paragraph mentioning an example must not taint a later action.
        example = bool(re.search(r'(?i)\bexample\b|\be\.g\.|\bfor instance\b|\bformat\s*[:：]|示例|例如|举例|格式如下', nearby))
        quote = (nearby in ('"', "'", '“', '「') or nearby.startswith('>')
                 or nearby.endswith(('"', "'", '“', '「')))
        return {'policy': 2, 'in_fenced_code': bool(fence), 'in_inline_code': bool(inline),
                'nearby_example_cue': example, 'quote_context': quote, 'context_limit': self.overflow}

    def reason(self):
        flags = self.snapshot()
        for key, reason in [('context_limit', 'context_limit'), ('in_fenced_code', 'inside_fenced_code'),
                            ('in_inline_code', 'inside_inline_code'), ('nearby_example_cue', 'nearby_example_cue'),
                            ('quote_context', 'quoted_call_context')]:
            if flags[key]:
                return reason
        return None


class RecoveringOutputParser(OutputParser):
    """Hold calls inside thinking until a real close or a normal EOS determines routing.

    Already-emitted reasoning is never retracted. A rejected tail is returned unchanged
    as reasoning. No general guess about where a prose answer starts is attempted.
    """
    MAX_HOLD = 256 * 1024

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.pending = ''
        self.context = RecoveryContext()
        self.active = self.state == 'reasoning' and bool(self.schemas)
        self.candidate = False
        self.finished = False
        self.recovery = {'decision': 'none', 'buffered_chars': 0, 'recovered_calls': 0,
                         'released_reasoning_chars': 0, 'explicit_close': False, 'duplicate_recovered_calls': 0,
                         'candidate_validation': 'not_attempted', 'candidate_call_count': 0}

    def _context(self, text):
        self.context.feed(text)

    def feed(self, delta):
        if self.finished:
            raise RuntimeError('feed after finish')
        if not self.active:
            return super().feed(delta)
        self.pending += delta
        close = self.pending.find(THINK_END)
        call = self.pending.find(CALL_START)
        if close >= 0:
            if self.candidate or 0 <= call < close:
                self.recovery.update(decision='explicit_close_kept_reasoning', explicit_close=True,
                                     released_reasoning_chars=close)
            self.active = False
            text, self.pending = self.pending, ''
            return super().feed(text)
        if call >= 0:
            out = []
            if not self.candidate:
                prefix = self.pending[:call]
                self._context(prefix)
                out = super().feed(prefix)
                self.pending = self.pending[call:]
                self.candidate = True
                self.recovery['decision'] = 'buffering'
            self.recovery['buffered_chars'] = len(self.pending)
            if len(self.pending) > self.MAX_HOLD:
                self.active = False
                self.recovery.update(decision='buffer_limit', released_reasoning_chars=len(self.pending))
                text, self.pending = self.pending, ''
                out += super().feed(text)
            return out
        keep = self._hold(self.pending, (THINK_END, CALL_START))
        safe = len(self.pending) - keep
        text, self.pending = self.pending[:safe], self.pending[safe:]
        self._context(text)
        return super().feed(text)

    def finish(self, finish_reason='length'):
        if self.finished:
            return []
        self.finished = True
        out = []
        if self.active and self.candidate:
            reason = None
            if finish_reason != 'stop':
                reason = 'non_normal_finish'
            else:
                validation_error = None
                try:
                    calls = strict_calls(self.pending, self.schemas)
                    self.recovery.update(candidate_validation='valid', candidate_call_count=len(calls))
                except RecoveryRejected as exc:
                    validation_error = str(exc)  # fixed codes, never payload or schema messages
                    self.recovery['candidate_validation'] = validation_error
                reason = self.context.reason() or validation_error
                if reason is None:
                    # The base parser can still hold a prefix ('<') from earlier reasoning.
                    out += super().finish()
                    for call in calls:
                        if self.stream_tools:
                            out += [Event('tool_start', call=call),
                                    Event('tool_args', json.dumps(call.arguments, ensure_ascii=False), call=call)]
                        out.append(Event('tool_call', call=call))
                    self.recovery['duplicate_recovered_calls'] = sum(
                        any(c.name == prev.name and c.arguments == prev.arguments for prev in calls[:i])
                        for i, c in enumerate(calls))
                    self.pending = ''
                    self.state = 'content'
                    self.recovery.update(decision='recovered_missing_think_end', recovered_calls=len(calls))
            if reason:
                self.recovery.update(decision=reason, released_reasoning_chars=len(self.pending))
        if self.pending:
            out += super().feed(self.pending)
            self.pending = ''
        out += super().finish()
        return out

    def diagnostic_snapshot(self):
        return {**self.recovery, 'guard': self.context.snapshot(),
                'pending_chars': len(self.pending), 'candidate_seen': self.candidate}
