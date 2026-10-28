"""只读采样：独立运行，不启停服务、不调用模型、不发送消息。"""
import argparse
import json
from pathlib import Path
import time

from omni.config import DATA_DIR, RuntimePaths
from omni.system.soak import start, collect, append, read, summarize, calibrate, data_identity


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('run', 'report', 'calibrate'))
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--data-dir', type=Path, default=DATA_DIR)
    parser.add_argument('--limits', type=Path, help='run: 资源和费用上限；calibrate: 新基线文件输出路径')
    parser.add_argument('--hours', type=float, default=72, help='本次采样时长；短跑不会通过 72 小时门槛')
    parser.add_argument('--interval', type=int, default=60)
    parser.add_argument('--resume', action='store_true')
    args = parser.parse_args()
    if args.command == 'calibrate':
        if args.limits is None: parser.error('需要 --limits 指定新的基线文件')
        calibrate(args.output, args.limits)
        return
    if args.command == 'report':
        manifest, rows = read(args.output)
        print(json.dumps(summarize(manifest, rows), ensure_ascii=False, indent=2))
        return
    if not 0 < args.hours <= 168: parser.error('hours 必须为 0..168')
    if args.resume:
        manifest, _rows = read(args.output)
        if manifest['data_id'] != data_identity(args.data_dir):
            parser.error('续采数据目录必须与初始目录一致')
    else:
        if args.limits is None: parser.error('需要 --limits；不会猜测可接受的费用和资源上限')
        manifest = start(args.output, json.loads(args.limits.read_text(encoding='utf-8')), interval_s=args.interval, data_dir=args.data_dir)
        _rows = []
    deadline = time.monotonic() + args.hours * 3600
    paths = RuntimePaths.create(args.data_dir)
    previous = _rows[-1]['sample'] if _rows else None
    try:
        while True:
            observed = collect(paths, manifest['source'], manifest['services'], previous=previous)
            report = append(args.output, observed)
            previous = observed
            print(json.dumps({'samples': report['samples'], 'hours': round(report['elapsed_hours'], 3),
                              'passed': report['soak_passed'], 'blockers': report['blockers']}, ensure_ascii=False), flush=True)
            if time.monotonic() >= deadline: break
            time.sleep(min(manifest['interval_s'], max(0, deadline-time.monotonic())))
    except KeyboardInterrupt:
        print('采样已停止，现有证据保留；没有停止被观察的服务。')


if __name__ == '__main__':
    main()
