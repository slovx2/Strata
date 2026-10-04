"""Fixed-.25, 32K, serial synthetic merge regression. Never writes production configuration.

Outputs timings and counters only. Run on Song PC with production service stopped.
Uses existing packs and both executables, with the same server/tokenizer and prompt order.
This is a short regression, not an ABBA performance proof or a 256K acceptance run.
"""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'tools')]
from profile_song_pc import fixtures
from calibrate import with_arg
from serve.server import StrataEngine, engine_args, child_env
import strata_tokenizer as ST


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--config', type=Path, required=True)
    p.add_argument('--baseline', type=Path, required=True)
    p.add_argument('--candidate', type=Path, required=True)
    p.add_argument('--out', type=Path, required=True)
    a = p.parse_args()
    os.umask(0o077)
    assert str(a.out.resolve()).startswith('/mnt/data/')
    a.out.mkdir(parents=True, exist_ok=False)
    original = a.config.read_bytes()
    cfg = json.loads(original)
    cfg.pop('api_key', None)
    tp = Path(cfg['tokenizer'])
    vocab = json.loads((tp / 'vocab.json').read_text())
    v = [None] * len(vocab)
    for t, i in vocab.items():
        v[i] = t
    tok = ST.Tokenizer(v, (tp / 'merges.txt').read_text().split('\n'),
                       json.loads((tp / 'token_type.json').read_text()))
    fs = fixtures(tok)
    data = dict(context=32768, pcie_frac=.25, completed=False, arms=[])

    def save():
        (a.out / 'results.json').write_text(json.dumps(data, indent=2))

    def idle():
        assert subprocess.run(['systemctl', 'is-active', '--quiet', 'strata-sc117']).returncode != 0
        for proc in Path('/proc').iterdir():
            if not proc.name.isdigit():
                continue
            try:
                exe = (proc / 'exe').resolve()
            except (PermissionError, FileNotFoundError, OSError):
                continue
            assert exe not in (Path(cfg['exe']).resolve(), a.baseline.resolve(), a.candidate.resolve()), 'Engine already running'

    for name, exe in [('baseline', a.baseline), ('candidate', a.candidate)]:
        idle()
        c = dict(cfg, exe=str(exe), log=str(a.out / (name + '.log')))
        c['args'] = with_arg(with_arg(with_arg(c['args'], '--max-context', '32768'),
                                     '--prompt-cache', '0'), '--pcie-frac', '0.25')
        assert not c.get('expert_profile_save') and '--expert-profile-save' not in c['args']
        env = child_env(c)
        for k in list(env):
            if k.startswith('STRATA_') and any(s in k for s in ('DEBUG', 'DUMP', 'HASH', 'LOGPOS', 'DIAGNOSTIC', 'TRACE', 'TIMELINE', 'TIMING', 'VERIFY_PROFILE')):
                env.pop(k, None)
        # Default shared-expert overlap must remain on in the upgraded engine.
        env.pop('STRATA_SH_STREAM', None)
        arm = dict(name=name, rows=[])
        data['arms'].append(arm)
        engine = None
        try:
            start = time.monotonic()
            engine = StrataEngine(str(exe), engine_args(c), cwd=c.get('cwd'), log=c['log'], env=env)
            arm['load_s'] = time.monotonic() - start
            arm['engine'] = engine.info
            arm['anon_huge_kib'] = next((int(line.split()[1]) for line in
                Path(f'/proc/{engine.proc.pid}/smaps_rollup').read_text().splitlines()
                if line.startswith('AnonHugePages:')), None)
            cases = [('network_256', 'warmup', 32), ('go_code_4096', 'warmup', 64)]
            for domain in ('network_256', 'network_4096', 'math_4096', 'zh_story_4096'):
                cases.extend([(domain, 'measure', 192)] * 2)
            for fixture, tag, limit in cases:
                start = time.monotonic()
                ids = [t for t in engine.generate(fs[fixture]['ids'], limit, {'temperature': 0}, threading.Event()) if t is not None]
                assert len(ids) >= 8 and tok.decode(ids).strip() and engine.last.get('reused') == 0
                row = dict(fixture=fixture, tag=tag, emitted=len(ids), output_nonempty=True,
                           wall_s=time.monotonic() - start, engine=dict(engine.last))
                arm['rows'].append(row)
                save()
                print(json.dumps(dict(arm=name, fixture=fixture, tag=tag, wall_s=row['wall_s'])), flush=True)
            if name == 'candidate' and str(engine.info.get('lendable', '0')) == '1':
                result = engine.command('LEND 128')
                assert result.startswith('LENT '), result
                result = engine.command('RECLAIM')
                assert result.startswith('RECLAIMED '), result
                arm['lend_reclaim'] = True
            arm['completed'] = True
        finally:
            if engine:
                engine.close()
            data['config_unchanged'] = a.config.read_bytes() == original
            save()
    data['completed'] = True
    save()


if __name__ == '__main__':
    main()
