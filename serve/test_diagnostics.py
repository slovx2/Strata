"""Boundary diagnostics: real HTTP with a scripted engine, no model or GPU."""
import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest import mock
import urllib.request

from serve.diagnostics import DiagnosticSink, MARKERS, MarkerStream
from serve.frontend import ChatTemplate
from serve.server import ByteTokenizer, MockEngine, Service, StrataEngine, serve

ROOT = Path(__file__).resolve().parents[1]
SECRET = 'PRIVATE_API_KEY_path_command_novel_秘密'
CALL = '<tool_call>\n<function=read>\n<parameter=path>\n' + SECRET + '\n</parameter>\n</function>\n</tool_call>'


class Markers(unittest.TestCase):
    def test_every_split_and_privacy(self):
        for marker in MARKERS:
            for split in range(len(marker) + 1):
                stream = MarkerStream()
                stream.feed(SECRET + marker[:split])
                stream.feed(marker[split:] + SECRET)
                snapshot = stream.snapshot()
                self.assertEqual(snapshot['markers'], {marker: 1})
                self.assertNotIn(SECRET, json.dumps(snapshot))
                self.assertEqual(snapshot['tail'][-1]['char_end'], len(SECRET) + len(marker))

    def test_bounded_memory_and_partial_marker(self):
        stream = MarkerStream()
        for _ in range(200):
            stream.feed(SECRET * 20 + '<tool_call>')
        stream.feed('</thi')
        result = stream.snapshot()
        self.assertEqual(len(result['tail']), 64)
        self.assertEqual(result['markers']['<tool_call>'], 200)
        self.assertGreater(result['tail_entries_dropped'], 0)
        self.assertTrue(result['partial_marker_at_end'])
        self.assertLess(len(stream.pending), max(map(len, MARKERS)))

    def test_rotation_private_permissions(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'events.jsonl'
            sink = DiagnosticSink(path, max_bytes=400, backups=2)
            for i in range(30):
                sink.write({'n': i, 'payload': '[omitted]' * 10})
            sink.handler.close()
            files = list(Path(temp).iterdir())
            self.assertEqual(len(files), 3)
            for file in files:
                self.assertEqual(file.stat().st_mode & 0o777, 0o600)
                self.assertLessEqual(file.stat().st_size, 400)

    def test_write_failure_does_not_dump_record_or_raise(self):
        with tempfile.TemporaryDirectory() as temp:
            sink = DiagnosticSink(Path(temp) / 'trace.jsonl', max_bytes=1)
            with mock.patch.object(sink.handler, 'doRollover', side_effect=OSError(SECRET)), \
                    mock.patch('builtins.print') as warning:
                sink.write({'payload': '[omitted]'})
                sink.write({'payload': '[omitted]'})
                self.assertEqual(warning.call_count, 1)
                self.assertNotIn(SECRET, str(warning.call_args))
            sink.handler.close()


class HttpBoundary(unittest.TestCase):
    def check_case(self, script, thinking, api, stream, expected_tool, budget=None, native=False, after_stop=""):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'trace.jsonl'
            tok = ByteTokenizer()
            engine = MockEngine(tok, script, max_context=8192)
            if native:
                emitted = tok.encode(script, parse_special=True) + tok.encode('<|im_end|>', parse_special=True)
                emitted += tok.encode(after_stop, parse_special=True)
                exe = Path(temp) / 'engine.py'
                exe.write_text("#!/usr/bin/env python3\nimport sys\nprint('READY 8192 stop', flush=True)\n"
                               "for line in sys.stdin:\n"
                               " if line.startswith('GEN '):\n"
                               "  for t in " + repr(emitted) + ": print('T',t,flush=True)\n"
                               "  print('DONE " + str(len(emitted)) + " 1 0 0 stop 0 0 0',flush=True)\n")
                exe.chmod(0o700)
                engine = StrataEngine(str(exe), [])
            svc = Service(engine, tok,
                          ChatTemplate(ROOT / 'serve/chat_template.jinja'))
            svc.diagnostic_sink = DiagnosticSink(path)
            httpd = serve(svc, port=0)
            try:
                req = {'model': 'm', 'messages': [{'role': 'user', 'content': SECRET}],
                       'max_tokens': 400, 'stream': stream}
                if api == 'anthropic':
                    req.update(thinking={'type': 'enabled' if thinking else 'disabled'},
                               tools=[{'name': 'read', 'input_schema': {'type': 'object', 'properties':
                                      {'path': {'type': 'string'}}}}])
                    endpoint = '/v1/messages'
                else:
                    req.update(chat_template_kwargs={'enable_thinking': thinking}, tools=[{'type': 'function',
                               'function': {'name': 'read', 'parameters': {'type': 'object', 'properties':
                                            {'path': {'type': 'string'}}}}}])
                    endpoint = '/v1/chat/completions'
                if budget is not None:
                    req['max_tokens'] = budget
                url = f'http://127.0.0.1:{httpd.server_port}' + endpoint
                request = urllib.request.Request(url, json.dumps(req).encode(),
                                                  {'Content-Type': 'application/json', 'x-api-key': 'PRIVATE_HEADER_SECRET'})
                with urllib.request.urlopen(request, timeout=10) as response:
                    request_id = response.headers['X-Strata-Diagnostic-ID']
                    output = response.read()
                # HTTP completion and handler finalization can happen on separate scheduling slices.
                end = None
                for _ in range(100):
                    records = [json.loads(line) for line in path.read_text().splitlines()]
                    ends = [r for r in records if r['phase'] == 'end']
                    if ends:
                        end = ends[-1]
                        break
                    time.sleep(.01)
                self.assertIsNotNone(end)
                self.assertEqual(end['request_id'], request_id)
                self.assertNotIn(SECRET, path.read_text())
                self.assertNotIn('PRIVATE_HEADER_SECRET', path.read_text())
                self.assertNotIn('path_command', path.read_text())
                run = end['runs'][0]
                self.assertEqual(run['parser_events'].get('tool_call', 0), int(expected_tool))
                self.assertTrue(run['parser_finish_called'])
                self.assertEqual(end['http_status'], 200)
                if expected_tool:
                    key = 'block.tool_use' if api == 'anthropic' else 'tool_calls'
                    self.assertGreater(end['http_written'][key], 0)
                    self.assertEqual(end['stop_reasons']['http_written'],
                                     'tool_use' if api == 'anthropic' else 'tool_calls')
                elif thinking and '</think>' not in script:
                    self.assertEqual(run['state'], 'reasoning')
                    self.assertNotIn('tool_start', run['parser_events'])
                    self.assertEqual(run['model_think_end_token_sequences'], 0)
                    key = 'thinking_delta' if api == 'anthropic' else 'reasoning_content'
                    self.assertGreater(end['api_prepared'].get(key, 0), 0)
                return end, output
            finally:
                httpd.shutdown()
                httpd.server_close()
                svc.diagnostic_sink.handler.close()
                if native:
                    engine.close()

    def test_missing_end_and_valid_calls_all_api_modes(self):
        for api in ('anthropic', 'openai'):
            for stream in (False, True):
                with self.subTest(api=api, stream=stream):
                    bad, _ = self.check_case(CALL, True, api, stream, False)
                    self.assertEqual(bad['runs'][0]['model_output']['markers']['<tool_call>'], 1)
                    self.assertIn('tool_marker_before_think_end', bad['runs'][0]['flags'])
                    good, _ = self.check_case(SECRET + '</think>' + CALL, True, api, stream, True)
                    self.assertEqual(good['runs'][0]['model_think_end_token_sequences'], 1)
                    self.assertEqual(good['runs'][0]['model_output']['markers']['</think>'], 1)

    def test_native_pipe_before_queue_and_drain(self):
        for prefix in ('', '</think>'):
            end, _ = self.check_case(prefix + CALL, True, 'anthropic', True, bool(prefix), native=True)
            run = end['runs'][0]
            pipe = run['engine_pipe']
            self.assertTrue(pipe['attached'])
            self.assertEqual(pipe['tokens'], run['model_tokens'])
            self.assertEqual(pipe['think_end_sequences'], int(bool(prefix)))
            self.assertEqual(pipe['raw_bytes']['markers'].get('</think>', 0), int(bool(prefix)))
            self.assertEqual(pipe['done'][0]['native_generated'], pipe['tokens'])
            self.assertEqual(pipe['observation_errors'], 0)
        # A marker emitted AFTER EOS must be visible in the pipe, but never reach the parser.
        end, _ = self.check_case(CALL, True, 'anthropic', True, False, native=True, after_stop='</think>')
        run = end['runs'][0]
        self.assertEqual(run['engine_pipe']['think_end_sequences'], 1)
        self.assertEqual(run['model_think_end_token_sequences'], 0)
        self.assertGreater(run['engine_pipe']['tokens'], run['model_tokens'])


    def test_thinking_off_and_truncation(self):
        self.check_case(CALL, False, 'anthropic', True, True)
        end, _ = self.check_case('</think>' + CALL, True, 'anthropic', True, False, budget=90)
        self.assertEqual(end['runs'][0]['finish'], 'length')
        self.assertEqual(end['stop_reasons']['http_written'], 'max_tokens')
        self.assertEqual(end['runs'][0]['state'], 'call')


if __name__ == '__main__':
    unittest.main()
