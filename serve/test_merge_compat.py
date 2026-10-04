"""Fork-specific boundaries after adopting the upstream service and cache paths."""
import contextlib
import io
import os
from pathlib import Path
import threading
import unittest
from unittest import mock

from serve.frontend import ChatTemplate
from serve.server import ByteTokenizer, MockEngine, Service, _debug_req, engine_args

ROOT = Path(__file__).resolve().parents[1]
SECRET = 'synthetic_private_message_8273'


class MergeCompatibility(unittest.TestCase):
    def test_debug_never_prints_request_preview_or_completion(self):
        output = io.StringIO()
        tok = ByteTokenizer()
        svc = Service(MockEngine(tok, SECRET), tok, ChatTemplate(ROOT / 'serve/chat_template.jinja'))
        with mock.patch.dict(os.environ, {'STRATA_DEBUG': '1'}), contextlib.redirect_stdout(output):
            _debug_req('openai', {'max_tokens': SECRET}, [{'role': 'user', 'content': SECRET}], None, 100, False, 5)
            events = list(svc.run([65], False, [], 100, {}, threading.Event()))
        self.assertNotIn(SECRET, output.getvalue())
        self.assertIn('[omitted]', output.getvalue())
        self.assertIn(SECRET, ''.join(e.text or '' for k, e in events if k == 'event'))

    def test_separate_cache_backends_rejected_before_loading(self):
        with self.assertRaisesRegex(ValueError, 'separate cache backends'):
            engine_args({'args': ['--serve', '--lendable-cache', '--vram-elastic']})

    def test_lending_not_allowed_during_batched_decoding(self):
        with self.assertRaisesRegex(ValueError, 'serial serving'):
            engine_args({'args': ['--serve', '--lendable-cache', '--batch', '2']})

    def test_each_cache_backend_still_selectable(self):
        for flag in ('--lendable-cache', '--vram-elastic'):
            self.assertIn(flag, engine_args({'args': ['--serve', flag]}))


if __name__ == '__main__':
    unittest.main()
