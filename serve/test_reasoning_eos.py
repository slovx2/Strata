"""EOS recovery: protocol behavior, bounded continuation, isolation and private logs."""
import json
import tempfile
import threading
import time
import unittest
import urllib.request
from pathlib import Path
from unittest import mock

from serve.diagnostics import DiagnosticSink
from serve.frontend import ChatTemplate
from serve.server import ByteTokenizer, EngineDied, MockEngine, REASONING_WRAP_UP, Service, StrataEngine, serve
from serve.test_recovery import TOOLS, CALL

ROOT = Path(__file__).resolve().parents[1]
SECRET = 'PRIVATE_SYNTHETIC_REASONING_98'


class RecordingEngine(MockEngine):
    def __init__(self, tok, scripts):
        super().__init__(tok, scripts, max_context=16384)
        self.prompts = []
        self.limits = []
        self.closed = 0

    def generate(self, ids, max_new, sampling, cancel, embeddings=None):
        self.prompts.append(list(ids))
        self.limits.append(max_new)
        try:
            yield from super().generate(ids, max_new, sampling, cancel, embeddings)
        finally:
            self.closed += 1


class EosRecovery(unittest.TestCase):
    def setUp(self):
        self.tok = ByteTokenizer()
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / 'trace.jsonl'
        self.sink = DiagnosticSink(self.path)

    def tearDown(self):
        self.sink.handler.close()
        self.temp.cleanup()

    def service(self, scripts):
        engine = RecordingEngine(self.tok, scripts)
        svc = Service(engine, self.tok, ChatTemplate(ROOT/'serve/chat_template.jinja'))
        return svc, engine

    def run_case(self, scripts, thinking=True, max_new=4000, tools=None, engine=None, sampling=None):
        svc, default_engine = self.service(scripts)
        if engine:
            svc.engine = engine
        else:
            engine = default_engine
        diagnostic = self.sink.begin('anthropic', True)
        svc.request_trace.diagnostic = diagnostic
        events = list(svc.run([65, 66], thinking, tools or [], max_new, sampling or {}, threading.Event()))
        run = diagnostic.runs[0].snapshot()
        self.assertNotIn(SECRET, json.dumps(run))
        return events, run, engine, svc

    def test_prefix_preserved_eos_removed_and_answer_separate(self):
        events, run, eng, svc = self.run_case([SECRET, 'Final answer.'])
        self.assertEqual(eng.closed, 2)
        self.assertEqual(eng.prompts[1], [65, 66] + self.tok.encode(SECRET) + self.tok.encode(REASONING_WRAP_UP, parse_special=True))
        self.assertEqual(eng.limits[1], 2048)
        self.assertEqual(''.join(e.text for k,e in events if k=='event' and e.kind=='content'), 'Final answer.')
        self.assertNotIn(SECRET, ''.join(e.text for k,e in events if k=='event' and e.kind=='content'))
        self.assertEqual(run['reasoning_eos_recovery']['outcome'], 'answer')
        self.assertEqual(run['model_think_end_token_sequences'], 0)
        self.assertEqual(run['server_injected']['markers']['</think>'], 1)
        self.assertEqual(events[-1][1]['finish'], 'stop')
        # A later request starts with fresh state and no recovery prefix from this turn.
        list(svc.run([70], False, [], 100, {}, threading.Event()))
        self.assertEqual(eng.prompts[-1], [70])

    def test_tools_after_continuation_once(self):
        events, run, eng, _ = self.run_case([SECRET, CALL], tools=TOOLS)
        self.assertEqual(sum(k=='event' and e.kind=='tool_call' for k,e in events), 1)
        self.assertEqual(run['reasoning_eos_recovery']['outcome'], 'tool_call')
        self.assertEqual(len(eng.prompts), 2)

    def test_existing_tool_recovery_has_priority(self):
        for text in [CALL, 'Example:\n'+CALL, CALL[:-4], '&lt;tool_call&gt;', '<function=read>']:
            with self.subTest(text=text[:20]):
                events, run, eng, _ = self.run_case([text, 'must not run'], tools=TOOLS)
                self.assertEqual(len(eng.prompts), 1)
                self.assertEqual(run['reasoning_eos_recovery']['decision'], 'tool_marker_seen')
                self.assertEqual(sum(k=='event' and e.kind=='tool_call' for k,e in events), int(text==CALL))
        _, run, eng, _ = self.run_case([CALL, 'must not run'])
        self.assertEqual(len(eng.prompts), 1)  # no schema does not make the text safe to continue

    def test_partial_boundary_refused(self):
        for text in [SECRET+'<tool_', SECRET+'</thi', SECRET+'&lt;tool_']:
            _, run, eng, _ = self.run_case([text, 'must not run'], tools=TOOLS)
            self.assertEqual(run['reasoning_eos_recovery']['decision'], 'partial_boundary')
            self.assertEqual(len(eng.prompts), 1)

    def test_normal_thinking_off_and_empty_untouched(self):
        for text, thinking in [('Reason.</think>Answer.', True), ('Answer.',False), ('',True)]:
            _, _, eng, _ = self.run_case([text,'must not run'], thinking=thinking)
            self.assertEqual(len(eng.prompts),1)

    def test_budget_no_room_and_truncation(self):
        _, run, eng, _ = self.run_case([SECRET,'must not run'],max_new=len(SECRET)+2)
        self.assertEqual(run['reasoning_eos_recovery']['decision'], 'no_answer_room')
        self.assertEqual(len(eng.prompts),1)
        events, _, eng, _ = self.run_case([SECRET,'must not run'],max_new=5)
        self.assertEqual(events[-1][1]['finish'],'length')
        self.assertEqual(len(eng.prompts),1)

    def test_remaining_budget_and_continuation_cap(self):
        for budget in (160, 8000):
            events, run, eng, _ = self.run_case([SECRET,'a'*3000],max_new=budget)
            injected=len(self.tok.encode(REASONING_WRAP_UP,parse_special=True))
            self.assertEqual(eng.limits[1],min(2048,budget-len(SECRET)-1-injected))
            self.assertEqual(events[-1][1]['finish'],'length')
            self.assertLessEqual(events[-1][1]['completion_tokens'],budget)
            self.assertEqual(run['reasoning_eos_recovery']['outcome'],'length')

    def test_empty_second_pass_does_not_retry(self):
        _, run, eng, _ = self.run_case([SECRET,''])
        self.assertEqual(len(eng.prompts),2)
        self.assertEqual(run['reasoning_eos_recovery']['outcome'],'empty')

    def test_switch_off(self):
        with mock.patch.dict('os.environ',{'STRATA_REASONING_EOS_RECOVERY':'0'}):
            _, run, eng, _ = self.run_case([SECRET,'must not run'])
        self.assertEqual(len(eng.prompts),1)
        self.assertEqual(run['reasoning_eos_recovery']['decision'],'disabled')

    def test_cancel_and_disconnect(self):
        for cancel_at_eos in (True,False):
            svc, eng=self.service([SECRET,'must not run'])
            cancel=threading.Event()
            original=eng.generate
            def generate(*args, **kw):
                for t in original(*args, **kw):
                    if cancel_at_eos and t in svc.stop_ids: cancel.set()
                    yield t
            eng.generate=generate
            gen=svc.run([65],True,[],4000,{},cancel)
            if cancel_at_eos:
                events=list(gen)
                self.assertEqual(events[-1][1]['finish'],'cancel')
            else:
                next(gen)
                gen.close()
            self.assertEqual(len(eng.prompts),1)
            self.assertEqual(eng.closed,1)
            self.assertFalse(svc.status['busy'])

    def test_engine_error_does_not_continue(self):
        svc, eng=self.service([SECRET,'must not run'])
        def fail(*args,**kw):
            yield ord('x')
            raise EngineDied('synthetic engine error')
        eng.generate=fail
        with self.assertRaises(EngineDied):
            list(svc.run([65],True,[],4000,{},threading.Event()))
        self.assertFalse(svc.status['busy'])

    def test_http_native_pipe_all_api_modes(self):
        # Real StrataEngine IPC pump with a scripted subprocess, including accepted speculative
        # tokens queued AFTER EOS. Drain them before GEN; they must not enter the answer/prefix.
        for api in ('anthropic','openai'):
            for stream in (False,True):
                with self.subTest(api=api,stream=stream), tempfile.TemporaryDirectory() as temp:
                    tok=self.tok
                    first=tok.encode(SECRET)+tok.encode('<|im_end|>',parse_special=True)+tok.encode('DISCARDED_SPECULATION')
                    second=tok.encode('Final answer.')+tok.encode('<|im_end|>',parse_special=True)
                    exe=Path(temp)/'engine.py'
                    exe.write_text("#!/usr/bin/env python3\nimport sys\nn=0\nprint('READY 16384 stop',flush=True)\nfor line in sys.stdin:\n if line.startswith('GEN '):\n  tokens="+repr(first)+" if n==0 else "+repr(second)+"\n  n+=1\n  for t in tokens: print('T',t,flush=True)\n  print(f'DONE {len(tokens)} 10 5 20 stop 2 2 0',flush=True)\n")
                    exe.chmod(0o700)
                    engine=StrataEngine(str(exe),[])
                    svc=Service(engine,tok,ChatTemplate(ROOT/'serve/chat_template.jinja'))
                    path=Path(temp)/'diag.jsonl'
                    svc.diagnostic_sink=DiagnosticSink(path)
                    http=serve(svc,port=0)
                    try:
                        body={'model':'m','messages':[{'role':'user','content':'PRIVATE_REQUEST_77'}], 'max_tokens':4000,'stream':stream}
                        if api=='anthropic':body['thinking']={'type':'enabled'}
                        endpoint='/v1/messages' if api=='anthropic' else '/v1/chat/completions'
                        req=urllib.request.Request(f'http://127.0.0.1:{http.server_port}'+endpoint,json.dumps(body).encode(),{'Content-Type':'application/json'})
                        with urllib.request.urlopen(req,timeout=10) as resp: raw=resp.read().decode()
                        if stream:
                            items=[json.loads(l[6:]) for l in raw.splitlines() if l.startswith('data: ') and l[6:]!='[DONE]']
                            if api=='anthropic':
                                answer=''.join(x.get('delta',{}).get('text','') for x in items)
                                stops=[x['delta']['stop_reason'] for x in items if x['type']=='message_delta']
                                self.assertEqual(stops,['end_turn'])
                            else:
                                answer=''.join(x['choices'][0]['delta'].get('content','') or '' for x in items)
                                self.assertEqual([x['choices'][0]['finish_reason'] for x in items if x['choices'][0]['finish_reason']],['stop'])
                        else:
                            obj=json.loads(raw)
                            answer=''.join(x.get('text','') for x in obj['content']) if api=='anthropic' else obj['choices'][0]['message']['content']
                        self.assertEqual(answer,'Final answer.')
                        self.assertNotIn('DISCARDED_SPECULATION',raw)
                        for _ in range(100):
                            ends=[json.loads(l) for l in path.read_text().splitlines() if json.loads(l)['phase']=='end']
                            if ends:break
                            time.sleep(.01)
                        run=ends[-1]['runs'][0]
                        self.assertEqual(run['engine_pipe']['passes'],2)
                        self.assertEqual(svc.history[-1]['decode_ms'],40)
                        self.assertEqual(svc.history[-1]['prompt_ms'],10)
                        self.assertEqual(svc.history[-1]['drafts_accepted'],4)
                        self.assertEqual(run['reasoning_eos_recovery']['outcome'],'answer')
                        self.assertEqual(ends[-1]['flags'],[])
                        self.assertNotIn(SECRET,path.read_text())
                        self.assertNotIn('PRIVATE_REQUEST_77',path.read_text())
                    finally:
                        http.shutdown();http.server_close();engine.close();svc.diagnostic_sink.handler.close()

if __name__=='__main__':unittest.main()
