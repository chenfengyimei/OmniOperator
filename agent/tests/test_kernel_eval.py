"""Scoring and isolation contracts of the pinned, paired kernel task set."""

import hashlib
import json
import runpy
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

eval_kernel = runpy.run_path(str(Path(__file__).resolve().parents[1] / "scripts/eval_kernel.py"))
provider = runpy.run_path(str(Path(__file__).resolve().parents[1] / "scripts/eval_kernel_provider.py"))
dataset = eval_kernel["DATASET"]
candidate = "c" * 40
PROFILE = {
    "model": "pinned-profile-id",
    "provider": "isolated-model-endpoint",
    "context_tokens": 32768,
    "output_tokens": 2048,
    "max_calls": 16,
    "budget_micro_per_cell": 5000,
    "budget_micro_total": 1500000,
}


@pytest.fixture
def trials(tmp_path):
    root = tmp_path / "evaluation"
    eval_kernel["prepare"](root, dataset, PROFILE, candidate)
    _, cases = eval_kernel["dataset"](dataset)
    return root, list(eval_kernel["cells"](root, cases))


def _seed_results(cells, *, mode="provider", overhead=0):
    for case, arm, _, folder in cells:
        job = json.loads((folder / "job.json").read_text(encoding="utf-8"))
        result = {
            **job,
            "measurement": mode,
            "input_tokens": 100 + (overhead if arm == "candidate" else 0),
            "output_tokens": 5,
            "cost_micro": 17,
            "model_calls": 2,
            "elapsed_ms": 20,
            "safety": dict.fromkeys(eval_kernel["SAFETY"], 0),
            "facts": {},
        }
        for check in case["checks"]:
            if "fact" in check:
                result["facts"][check["fact"]] = check["equals"]
            else:
                eval_kernel["write_json"](folder / "workspace" / check["path"], check["json_equals"])
        eval_kernel["write_json"](folder / "result.json", result)


def test_missing_trials_and_scripted_measurements_never_qualify(trials):
    root, cells = trials
    assert len(cells) == 288 and len({folder.resolve() for *_, folder in cells}) == 288
    empty = eval_kernel["score"](root, dataset)
    assert empty["disposition"] == "insufficient_evidence" and len(empty["missing"]) == 288
    _seed_results(cells, mode="scripted")
    scripted = eval_kernel["score"](root, dataset)
    assert scripted["disposition"] == "insufficient_evidence" and not scripted["missing"]


def test_cost_limit_counts_every_trial_and_safety_blocks_quality_claim(trials):
    root, cells = trials
    _seed_results(cells, overhead=22)
    report = eval_kernel["score"](root, dataset)
    assert report["paired_95_ci"] == [0.0, 0.0] and report["disposition"] == "below_target"
    assert report["totals"]["candidate"]["total_tokens"] == 96 * 127
    path = next(folder / "result.json" for _, arm, _, folder in cells if arm == "candidate")
    row = json.loads(path.read_text(encoding="utf-8"))
    row["safety"]["unknown_replays"] = 1
    eval_kernel["write_json"](path, row)
    assert eval_kernel["score"](root, dataset)["disposition"] == "safety_failed"


def test_same_model_and_revision_required_for_each_arm(trials):
    root, cells = trials
    _seed_results(cells)
    report = eval_kernel["score"](root, dataset)
    assert report["disposition"] == "met" and report["token_ratio"] == 1.0
    path = cells[0][-1] / "result.json"
    row = json.loads(path.read_text(encoding="utf-8"))
    row["revision"] = candidate
    eval_kernel["write_json"](path, row)
    with pytest.raises(ValueError, match="binding mismatch"):
        eval_kernel["score"](root, dataset)


def test_collector_holds_unsettled_trial_and_prevents_replay(trials, tmp_path, monkeypatch):
    root, cells = trials
    drivers = tmp_path / "drivers.json"
    revisions = {**eval_kernel["REFERENCE"], "candidate": candidate}
    checkouts = {tmp_path / arm: revision for arm, revision in revisions.items()}
    eval_kernel["write_json"](
        drivers, {arm: {"checkout": str(tmp_path / arm), "argv": [sys.executable]} for arm in revisions}
    )
    dispatches = []

    def git(argv, *, cwd, text):
        assert text and argv[0] == "git"
        return checkouts[cwd] if argv[1] == "rev-parse" else ""

    def dispatch(argv, **kwargs):
        assert argv[0] == sys.executable and Path(argv[-1]).name == "job.json"
        dispatches.append(argv[-1])
        return SimpleNamespace(returncode=1)

    monkeypatch.setattr(eval_kernel["subprocess"], "check_output", git)
    monkeypatch.setattr(eval_kernel["subprocess"], "run", dispatch)
    with pytest.raises(ValueError, match="Collector stopped"):
        eval_kernel["collect"](root, dataset, drivers)
    assert (cells[0][-1] / "started.json").is_file()
    with pytest.raises(FileExistsError):
        eval_kernel["collect"](root, dataset, drivers)
    assert len(dispatches) == 1


def test_provider_receipts_require_usage_and_complete_stream():
    response_usage = provider["response_usage"]
    usage = b'{"usage":{"prompt_tokens":57,"completion_tokens":9}}'
    assert response_usage(usage, streaming=False) == (57, 9)
    event = b"data: " + usage + b"\n\n"
    assert response_usage(event + b"data: [DONE]\n\n", streaming=True) == (57, 9)
    assert response_usage(event, streaming=True) is None
    assert response_usage(b'{"usage":{"prompt_tokens":57}}', streaming=False) is None
    with pytest.raises(ValueError, match="loopback"):
        provider["provider_port"]("https://example.com/v1")


def test_meter_holds_unknown_usage_and_stops_at_the_call_limit():
    meter = provider["Meter"]("http://127.0.0.1:8765/v1", {**PROFILE, "max_calls": 1}, 2)
    row = meter.admit({"tools": [{"function": {"name": "files_read"}}]})
    assert row == {"tools": ["files_read"], "message_count": 0, "message_bytes": 2, "usage": None}
    assert meter.admit({}) is None
    assert meter.totals() == {"model_calls": 1, "input_tokens": None, "output_tokens": None, "cost_micro": None}
    row["usage"] = (12, 3)
    assert meter.totals() == {"model_calls": 1, "input_tokens": 12, "output_tokens": 3, "cost_micro": 0}


def test_local_trial_exposes_format_and_binds_check_to_copied_bytes(trials, monkeypatch):
    root, _ = trials
    scripts = Path(__file__).resolve().parents[1] / "scripts"
    monkeypatch.syspath_prepend(str(scripts))
    trial_class = runpy.run_path(str(scripts / "eval_kernel_local.py"))["Trial"]

    planning = trial_class(root / "planning-inventory/1-candidate/job.json", None, None)
    for prompt in (planning.prompt(hermes=False), planning.prompt(hermes=True)):
        assert "单行 JSON 数组" in prompt and "回读 result.json" in prompt
        assert '["alpha", "beta"]' not in prompt
    discovery = trial_class(root / "tool-discovery-find/1-candidate/job.json", None, None)
    assert "用工作区写入工具保存 result.json" in discovery.prompt(hermes=False)
    modified_job = root / "tool-discovery-find/2-candidate/job.json"
    (modified_job.parent / "workspace/input.json").write_text('"changed"\n', encoding="utf-8")
    with pytest.raises(ValueError, match="frozen input files"):
        trial_class(modified_job, None, None)
    prefilled_job = root / "tool-discovery-find/3-candidate/job.json"
    (prefilled_job.parent / "workspace/result.json").write_text("[]\n", encoding="utf-8")
    with pytest.raises(ValueError, match="frozen input files"):
        trial_class(prefilled_job, None, None)

    case = trial_class(root / "coding-validation/1-candidate/job.json", None, "Fixture")
    output = case.workspace / "submit.py"
    expected = b"def accept(value): return True\n"
    output.write_bytes(expected)

    def runner(staged):
        assert (staged / "submit.py").read_bytes() == expected
        output.write_bytes(b"def accept(value): return False\n")
        return 0

    monkeypatch.setattr(case, "_run_namespace_check", runner)
    assert case.run_check() == 0
    assert case.checks[0]["scope"] == {"submit.py": hashlib.sha256(expected).hexdigest()}
    assert not case.facts()["code_check_passed"]
