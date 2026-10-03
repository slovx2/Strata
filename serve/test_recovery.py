"""Recovery must never invent, duplicate, partially execute or silently lose output."""
import json
import unittest
from unittest import mock
from serve.recovery import RecoveringOutputParser
from serve.frontend import anthropic_to_messages, ChatTemplate
from pathlib import Path

TOOLS = [{'name': 'read', 'parameters': {'type': 'object', 'properties': {
    'path': {'type': 'string'}, 'limit': {'type': 'integer', 'minimum': 1}},
    'required': ['path'], 'additionalProperties': False}}]
CALL = '<tool_call>\n<function=read>\n<parameter=path>\nsecret/path\n</parameter>\n</function>\n</tool_call>'


def run(text, split=None, finish='stop', tools=TOOLS, stream=True, limit=None):
    parser = RecoveringOutputParser(thinking=True, tools=tools, stream_tools=stream)
    if limit:
        parser.MAX_HOLD = limit
    chunks = [text] if split is None else [text[:split], text[split:]]
    events = []
    for chunk in chunks:
        events += parser.feed(chunk)
    events += parser.finish(finish)
    return parser, events


class Recovery(unittest.TestCase):
    def test_all_splits_and_multi_calls(self):
        text = 'I will read now.\n' + CALL + '\n' + CALL.replace('secret/path', 'second')
        for stream in (True, False):
            for split in range(len(text)+1):
                p, evs = run(text, split, stream=stream)
                calls = [e.call for e in evs if e.kind == 'tool_call']
                self.assertEqual([c.arguments for c in calls], [{'path': 'secret/path'}, {'path': 'second'}])
                self.assertEqual(len(set(c.id for c in calls)), 2)
                self.assertEqual(''.join(e.text for e in evs if e.kind == 'reasoning'), 'I will read now.\n')
                self.assertEqual(len([e for e in evs if e.kind == 'tool_start']), 2 if stream else 0)
                self.assertEqual(p.recovery['recovered_calls'], 2)
                self.assertEqual(p.finish('stop'), [])

    def test_no_early_call_or_reasoning_leak(self):
        p = RecoveringOutputParser(tools=TOOLS, stream_tools=True)
        evs = []
        for c in 'Read now.\n' + CALL:
            evs += p.feed(c)
        self.assertFalse(any(e.kind.startswith('tool') for e in evs))
        self.assertEqual(''.join(e.text for e in evs), 'Read now.\n')
        self.assertEqual(sum(e.kind == 'tool_call' for e in p.finish('stop')), 1)

    def assert_kept(self, text, decision=None, **kwargs):
        for split in (0, len(text)//2, len(text)):
            p, evs = run(text, split, **kwargs)
            self.assertFalse(any(e.kind.startswith('tool') for e in evs))
            self.assertEqual(''.join(e.text for e in evs), text)
            if decision:
                self.assertEqual(p.recovery['decision'], decision)

    def test_later_real_close_and_plain_answer(self):
        text = 'Example\n' + CALL + '\nStill thinking.</think>\nAnswer.'
        for split in range(len(text)+1):
            p, evs = run(text, split)
            self.assertEqual(''.join(e.text for e in evs if e.kind == 'reasoning'), text.split('</think>')[0])
            self.assertEqual(''.join(e.text for e in evs if e.kind == 'content'), 'Answer.')
            self.assertFalse(any(e.kind.startswith('tool') for e in evs))
        # A genuine call after a real boundary is still emitted exactly once.
        p, evs = run(CALL + '</think>' + CALL)
        self.assertEqual(sum(e.kind == 'tool_call' for e in evs), 1)
        self.assertEqual(p.recovery['recovered_calls'], 0)

    def test_refuse_examples_and_quotes(self):
        for prefix in ('```xml\n', '~~~\n', 'Example\n', '例如\n', '"', '> ', 'format:\n', '`'):
            self.assert_kept(prefix + CALL, 'example_or_quote_context')
        self.assert_kept('```' + 'x'*600 + CALL, 'example_or_quote_context')

    def test_stop_only(self):
        for finish in ('length', 'cancel', 'disconnect', 'error'):
            self.assert_kept(CALL, 'non_normal_finish', finish=finish)
        self.assert_kept(CALL, tools=[])

    def test_incomplete_and_mixed_tail(self):
        for text in (CALL[:-1], CALL.replace('</function>', ''), CALL.replace('</parameter>', ''),
                     CALL + '\nprose', CALL + CALL[:-5], CALL + '</think', CALL.replace('<parameter=path>', '<parameter=>')):
            self.assert_kept(text)

    def test_all_or_nothing_schema(self):
        for text in (CALL.replace('function=read', 'function=unknown'),
                     CALL.replace('parameter=path', 'parameter=unknown'),
                     CALL.replace('<parameter=path>\nsecret/path\n</parameter>', ''),
                     CALL.replace('</function>', '<parameter=limit>"2"</parameter></function>'),
                     CALL.replace('</function>', '<parameter=limit>0</parameter></function>'),
                     CALL.replace('</function>', '<parameter=limit>NaN</parameter></function>'),
                     CALL.replace('</function>', '<parameter=path>second</parameter></function>')):
            self.assert_kept(CALL + '\n' + text)
        p, evs = run(CALL.replace('</function>', '<parameter=limit>2</parameter></function>'))
        self.assertEqual(evs[-1].call.arguments['limit'], 2)

    def test_boolean_schema_fails_closed(self):
        call = '<tool_call><function=read></function></tool_call>'
        self.assert_kept(call, 'schema_unverifiable', tools=[{'name': 'read', 'parameters': False}])

    def test_remote_ref_fails_closed(self):
        tools = [{'name': 'read', 'parameters': {'properties': {'path': {'type': 'string'}}, '$ref': 'https://example.invalid/private'}}]
        self.assert_kept(CALL, 'schema_unverifiable', tools=tools)

    def test_bounded_hold_and_escaped_tags(self):
        self.assert_kept(CALL, 'buffer_limit', limit=50)
        self.assert_kept('No actual call. &lt;tool_call&gt;')
        self.assert_kept('Reasoning only.')
        self.assert_kept('Partial <tool_')

    def test_roundtrip_history(self):
        p, events = run('Read now.\n' + CALL)
        c = next(e.call for e in events if e.kind == 'tool_call')
        req = {'messages': [{'role': 'user', 'content': 'read'}, {'role': 'assistant', 'content': [
            {'type': 'thinking', 'thinking': 'Read now.\n'},
            {'type': 'tool_use', 'id': c.id, 'name': c.name, 'input': c.arguments}]},
            {'role': 'user', 'content': [{'type': 'tool_result', 'tool_use_id': c.id, 'content': 'test'}]}]}
        messages, _, _ = anthropic_to_messages(req)
        self.assertEqual(messages[1]['reasoning_content'], 'Read now.\n')
        self.assertEqual(len(messages[1]['tool_calls']), 1)
        rendered = ChatTemplate(Path(__file__).parent/'chat_template.jinja').render(messages, tools=TOOLS)
        self.assertEqual(rendered.count('<function=read>'), 1)


if __name__ == '__main__':
    unittest.main()
