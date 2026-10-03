"""Conservative end-of-turn recovery; no model text or argument values in diagnostics."""
import json
import re

from serve.frontend import (OutputParser, Event, ToolCall, THINK_END, CALL_START, CALL_END,
                            FUNC_END, PARAM_END, param_end)


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
        schema = schemas[name].get('parameters') or {'type': 'object'}
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


class RecoveringOutputParser(OutputParser):
    """Hold calls inside thinking until a real close or a normal EOS determines routing.

    Already-emitted reasoning is never retracted. A rejected tail is returned unchanged
    as reasoning. No general guess about where a prose answer starts is attempted.
    """
    MAX_HOLD = 256 * 1024

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.pending = ''
        self.context = ''
        self.code_seen = False  # deliberately conservative, even for earlier closed code
        self.active = self.state == 'reasoning' and bool(self.schemas)
        self.candidate = False
        self.finished = False
        self.recovery = {'decision': 'none', 'buffered_chars': 0, 'recovered_calls': 0,
                         'released_reasoning_chars': 0, 'explicit_close': False, 'duplicate_recovered_calls': 0}

    def _context(self, text):
        self.code_seen |= '`' in text or '~' in text
        self.context = (self.context + text)[-512:]

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
            elif self.code_seen or re.search(r'(?i)example|e\.g\.|for instance|示例|例如|举例|格式如下', self.context) \
                    or self.context.rstrip().endswith(('"', "'", '“', '「', '>', ':', '：')):
                reason = 'example_or_quote_context'
            else:
                try:
                    calls = strict_calls(self.pending, self.schemas)
                except RecoveryRejected as exc:
                    reason = str(exc)  # only fixed codes defined above
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
        return {**self.recovery, 'pending_chars': len(self.pending), 'candidate_seen': self.candidate}
