"""Isolated candidate screening; fixed PCIe .25, synthetic independent requests.

Plan entries specify name, exe, args (flag:value), env and context. Every entry
loads a fresh engine. Repeat entries in ABBA order for confirmation. No prompt,
completion, token ID, expert ID or content hash is persisted. Production config
is read-only. Run on Song PC via direct-env.sh; output must live on /mnt/data.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'tools')]
from profile_song_pc import fixtures, proc_snapshot
from calibrate import with_arg
from serve.server import StrataEngine, engine_args, child_env
import strata_tokenizer as ST


def process_io(pid):
    """Kernel I/O counters only; no paths or request contents."""
    try:
        return {k: int(v) for k, v in (line.split(":", 1) for line in
                Path(f"/proc/{pid}/io").read_text().splitlines())}
    except (FileNotFoundError, PermissionError):
        return {}  # Some WSL kernels omit TASK_IO_ACCOUNTING entirely.


def gpu_snapshot():
    try:
        r = subprocess.run(["/usr/lib/wsl/lib/nvidia-smi",
            "--query-gpu=temperature.gpu,clocks.sm,clocks.mem,power.draw",
            "--format=csv,noheader,nounits"], capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        return {}
    try:
        return dict(zip(("temperature_c", "sm_mhz", "memory_mhz", "power_w"),
                        map(float, r.stdout.strip().split(","))))
    except ValueError:
        return {}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--config', type=Path, required=True)
    p.add_argument('--plan', type=Path, required=True)
    p.add_argument('--out', type=Path, required=True)
    p.add_argument('--repeats', type=int, default=2)
    p.add_argument('--tokens', type=int, default=192)
    p.add_argument('--warm-all', action='store_true', help='Warm every selected fixture before measured passes')
    a = p.parse_args()
    os.umask(0o077)
    assert a.out.resolve().is_relative_to('/mnt/data')
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
    fs = fixtures(tok, network_budgets=(256,1024,2048,4096,16384,32768))
    plan = json.loads(a.plan.read_text())
    data = dict(pcie_frac=.25, completed=False, arms=[], warm_all=a.warm_all,
                runner_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                plan_sha256=hashlib.sha256(a.plan.read_bytes()).hexdigest(),
                fixture_tokens={k:len(v['ids']) for k,v in fs.items()})
    references = {}  # IDs remain in memory only; persist boolean comparison, never their contents

    def save():
        tmp = a.out / 'results.tmp'
        tmp.write_text(json.dumps(data, indent=2))
        tmp.replace(a.out / 'results.json')

    for index, spec in enumerate(plan):
        assert subprocess.run(['systemctl', 'is-active', '--quiet', 'strata-sc117']).returncode != 0
        for proc in Path('/proc').iterdir():
            if not proc.name.isdigit():
                continue
            try:
                exe = (proc / 'exe').resolve()
            except OSError:
                continue
            assert exe.name != 'strata', 'A Strata engine is already running'
        c = dict(cfg, exe=spec['exe'], log=str(a.out / f'{index:02d}-{spec["name"]}.log'))
        c.update(spec.get('config', {}))
        c['args'] = list(cfg['args'])
        for k, value in dict(spec.get('args', {}), **{'--max-context': str(spec.get('context', 32768)),
                          '--prompt-cache': '0', '--pcie-frac': '0.25'}).items():
            c['args'] = with_arg(c['args'], k, str(value) if value is not None else None)
        assert not c.get('expert_profile_save') and '--expert-profile-save' not in c['args']
        env = child_env(c)
        for k in list(env):
            if k.startswith('STRATA_') and any(s in k for s in ('DEBUG', 'DUMP', 'HASH', 'LOGPOS', 'DIAGNOSTIC', 'TRACE', 'TIMELINE', 'TIMING', 'VERIFY_PROFILE')):
                env.pop(k, None)
        env.pop('STRATA_SH_STREAM', None)
        env.update(spec.get('env', {}))
        arm = dict(spec=spec, rows=[], started=time.time(),
                   binary_sha256=hashlib.sha256(Path(spec['exe']).read_bytes()).hexdigest())
        data['arms'].append(arm)
        save()
        print(json.dumps(dict(event='load', index=index, name=spec['name'])), flush=True)
        engine = None
        try:
            start = time.monotonic()
            engine = StrataEngine.__new__(StrataEngine)
            engine.__init__(c['exe'], engine_args(c), cwd=c.get('cwd'), log=c['log'], env=env)
            arm['load_s'] = time.monotonic() - start
            arm['engine'] = {k:v for k,v in engine.info.items() if k != 'tail_role_token'}
            arm['process_io_available'] = bool(process_io(engine.proc.pid))
            arm['stage'] = 'ready'
            save()
            assert engine.proc.poll() is None, 'Engine exited after READY'
            assert int(engine.info['context']) == int(spec.get('context', 32768))
            assert float(engine.info['pcie_frac']) == .25
            for k, expected in spec.get('expect_info', {}).items():
                assert engine.info.get(k) == expected, f'Engine setting mismatch: {k}'
            for required in spec.get('require_log', []):
                assert required in Path(c['log']).read_text(errors='replace'), 'Required candidate path was not activated'
            assert int(engine.info['pool_workers']) == int(spec.get('args', {}).get('--pool-workers', 9))
            arm['anon_huge_kib'] = next((int(line.split()[1]) for line in
                Path(f'/proc/{engine.proc.pid}/smaps_rollup').read_text().splitlines()
                if line.startswith('AnonHugePages:')), None)
            cases = [('network_256', 'warmup', 32), ('go_code_4096', 'warmup', 64)]
            names = spec.get('fixtures', ['network_256', 'network_4096', 'go_code_4096',
                              'zh_story_4096', 'math_4096', 'en_science_4096', 'mixed_structured_4096'])
            if a.warm_all:
                cases += [(f, 'warmup_all', 64) for f in names]
            for rep in range(a.repeats):
                cases += [(f, 'measure', a.tokens) for f in names]
            occurrences = {}
            arm_references = {}
            for fixture, tag, limit in cases:
                assert subprocess.run(['systemctl', 'is-active', '--quiet', 'strata-sc117']).returncode != 0
                arm['stage'] = 'gpu_snapshot_before'
                save()
                gpu_before = gpu_snapshot()
                arm['stage'] = 'snapshot_before'
                assert engine.proc.poll() is None, 'Engine exited during GPU snapshot'
                start = time.monotonic()
                before = proc_snapshot()
                io_before = process_io(engine.proc.pid)
                arm['stage'] = 'generate'
                ids = []
                first_token_s = None
                for token in engine.generate(fs[fixture]['ids'], limit, {'temperature': 0}, threading.Event()):
                    if token is not None:
                        if first_token_s is None:
                            first_token_s = time.monotonic() - start
                        ids.append(token)
                assert len(ids) >= 8 and tok.decode(ids).strip() and engine.last.get('reused') == 0
                occurrence = occurrences.get((fixture, tag), 0)
                occurrences[(fixture, tag)] = occurrence + 1
                key = (spec.get('context', 32768), fixture, tag, occurrence)
                reference = references.setdefault(key, ids)
                repeat_key = (fixture, tag, limit)
                within_arm_repeat_matches = (ids == arm_references[repeat_key]
                    if repeat_key in arm_references else None)
                arm_references.setdefault(repeat_key, ids)
                row = dict(occurrence=occurrence, within_arm_repeat_matches=within_arm_repeat_matches, first_token_s=first_token_s, output_matches_reference=ids == reference, fixture=fixture, tag=tag, emitted=len(ids), output_nonempty=True,
                           wall_s=time.monotonic() - start, engine=dict(engine.last),
                           before=before, after=proc_snapshot(),
                           process_io_delta={k: v - io_before[k] for k, v in process_io(engine.proc.pid).items()})
                row['gpu_before'] = gpu_before
                row['gpu_after'] = gpu_snapshot()
                arm['stage'] = 'request_complete'
                arm['rows'].append(row)
                save()
                print(json.dumps(dict(event='row', name=spec['name'], fixture=fixture,
                                      tag=tag, emitted=len(ids), pf_ms=engine.last.get('prompt_ms'),
                                      decode_ms=engine.last.get('decode_ms'))), flush=True)
            arm['completed'] = True
        except BaseException as exc:
            arm['error_type'] = type(exc).__name__
            raise
        finally:
            if engine:
                arm['engine_exit_code_before_cleanup'] = engine.proc.poll() if engine.proc else None
                save()
                engine.close()
            data['config_unchanged'] = a.config.read_bytes() == original
            save()
    data['completed'] = True
    save()


if __name__ == '__main__':
    main()
