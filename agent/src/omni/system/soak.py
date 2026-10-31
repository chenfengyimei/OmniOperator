"""只读常驻采样与连续性核验；不会启动服务、调用模型或投递消息。"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import math
import os
import sqlite3
import time
from pathlib import Path

from ..config import DATA_DIR, STATE_SCHEMA_VERSION, RuntimePaths
from .atomic import atomic_write_text
from .process_identity import inspect_process, process_parents, sample_process
from .provenance import source_fingerprint
from .services import OMNI_SERVICES, ServiceManager

REQUIRED_SECONDS = 72 * 3600


def data_identity(path: Path) -> str:
    return hashlib.sha256(os.path.normcase(str(path.resolve())).encode()).hexdigest()


def valid_time(sample: dict) -> bool:
    return all(type(sample.get(k)) in (int, float) and math.isfinite(sample[k]) for k in ('wall', 'monotonic'))


def digest(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()


def validate_limits(value: dict) -> None:
    for key in ('max_private_bytes', 'max_handles', 'max_private_growth_bytes', 'max_cost_micro', 'max_log_bytes'):
        number = value.get(key)
        if type(number) not in (int, float) or not math.isfinite(number) or number <= 0:
            raise ValueError(f'{key} 必须为有限正数')


def collect(paths: RuntimePaths, expected_source: str, services=OMNI_SERVICES, *,
            inspector=inspect_process, sampler=sample_process, parents=process_parents, fingerprint=source_fingerprint,
            previous: dict | None = None) -> dict:
    sample = {'at': dt.datetime.now(dt.timezone.utc).isoformat(), 'wall': time.time(), 'monotonic': time.monotonic(),
              'services': {}, 'errors': [], 'counts': {}, 'source_matches': False, 'data_id': data_identity(paths.data)}
    try:
        sample['source_matches'] = fingerprint() == expected_source
        ancestry = parents()
        for name in services:
            path = paths.run / f'{name}.pid'
            try:
                if path.stat().st_size > 8192:
                    raise ValueError('oversized record')
                record = json.loads(path.read_text(encoding='utf-8'))
                identity = ServiceManager._record_identity(record if isinstance(record, dict) else None)
                if (not identity or record.get('service') != name or record.get('version') != 1
                        or not isinstance(record.get('launch_id'), str) or len(record['launch_id']) != 32
                        or record.get('source_confirmed') is not True
                        or inspector(identity.pid) != identity or record.get('source_fingerprint') != expected_source):
                    raise ValueError('unverified identity/version')
                found = {identity.pid}
                for _ in range(32):
                    more = {pid for pid, parent in ancestry.items() if parent in found}
                    if more <= found:
                        break
                    found |= more
                    if len(found) > 256:
                        raise ValueError('too many descendants')
                resources = [sampler(identity)]
                children = []
                for pid in sorted(found - {identity.pid}):
                    child = inspector(pid)
                    if child is None:
                        continue  # 两次只读采样间自然退出，不猜其资源数。
                    if int(child.created) < int(identity.created):
                        raise ValueError('descendant identity predates root')
                    resources.append(sampler(child))
                    children.append({'pid': pid, 'created': child.created})
                sample['services'][name] = {'verified': True, 'pid': identity.pid, 'created': identity.created,
                    'private_bytes': sum(r['private_bytes'] for r in resources),
                    'working_set': sum(r['working_set'] for r in resources), 'handles': sum(r['handles'] for r in resources),
                    'cpu_seconds': sum(r['cpu_seconds'] for r in resources), 'children': children}
            except Exception as exc:  # noqa: BLE001, S110  （有意容错：边界转换/生命周期清理不阻断）
                sample['services'][name] = {'verified': False, 'error': type(exc).__name__}
        tracked = {(resource['pid'], resource['created']) for resource in sample['services'].values() if resource.get('verified')}
        tracked |= {(child['pid'], child['created']) for resource in sample['services'].values() for child in resource.get('children', [])}
        for resource in (previous or {}).get('services', {}).values():
            if not resource.get('verified'):
                continue
            for old in [resource, *resource.get('children', [])]:
                if (old['pid'], old['created']) not in tracked:
                    remaining = inspector(old['pid'])
                    if remaining and remaining.created == old['created']:
                        sample['errors'].append('previous_service_process_still_alive')
        connection = sqlite3.connect(paths.state.resolve().as_uri() + '?mode=ro', uri=True, timeout=2)
        deadline = time.monotonic() + 5
        try:
            connection.set_progress_handler(lambda: int(time.monotonic() > deadline), 1000)
            connection.execute('BEGIN')
            sample['schema'] = int(connection.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()[0])
            sample['integrity'] = connection.execute('PRAGMA quick_check').fetchone()[0] == 'ok'
            for table in ('runs', 'run_events', 'executions', 'llm_calls'):
                sample['counts'][table] = connection.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0]
            sample['counts']['outbox_unknown'] = connection.execute("SELECT COUNT(*) FROM outbox WHERE status IN ('unknown','sending')").fetchone()[0]
            sample['counts']['publication_unknown'] = connection.execute("SELECT COUNT(*) FROM publication_drafts WHERE status IN ('unknown','sending')").fetchone()[0]
            sample['counts']['cost_micro'] = connection.execute('SELECT COALESCE(SUM(cost_micro),0) FROM llm_calls').fetchone()[0]
            sample['counts']['unpriced_calls'] = connection.execute('SELECT COUNT(*) FROM llm_calls WHERE cost_micro IS NULL').fetchone()[0]
        finally:
            connection.close()
        logs = [p for p in paths.logs.iterdir() if p.is_file() and not p.is_symlink()] if paths.logs.exists() else []
        sample['log_bytes'] = sum(p.stat().st_size for p in logs)
    except Exception as exc:  # noqa: BLE001, S110  （有意容错：边界转换/生命周期清理不阻断）
        sample['errors'].append(type(exc).__name__)
    return sample


def start(output: Path, limits: dict, *, interval_s: int = 60, services=OMNI_SERVICES, source=None, data_dir: Path = DATA_DIR) -> dict:
    validate_limits(limits)
    if not 5 <= interval_s <= 300 or not services or any(name not in OMNI_SERVICES for name in services):
        raise ValueError('采样间隔/服务集合无效')
    manifest = {'schema': 1, 'mode': 'live-readonly', 'source': source or source_fingerprint(),
                'required_seconds': REQUIRED_SECONDS, 'interval_s': interval_s, 'services': list(services),
                'limits': limits, 'data_id': data_identity(data_dir), 'created_at': dt.datetime.now(dt.timezone.utc).isoformat()}
    output.mkdir(parents=True, exist_ok=False)
    atomic_write_text(output / 'campaign.json', json.dumps(manifest, ensure_ascii=False, indent=2))
    return manifest


def read(output: Path) -> tuple[dict, list[dict]]:
    def parse(value):
        return json.loads(value, parse_constant=lambda value: (_ for _ in ()).throw(ValueError('非有限 JSON 数值')))
    manifest = parse((output / 'campaign.json').read_text(encoding='utf-8'))
    if (manifest.get('schema') != 1 or manifest.get('mode') != 'live-readonly'
            or manifest.get('required_seconds') != REQUIRED_SECONDS
            or not manifest.get('services') or any(s not in OMNI_SERVICES for s in manifest['services'])
            or len(manifest.get('data_id', '')) != 64 or len(manifest.get('source', '')) != 64
            or not 5 <= manifest.get('interval_s', 0) <= 300):
        raise ValueError('常驻清单不合法')
    path = output / 'samples.jsonl'
    if path.exists() and path.stat().st_size > 100_000_000:
        raise ValueError('采样证据超过 100 MB，请分批归档')
    samples, previous = [], digest(manifest)
    for line in path.read_text(encoding='utf-8').splitlines() if path.exists() else []:
        row = parse(line)
        checksum = row.pop('sha256')
        if row.get('previous') != previous or digest(row) != checksum:
            raise ValueError('采样链断裂或证据被修改')
        previous = checksum
        samples.append({**row, 'sha256': checksum})
    return manifest, samples


def append(output: Path, sample: dict) -> dict:
    from .filelock import FileLock
    with FileLock(output / '.sampling.lock', timeout_s=2):
        manifest, samples = read(output)
        row = {'previous': samples[-1]['sha256'] if samples else digest(manifest), 'sample': sample}
        row['sha256'] = digest(row)
        with (output / 'samples.jsonl').open('a', encoding='utf-8') as stream:
            stream.write(json.dumps(row, ensure_ascii=False, separators=(',', ':')) + '\n')
            stream.flush()
        report = summarize(manifest, [*samples, row])
        atomic_write_text(output / 'report.json', json.dumps(report, ensure_ascii=False, indent=2))
        return report


def summarize(manifest: dict, rows: list[dict]) -> dict:
    reasons, incidents = set(), []
    limits = manifest['limits']
    validate_limits(limits)
    samples = [row['sample'] for row in rows]
    span = samples[-1]['monotonic'] - samples[0]['monotonic'] if len(samples) > 1 and valid_time(samples[0]) and valid_time(samples[-1]) else 0
    if not math.isfinite(span) or manifest.get('required_seconds') != REQUIRED_SECONDS or span < REQUIRED_SECONDS:
        reasons.add('不足 72 小时')
    if not valid_calibration(limits, manifest):
        reasons.add('缺少至少 30 分钟的资源预热基线')
    baseline, peaks, last = {}, {}, None
    for index, sample in enumerate(samples):
        if not valid_time(sample):
            reasons.add('采样时间无效')
        if sample.get('data_id') != manifest['data_id']:
            reasons.add('采样数据目录变化')
        if sample.get('errors') or not sample.get('source_matches') or sample.get('schema') != STATE_SCHEMA_VERSION or not sample.get('integrity'):
            reasons.add('源码/数据库/采样检查存在失败')
        if last and valid_time(sample) and valid_time(last):
            delta = sample['monotonic'] - last['monotonic']
            if delta <= 0 or delta > manifest['interval_s'] * 2.5 or abs((sample['wall'] - last['wall']) - delta) > 30:
                reasons.add('采样不连续或时钟跳变')
            if any(sample.get('counts', {}).get(k, 0) < last.get('counts', {}).get(k, 0)
                   for k in ('runs', 'run_events', 'executions', 'llm_calls', 'cost_micro', 'unpriced_calls')):
                reasons.add('累计账本计数回退，不能确认完整使用量')
        for name in manifest['services']:
            resource = sample.get('services', {}).get(name, {})
            if not resource.get('verified'):
                incidents.append({'sample': index, 'service': name, 'state': 'unverified'})
                reasons.add('存在未核验的服务样本，需重新完成连续验收')
                continue
            key = (name, resource['pid'], resource['created'])
            baseline.setdefault(key, resource['private_bytes'])
            peaks[name] = max(peaks.get(name, 0), resource['private_bytes'])
            if resource['private_bytes'] > limits['max_private_bytes'] or resource['handles'] > limits['max_handles']:
                reasons.add('资源绝对上限超标')
            if resource['private_bytes'] - baseline[key] > limits['max_private_growth_bytes']:
                reasons.add('进程私有内存增长超标')
        if sample.get('log_bytes', 0) > limits['max_log_bytes']:
            reasons.add('日志容量超标')
        last = sample
    if samples:
        first, final = samples[0].get('counts', {}), samples[-1].get('counts', {})
        if any(final.get(name, 0) for name in ('outbox_unknown', 'publication_unknown')):
            reasons.add('仍有未处理的发布/投递结果未知')
        if final.get('unpriced_calls', 0) > first.get('unpriced_calls', 0):
            reasons.add('验收期间存在未知费用')
        if final.get('cost_micro', 0) - first.get('cost_micro', 0) > limits['max_cost_micro']:
            reasons.add('验收费用超过预设上限')
        if final.get('runs', 0) <= first.get('runs', 0) or final.get('executions', 0) <= first.get('executions', 0):
            reasons.add('缺少实际任务或定时执行记录，空闲常驻不能代替验收')
    return {'schema': 1, 'source': manifest['source'], 'manifest_sha256': digest(manifest),
            'samples': len(samples), 'elapsed_hours': span / 3600, 'required_hours': 72,
            'soak_passed': bool(samples) and not reasons, 'blockers': sorted(reasons),
            'private_bytes_peak': peaks, 'incidents': incidents[:100], 'incident_count': len(incidents),
            'last_hash': rows[-1]['sha256'] if rows else None, 'publishing_authorized': False}


def calibrate(output: Path, target: Path) -> dict:
    manifest, rows = read(output)
    samples = [r['sample'] for r in rows]
    if len(samples) < 30 or not valid_time(samples[0]) or not valid_time(samples[-1]) or samples[-1]['monotonic'] - samples[0]['monotonic'] < 1800:
        raise ValueError('预热至少 30 个样本且覆盖 30 分钟')
    if not calibration_samples_valid(manifest, samples):
        raise ValueError('预热存在源码/数据库/采样失败')
    resources = [s['services'].get(name, {}) for s in samples for name in manifest['services']]
    if not all(r.get('verified') for r in resources):
        raise ValueError('预热服务身份不完整')
    limits = dict(manifest['limits'])
    limits.update(max_private_bytes=int(max(r['private_bytes'] for r in resources)*1.5)+128*1024**2,
                  max_handles=int(max(r['handles'] for r in resources)*1.5)+128,
                  calibration_sha256=rows[-1]['sha256'], calibration_seconds=samples[-1]['monotonic']-samples[0]['monotonic'],
                  calibration_directory=str(output.resolve()))
    with target.open('x', encoding='utf-8') as stream:
        json.dump(limits, stream, ensure_ascii=False, indent=2)
    return limits


def calibration_samples_valid(manifest: dict, samples: list[dict]) -> bool:
    return (all(valid_time(s) and not s.get('errors') and s.get('source_matches') and s.get('integrity')
                and s.get('schema') == STATE_SCHEMA_VERSION and s.get('data_id') == manifest['data_id']
                and all(s.get('services', {}).get(name, {}).get('verified') for name in manifest['services']) for s in samples)
            and all(0 < b['monotonic']-a['monotonic'] <= manifest['interval_s']*2.5
                    and abs((b['wall']-a['wall'])-(b['monotonic']-a['monotonic'])) <= 30 for a,b in zip(samples,samples[1:])))


def valid_calibration(limits: dict, campaign: dict) -> bool:
    try:
        manifest, rows = read(Path(limits['calibration_directory']))
        samples = [r['sample'] for r in rows]
        return (len(samples) >= 30 and samples[-1]['monotonic']-samples[0]['monotonic'] >= 1800
                and manifest['source'] == campaign['source'] and manifest['data_id'] == campaign['data_id']
                and set(manifest['services']) >= set(campaign['services'])
                and rows[-1]['sha256'] == limits.get('calibration_sha256')
                and calibration_samples_valid(manifest, samples))
    except (OSError, ValueError, KeyError, TypeError, IndexError):
        return False
