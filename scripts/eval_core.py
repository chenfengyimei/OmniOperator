"""冻结场景的三轮离线契约评测；不宣称真实模型任务成功率。"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import platform
import statistics
import subprocess
import sys
import time
import xml.etree.ElementTree as ET


def evaluate(root: Path, output: Path, repeats: int = 3, *, warmup: bool = True) -> dict:
    source = root / 'tests/fixtures/core_scenarios_v1.json'
    dataset = json.loads(source.read_text(encoding='utf-8'))
    cases = dataset['cases']
    if len(cases) < 20 or len({case['id'] for case in cases}) != len(cases):
        raise ValueError('场景必须至少 20 个且 ID 唯一')
    if not 1 <= repeats <= 10:
        raise ValueError('repeats 必须为 1..10')
    output.mkdir(parents=True, exist_ok=False)
    report = {'schema': 1, 'mode': 'offline-contract', 'model': dataset['model'],
              'dataset_sha256': hashlib.sha256(source.read_bytes()).hexdigest(),
              'platform': platform.platform(), 'python': platform.python_version(),
              'at': dt.datetime.now(dt.timezone.utc).isoformat(), 'repeats': repeats,
              'actual_model_calls': 0, 'tokens': None, 'cost_usd': None,
              'usage_note': '工具和预算测试使用固定模型响应/模拟 usage；不代表供应商账单或真实任务效果',
              'rounds': [], 'complete': False}
    git = subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=root, text=True, capture_output=True)
    report['source_commit'] = git.stdout.strip() if git.returncode == 0 else None
    report['source_dirty'] = bool(subprocess.run(['git', 'status', '--porcelain', '--untracked-files=no'], cwd=root,
                                               capture_output=True, text=True).stdout) if git.returncode == 0 else None
    for iteration in range(0 if warmup else 1, repeats + 1):
        junit, log = output / f'round-{iteration}.xml', output / f'round-{iteration}.log'
        started = time.monotonic()
        with log.open('w', encoding='utf-8') as stream:
            try:
                result = subprocess.run([sys.executable, '-m', 'pytest', '-q', '--tb=short',
                                         f'--junitxml={junit}', *[case['node'] for case in cases]],
                                        cwd=root, stdout=stream, stderr=subprocess.STDOUT,
                                        env=dict(os.environ, PYTHONUTF8='1'), timeout=300)
                exit_code = result.returncode
            except subprocess.TimeoutExpired:
                exit_code = 124
        elapsed = time.monotonic() - started
        nodes = list(ET.parse(junit).iter('testcase')) if junit.exists() else []
        outcomes = []
        for case in cases:
            filename, function = case['node'].split('::')
            module = Path(filename).stem
            matches = [node for node in nodes if node.get('classname', '').split('.')[-1] == module
                       and (node.get('name') == function or node.get('name', '').startswith(function + '['))]
            failed = any(node.find('failure') is not None or node.find('error') is not None for node in matches)
            skipped = any(node.find('skipped') is not None for node in matches)
            state = 'passed' if matches and not failed and not skipped else 'failed' if failed else 'skipped' if skipped else 'missing'
            outcomes.append({**case, 'contract_result': state, 'checks': len(matches),
                             'seconds': sum(float(n.get('time', 0)) for n in matches)})
        item = {'iteration': iteration, 'warmup': iteration == 0, 'exit_code': exit_code,
                'seconds': elapsed, 'cases': outcomes}
        report['rounds'].append(item)
        (output / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        print(f'round={iteration} exit={exit_code} passed={sum(c["contract_result"]=="passed" for c in outcomes)}/{len(cases)} seconds={elapsed:.2f}', flush=True)
        if exit_code != 0 or any(c['contract_result'] != 'passed' for c in outcomes):
            return report  # 保留失败 XML/log/已完成轮次，不用部分通过替代完整评测。
    measured = [r for r in report['rounds'] if not r['warmup']]
    timings = sorted(c['seconds'] for r in measured for c in r['cases'])
    report['complete'] = len(measured) == repeats
    report['contract_pass_rate'] = sum(c['contract_result'] == 'passed' for r in measured for c in r['cases']) / (repeats * len(cases))
    report['scenario_seconds'] = {'median': statistics.median(timings), 'p95': timings[min(len(timings)-1, int(len(timings)*.95))], 'max': max(timings)}
    report['validated_expected_outcomes'] = {name: sum(c['expected_outcome'] == name for r in measured for c in r['cases'])
                                             for name in ('ok', 'error', 'unknown', 'denied', 'cancelled')}
    report['manual_approval_scenarios'] = sum(c['human_required'] for c in cases)
    report['real_model_task_success_rate'] = None
    (output / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True, help='新的结果目录，失败现场也保留')
    parser.add_argument('--repeats', type=int, default=3)
    parser.add_argument('--no-warmup', action='store_true')
    args = parser.parse_args()
    report = evaluate(Path(__file__).resolve().parents[1], args.output.resolve(), args.repeats, warmup=not args.no_warmup)
    raise SystemExit(0 if report['complete'] else 1)


if __name__ == '__main__':
    main()
