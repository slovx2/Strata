"""Opt-in, content-free inference boundary diagnostics. Never log payload excerpts."""
from __future__ import annotations

from collections import Counter, deque
from logging.handlers import RotatingFileHandler
import json
import logging
import os
import time
import threading
import uuid

MARKERS = ('<|im_end|>', '<|endoftext|>', '<think>', '</think>', '<tool_call>', '</tool_call>', '<function=',
           '</function>', '<parameter=', '</parameter>',
           '&lt;/think&gt;', '&lt;tool_call&gt;', '&lt;/tool_call&gt;',
           '&lt;/parameter&gt;', '&amp;lt;/think&amp;gt;', '&amp;lt;/parameter&amp;gt;')
EVENTS = {'reasoning', 'content', 'tool_start', 'tool_args', 'tool_call'}
STATES = {'reasoning', 'content', 'call'}
STOPS = {'stop', 'length', 'cancel', 'disconnect', 'error', 'end_turn', 'tool_use', 'tool_calls', 'max_tokens'}


def known(value, choices):
    return value if isinstance(value, str) and value in choices else 'other'


class MarkerStream:
    """Only possible marker prefixes are held, even for unbounded text and split tags."""
    def __init__(self):
        self.pending = ''
        self.omitted = 0
        self.chars = 0
        self.counts = Counter()
        self.first_at = {}
        self.timeline = deque(maxlen=64)
        self.entries = 0

    def _add(self, item):
        self.entries += 1
        self.timeline.append(item)

    def _omit(self):
        if self.omitted:
            self._add({'omitted_chars': self.omitted})
            self.omitted = 0

    def feed(self, text):
        for char in text:
            self.chars += 1
            self.pending += char
            while self.pending:
                if self.pending in MARKERS:
                    self._omit()
                    self.counts[self.pending] += 1
                    self.first_at.setdefault(self.pending, self.chars)
                    self._add({'marker': self.pending, 'char_end': self.chars})
                    self.pending = ''
                    break
                if any(marker.startswith(self.pending) for marker in MARKERS):
                    break
                self.omitted += 1
                self.pending = self.pending[1:]

    def snapshot(self):
        # Do not flush a pending prefix: snapshots can be taken before the stream ends.
        return {'chars': self.chars, 'markers': dict(self.counts), 'first_marker_char_end': self.first_at,
                'tail': list(self.timeline),
                'tail_entries_dropped': max(0, self.entries - 64),
                'trailing_omitted_chars': self.omitted + len(self.pending),
                'partial_marker_at_end': bool(self.pending)}


class PipeDiagnostic:
    """Observe the native stdout BEFORE queueing, independently of Detokenizer/OutputParser.

    Arbitrary token IDs/bytes never enter a record. Latin-1 preserves ASCII delimiters
    and gives byte offsets without sharing the server's incremental UTF-8 decoder.
    """
    def __init__(self, tokenizer, think_end_ids):
        self.tok = tokenizer
        self.think_end_ids = think_end_ids
        self.lock = threading.Lock()
        self.tail = deque(maxlen=max(1, len(think_end_ids)))
        self.markers = MarkerStream()
        self.tokens = 0
        self.think_end = 0
        self.read_errors = 0
        self.error_lines = 0
        self.done = deque(maxlen=8)
        self.pass_tokens = 0
        self.passes = 0
        self.attached = False

    def observe(self, line):
        with self.lock:
            try:
                if line.startswith('T '):
                    token = int(line[2:])
                    self.tokens += 1
                    self.pass_tokens += 1
                    self.tail.append(token)
                    if self.think_end_ids and tuple(self.tail) == self.think_end_ids:
                        self.think_end += 1
                    raw = self.tok.token_bytes(token) if hasattr(self.tok, 'token_bytes') else \
                        self.tok.decode([token]).encode('utf-8')
                    self.markers.feed(raw.decode('latin-1'))
                elif line.startswith('DONE '):
                    fields = line.split()
                    self.done.append({'native_generated': int(fields[1]), 'pipe_tokens': self.pass_tokens,
                                      'finish': known(fields[5], STOPS)})
                    self.pass_tokens = 0
                    self.passes += 1
                    self.tail.clear()
                elif line.startswith('ERR'):
                    self.error_lines += 1
            except Exception:
                # The observation must never break the pump or log the payload/exception.
                self.read_errors += 1

    def snapshot(self):
        with self.lock:
            return {'attached': self.attached, 'tokens': self.tokens, 'think_end_sequences': self.think_end,
                    'raw_bytes': self.markers.snapshot(), 'observation_errors': self.read_errors,
                    'engine_error_lines': self.error_lines, 'done': list(self.done),
                    'passes': self.passes, 'pending_pass_tokens': self.pass_tokens}


class PrivateHandler(RotatingFileHandler):
    def _open(self):
        fd = os.open(self.baseFilename, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        os.fchmod(fd, 0o600)
        return os.fdopen(fd, 'a', encoding='utf-8')

    def handleError(self, record):
        # logging's default error handler prints the record (and exceptions). Never do that.
        if not getattr(self, '_warned', False):
            self._warned = True
            print('[strata] diagnostic log write failed; inference continues', flush=True)


class DiagnosticSink:
    def __init__(self, path, max_bytes=4 * 1024 * 1024, backups=3):
        self.handler = PrivateHandler(path, maxBytes=max_bytes, backupCount=backups, encoding='utf-8')
        self.handler.setFormatter(logging.Formatter('%(message)s'))

    @classmethod
    def from_env(cls):
        path = os.environ.get('STRATA_DIAGNOSTIC_LOG')
        return cls(path) if path else None

    def write(self, data):
        record = logging.LogRecord('strata.boundaries', logging.INFO, '', 0,
                                   json.dumps(data, ensure_ascii=True), (), None)
        self.handler.handle(record)

    def begin(self, api, stream):
        return RequestDiagnostic(self, api, stream)


class RunDiagnostic:
    def __init__(self, thinking, prompt_tokens, max_new, tools_count, think_end_ids, tokenizer=None):
        self.pipe = PipeDiagnostic(tokenizer, think_end_ids) if tokenizer else None
        self.model = MarkerStream()
        self.injected = MarkerStream()
        self.channels = {k: MarkerStream() for k in ('reasoning', 'content', 'tool_args')}
        self.call_ids = set()
        self.duplicate_calls = 0
        self.events = Counter()
        self.event_chars = Counter()
        self.transitions = deque(maxlen=32)
        self.think_end_ids = think_end_ids
        self.token_tail = deque(maxlen=max(1, len(think_end_ids)))
        self.data = {'thinking': bool(thinking), 'prompt_tokens': prompt_tokens, 'max_new': max_new,
                     'tools_count': tools_count, 'model_tokens': 0, 'model_think_end_token_sequences': 0,
                     'stop_token_seen': False, 'state': 'reasoning' if thinking else 'content',
                     'finish': 'incomplete', 'parser_finish_called': False}

    def token(self, token, stop=False):
        self.data['model_tokens'] += 1
        self.token_tail.append(token)
        if self.think_end_ids and tuple(self.token_tail) == self.think_end_ids:
            self.data['model_think_end_token_sequences'] += 1
        if stop:
            self.data['stop_token_seen'] = True
            self.data['stop_token_id'] = token  # configured control token only, never the token stream

    def feed(self, parser, text, injected=False):
        (self.injected if injected else self.model).feed(text)
        before = parser.state
        events = parser.feed(text)
        self.parsed(parser, events, before)
        return events

    def parsed(self, parser, events, before=None):
        self.data['state'] = known(parser.state, STATES)
        if before is not None and before != parser.state:
            self.transitions.append({'from': known(before, STATES), 'to': self.data['state'],
                                     'at_model_token': self.data['model_tokens']})
        for event in events:
            kind = known(event.kind, EVENTS)
            self.events[kind] += 1
            self.event_chars[kind] += len(event.text or '')
            if kind in self.channels:
                self.channels[kind].feed(event.text or '')
            if kind == 'tool_call':
                if event.call.id in self.call_ids:
                    self.duplicate_calls += 1
                if len(self.call_ids) < 1024:
                    self.call_ids.add(event.call.id)

    def snapshot(self):
        call_at = self.model.first_at.get('<tool_call>')
        end_at = self.model.first_at.get('</think>')
        flags = []
        if self.data.get('recovery', {}).get('duplicate_recovered_calls'):
            flags.append('duplicate_recovered_call_payload')
        if self.data['thinking'] and call_at is not None and (end_at is None or call_at < end_at):
            flags.append('tool_marker_before_think_end')
        if self.data['thinking'] and self.data['state'] == 'reasoning':
            flags.append('ended_in_reasoning')
        if self.duplicate_calls:
            flags.append('duplicate_parser_call_id')
        if self.channels['content'].counts.get('</think>') or self.channels['content'].counts.get('<think>'):
            flags.append('think_marker_in_content')
        if self.data['thinking'] and not self.events['tool_call'] and not self.event_chars['content']:
            flags.append('no_answer_or_tool')
        if self.events['tool_start'] > self.events['tool_call']:
            flags.append('unfinished_tool_call')
        return {'flags': flags, **self.data, 'model_output': self.model.snapshot(), 'server_injected': self.injected.snapshot(),
                'engine_pipe': self.pipe.snapshot() if self.pipe else None,
                'parser_channels': {k: v.snapshot() for k, v in self.channels.items()},
                'duplicate_call_ids': self.duplicate_calls, 'parser_events': dict(self.events), 'parser_chars': dict(self.event_chars),
                'transitions': list(self.transitions)}


class RequestDiagnostic:
    def __init__(self, sink, api, stream):
        self.sink = sink
        self.id = uuid.uuid4().hex
        self.variant = known(os.environ.get('STRATA_DIAGNOSTIC_VARIANT'), {'sc117-iq3_s', 'orca-iq3_xxs'})
        self.api = known(api, {'openai', 'anthropic', 'responses'})
        self.stream = bool(stream)
        self.clock = time.monotonic()
        self.runs = deque(maxlen=8)
        self.run_count = 0
        self.counts = {'api_prepared': Counter(), 'http_written': Counter()}
        self.channels = {stage: {k: MarkerStream() for k in ('reasoning', 'content', 'tool_args')}
                         for stage in self.counts}
        self.sequence = {stage: deque(maxlen=64) for stage in self.counts}
        self.input_shape = {}
        self.stops = {}
        self.http_status = None
        self.disconnected = False
        self.sink.write({'phase': 'start', 'request_id': self.id, 'time': time.time(),
                         'schema_version': 3, 'variant': self.variant, 'api': self.api, 'stream': self.stream, 'payload': '[omitted]'})

    def normalized_input(self, messages):
        # Structural metadata only, never message content, names, IDs, paths or schema values.
        shape = Counter()
        for m in messages:
            role = known(m.get('role'), {'system', 'assistant', 'user', 'tool'})
            shape[role] += 1
            if role == 'assistant':
                shape['assistant_with_reasoning'] += bool(m.get('reasoning_content'))
                shape['assistant_with_calls'] += bool(m.get('tool_calls'))
                shape['calls_without_reasoning'] += bool(m.get('tool_calls')) and not bool(m.get('reasoning_content'))
        self.input_shape = dict(shape)

    def channel(self, stage, kind, text):
        if isinstance(text, str):
            self.channels[stage][kind].feed(text)

    def step(self, stage, kind):
        seq = self.sequence[stage]
        if seq and seq[-1]['event'] == kind:
            seq[-1]['count'] += 1
        else:
            seq.append({'event': kind, 'count': 1})

    def run(self, *args):
        result = RunDiagnostic(*args)
        self.run_count += 1
        self.runs.append(result)
        return result

    def observe(self, item, stage):
        """Count only known protocol fields, never arbitrary values, names, IDs or arguments."""
        if item is None:
            self.counts[stage]['heartbeat'] += 1
            return
        counts = self.counts[stage]
        if self.api == 'anthropic':
            _, event = item
            kind = known(event.get('type'), {'message_start', 'content_block_start', 'content_block_delta',
                                           'content_block_stop', 'message_delta', 'message_stop', 'error'})
            counts[kind] += 1
            self.step(stage, kind)
            if kind == 'content_block_start':
                counts['block.' + known(event.get('content_block', {}).get('type'),
                                         {'thinking', 'text', 'tool_use'})] += 1
            if kind == 'content_block_delta':
                delta = event.get('delta', {})
                sub = known(delta.get('type'), {'thinking_delta', 'text_delta', 'input_json_delta', 'signature_delta'})
                counts[sub] += 1
                for field, channel in (('thinking', 'reasoning'), ('text', 'content'), ('partial_json', 'tool_args')):
                    self.channel(stage, channel, delta.get(field, ''))
            if kind == 'message_delta':
                self.stops[stage] = known(event.get('delta', {}).get('stop_reason'), STOPS)
        else:
            counts['chunk'] += 1
            choices = item.get('choices') or []
            if choices:
                choice = choices[0]
                delta = choice.get('delta', {})
                for field in ('reasoning_content', 'content', 'tool_calls'):
                    if delta.get(field):
                        counts[field] += 1
                for field, channel in (('reasoning_content', 'reasoning'), ('content', 'content')):
                    self.channel(stage, channel, delta.get(field, ''))
                for call in delta.get('tool_calls') or []:
                    self.channel(stage, 'tool_args', call.get('function', {}).get('arguments', ''))
                    if call.get('id'):
                        counts['tool_start'] += 1
                self.step(stage, 'chunk')
                if choice.get('finish_reason') is not None:
                    self.stops[stage] = known(choice['finish_reason'], STOPS)

    def prepared(self, items):
        try:
            for item in items:
                self.observe(item, 'api_prepared')
                yield item
        finally:
            items.close()

    def json_written(self, obj):
        counts = self.counts['http_written']
        counts['json_response'] += 1
        if self.api == 'anthropic':
            for block in obj.get('content', []):
                for field, channel in (('thinking', 'reasoning'), ('text', 'content')):
                    self.channel('http_written', channel, block.get(field, ''))
                counts['block.' + known(block.get('type'), {'thinking', 'text', 'tool_use'})] += 1
            if 'stop_reason' in obj:
                self.stops['http_written'] = known(obj['stop_reason'], STOPS)
        elif obj.get('choices'):
            choice = obj['choices'][0]
            message = choice.get('message', {})
            for field, channel in (('reasoning_content', 'reasoning'), ('content', 'content')):
                self.channel('http_written', channel, message.get(field, ''))
            counts['tool_start'] += len(message.get('tool_calls') or [])
            for key in ('reasoning_content', 'content', 'tool_calls'):
                if message.get(key):
                    counts[key] += 1
            self.stops['http_written'] = known(choice.get('finish_reason'), STOPS)

    def finish(self):
        flags = []
        for channel in ('reasoning', 'content'):
            parsed = sum(r.event_chars[channel] for r in self.runs)
            prepared = self.channels['api_prepared'][channel].chars
            written = self.channels['http_written'][channel].chars
            if self.run_count == 1 and parsed != prepared:
                flags.append('parser_api_' + channel + '_count_mismatch')
            if prepared != written:
                flags.append('api_http_' + channel + '_count_mismatch')
        tool_key = 'block.tool_use' if self.api == 'anthropic' else 'tool_start'
        prepared_tools = self.counts['api_prepared'][tool_key]
        written_tools = self.counts['http_written'][tool_key]
        if prepared_tools != written_tools:
            flags.append('api_http_tool_count_mismatch')
        if (self.run_count == 1 and self.runs[0].data['finish'] == 'stop'
                and self.runs[0].events['tool_call'] != prepared_tools):
            flags.append('parser_api_tool_count_mismatch')
        if self.stream:
            for channel in ('reasoning', 'content', 'tool_args'):
                if self.channels['api_prepared'][channel].snapshot() != self.channels['http_written'][channel].snapshot():
                    flags.append('api_http_' + channel + '_structure_mismatch')
        self.sink.write({'phase': 'end', 'request_id': self.id, 'time': time.time(),
                         'schema_version': 3, 'variant': self.variant, 'api': self.api, 'stream': self.stream, 'elapsed_s': round(time.monotonic() - self.clock, 3),
                         'input_shape': self.input_shape, 'flags': flags,
                         'channels': {stage: {k: v.snapshot() for k, v in channels.items()}
                                      for stage, channels in self.channels.items()},
                         'event_sequence_tail': {k: list(v) for k, v in self.sequence.items()},
                         'http_status': self.http_status, 'disconnected': self.disconnected,
                         'run_count': self.run_count, 'runs_dropped': max(0, self.run_count - 8),
                         'runs': [run.snapshot() for run in self.runs],
                         'api_prepared': dict(self.counts['api_prepared']),
                         'http_written': dict(self.counts['http_written']), 'stop_reasons': self.stops})
