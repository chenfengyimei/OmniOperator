import argparse
import json
from pathlib import Path

from omni.system.release_gate import assess


def main():
    parser = argparse.ArgumentParser(description='检查发布材料；不发布、不代替人工批准')
    parser.add_argument('--manifest', type=Path, required=True)
    parser.add_argument('--soak', type=Path, required=True)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    result = assess(args.manifest.resolve(), args.soak.resolve())
    text = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        with args.output.open('x', encoding='utf-8') as stream: stream.write(text)
    print(text)
    raise SystemExit(0 if result['ready_for_human_review'] else 1)


if __name__ == '__main__': main()
