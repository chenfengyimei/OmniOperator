"""在临时数据库评测合成样本；hybrid 模式须显式传配置，默认完全离线。"""

from __future__ import annotations

import argparse
import hashlib
import json
import tempfile
import tracemalloc
from pathlib import Path

import yaml

from ..bootstrap import build_runtime
from ..config import APP_ROOT, load_config, load_env
from ..core.evaluation import evaluate_retrieval


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=["fts", "hybrid"], default="fts")
    parser.add_argument("--config", type=Path)
    parser.add_argument("--dataset", type=Path, default=APP_ROOT / "tests/fixtures/retrieval_v1.json")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    dataset = json.loads(args.dataset.read_text(encoding="utf-8"))
    config = {"review": {"enabled": False}}
    if args.mode == "hybrid":
        if args.config is None:
            parser.error("hybrid 需要 --config 明确选择 embedding 供应商与预算")
        load_env()
        embedding = load_config(args.config).embedding
        if not embedding.enabled:
            parser.error("配置未启用 embedding")
        config["embedding"] = embedding.model_dump()
    with tempfile.TemporaryDirectory(prefix="omni-retrieval-eval-") as directory:
        root = Path(directory)
        path = root / "config.yaml"
        path.write_text(yaml.safe_dump(config), encoding="utf-8")
        runtime = build_runtime(
            config_path=path, data_dir=root, skills_root=root / "skills", start_background_workers=False
        )
        try:
            for fact in dataset["facts"]:
                runtime.store.save_fact(fact["text"], source="eval", scope=fact["scope"])
            search = runtime.store.search_facts
            if runtime.retrieval:
                if not runtime.retrieval.index.available:
                    raise RuntimeError("sqlite-vec 不可用，不能将 FTS 回退记为混合评测")
                for _ in range(len(dataset["facts"]) + 1):
                    if not runtime.retrieval.index_pending():
                        break
                search = runtime.retrieval.search
            tracemalloc.start()
            report = evaluate_retrieval(search, dataset["cases"])
            _, peak = tracemalloc.get_traced_memory()
            tracemalloc.stop()
            report.update(
                mode=args.mode,
                dataset_version=dataset["version"],
                python_peak_bytes=peak,
                valid_comparison=not bool(runtime.retrieval and runtime.retrieval.last_error),
                dataset_sha256=hashlib.sha256(args.dataset.read_bytes()).hexdigest(),
                retrieval=runtime.retrieval.status() if runtime.retrieval else None,
                usage=[record.brief() for record in runtime.usage.recent(500)],
            )
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
            print(json.dumps({k: v for k, v in report.items() if k not in {"cases", "usage"}}, ensure_ascii=False))
        finally:
            runtime.close_retrieval()
            runtime.close_reviewer()
            runtime.close_mcp()
            runtime.close_browser()
            runtime.store.close()
            runtime.close_state()


if __name__ == "__main__":
    main()
