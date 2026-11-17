"""The V2-only candidate gate binds complete offline results to immutable source and artifact bytes."""

import hashlib
import json
import runpy
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
GATE = runpy.run_path(str(ROOT / "agent/scripts/v2_candidate_gate.py"))


def write_json(path, value):
    path.write_text(json.dumps(value), encoding="utf-8")
    return {"path": path.name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}


@pytest.fixture
def candidate(tmp_path):
    root, evidence = tmp_path / "source", tmp_path / "evidence"
    evidence.mkdir()
    for name in GATE["SOURCE_DIRS"]:
        (root / name).mkdir(parents=True)
    (root / "agent/tests/test_example.py").write_text("def test_one(): pass\n", encoding="utf-8")
    (root / "agent/src/example.py").write_text("VALUE = 1\n", encoding="utf-8")
    write_json(root / "pyrightconfig.strict.json", {"include": ["agent/src/example.py"]})
    snapshot = GATE["capture_source"](root)
    manifest = {
        "format": "omni-v2-candidate-gate-1",
        "source": write_json(evidence / "source.json", snapshot),
        "checks": {},
    }
    reports = {}
    for kind in GATE["CHECKS"]:
        log = evidence / (kind + ".log")
        log.write_text("completed\n", encoding="utf-8")
        artifacts = {"log": {"path": log.name, "sha256": hashlib.sha256(log.read_bytes()).hexdigest()}}
        report = {
            "format": "omni-v2-candidate-check-1",
            "kind": kind,
            "source_sha256": snapshot["source_sha256"],
            "command": ["python", "-m", "pytest", "-q", "-n", "4"],
            "returncode": 0,
            "artifacts": artifacts,
        }
        if kind == "full_pytest":
            report.update(
                {
                    "collection_command": ["python", "-m", "pytest", "--collect-only", "-q", "-n", "0"],
                    "collection_returncode": 0,
                }
            )
            artifacts["collected"] = write_json(evidence / "collected.json", ["agent/tests/test_example.py::test_one"])
            junit = evidence / "suite.xml"
            junit.write_text(
                '<testsuites><testsuite failures="0" errors="0" skipped="0"><testcase classname="tests.test_example" name="test_one"/></testsuite></testsuites>',
                encoding="utf-8",
            )
            artifacts["junit"] = {"path": junit.name, "sha256": hashlib.sha256(junit.read_bytes()).hexdigest()}
        elif kind == "strict_types":
            artifacts["pyright"] = write_json(
                evidence / "pyright.json", {"summary": {"filesAnalyzed": 1, "errorCount": 0, "warningCount": 0}}
            )
        reports[kind] = report
        manifest["checks"][kind] = write_json(evidence / (kind + ".json"), report)
    manifest_path = evidence / "manifest.json"
    write_json(manifest_path, manifest)

    def update(kind):
        manifest["checks"][kind] = write_json(evidence / (kind + ".json"), reports[kind])
        write_json(manifest_path, manifest)

    return root, evidence, manifest_path, manifest, reports, update


def test_complete_offline_evidence_does_not_claim_real_world_release(candidate):
    root, _, manifest, _, _, _ = candidate
    before = {str(p): p.read_bytes() for p in root.rglob("*") if p.is_file()}
    result = GATE["assess"](root, manifest)
    assert result["ready_for_windows_offline_review"] and result["blockers"] == []
    assert result["production_release_ready"] is False and result["whole_v2_complete"] is False
    assert "72h" in result["unverified_release_gates"]
    assert {str(p): p.read_bytes() for p in root.rglob("*") if p.is_file()} == before


@pytest.mark.parametrize("change", ["modify", "add", "delete"])
def test_changed_source_cannot_reuse_a_green_report(candidate, change):
    root, _, manifest, _, _, _ = candidate
    source = root / "agent/src/example.py"
    if change == "modify":
        source.write_text("VALUE = 2\n", encoding="utf-8")
    elif change == "add":
        (root / "agent/src/new.py").write_text("VALUE = 1\n", encoding="utf-8")
    else:
        source.unlink()
    result = GATE["assess"](root, manifest)
    assert result["ready_for_windows_offline_review"] is False and "candidate_source_changed" in result["blockers"]


@pytest.mark.parametrize(
    "selection", [["agent/tests/test_example.py"], ["-k", "one"], ["--lf"], ["--ignore=agent/tests"], ["-n", "8"]]
)
def test_scoped_or_last_failed_pytest_is_never_a_full_suite(candidate, selection):
    root, _, manifest, _, reports, update = candidate
    reports["full_pytest"]["command"] = ["python", "-m", "pytest", *selection]
    update("full_pytest")
    result = GATE["assess"](root, manifest)
    assert result["ready_for_windows_offline_review"] is False
    assert any(b.startswith("full_pytest:") for b in result["blockers"])


@pytest.mark.parametrize("tag", ["failure", "error", "skipped"])
def test_original_junit_failures_or_skips_cannot_be_relabelled_green(candidate, tag):
    root, evidence, manifest, _, reports, update = candidate
    junit = evidence / "suite.xml"
    junit.write_text(
        f'<testsuites><testsuite><testcase classname="tests.test_example" name="test_one"><{tag}/></testcase></testsuite></testsuites>',
        encoding="utf-8",
    )
    reports["full_pytest"]["artifacts"]["junit"] = {
        "path": junit.name,
        "sha256": hashlib.sha256(junit.read_bytes()).hexdigest(),
    }
    update("full_pytest")
    result = GATE["assess"](root, manifest)
    assert any("full_suite_has_failures_or_skips" in b for b in result["blockers"])


def test_omitted_collected_cases_do_not_pass(candidate):
    root, evidence, manifest, _, reports, update = candidate
    reports["full_pytest"]["artifacts"]["collected"] = write_json(
        evidence / "collected.json", ["agent/tests/test_example.py::test_one", "agent/tests/test_example.py::test_two"]
    )
    update("full_pytest")
    assert any("collection_mismatch" in b for b in GATE["assess"](root, manifest)["blockers"])


def test_partial_strict_scope_does_not_pass(candidate):
    root, evidence, manifest, _, reports, update = candidate
    reports["strict_types"]["artifacts"]["pyright"] = write_json(
        evidence / "pyright.json", {"summary": {"filesAnalyzed": 0, "errorCount": 0, "warningCount": 0}}
    )
    update("strict_types")
    assert any("strict_scope_incomplete" in b for b in GATE["assess"](root, manifest)["blockers"])


def test_modified_artifact_without_repinning_fails(candidate):
    root, evidence, manifest, _, _, _ = candidate
    (evidence / "package.log").write_text("changed\n", encoding="utf-8")
    assert any("artifact_hash_mismatch" in b for b in GATE["assess"](root, manifest)["blockers"])


def test_report_for_another_source_fails(candidate):
    root, _, manifest, _, reports, update = candidate
    reports["windows_lifecycle"]["source_sha256"] = "f" * 64
    update("windows_lifecycle")
    assert any(b.startswith("windows_lifecycle:") for b in GATE["assess"](root, manifest)["blockers"])


def test_missing_required_check_cannot_silently_reduce_scope(candidate):
    root, evidence, manifest_path, manifest, _, _ = candidate
    manifest["checks"].pop("full_pytest")
    write_json(evidence / manifest_path.name, manifest)
    with pytest.raises(ValueError, match="missing_or_unknown_check"):
        GATE["assess"](root, manifest_path)


def test_report_paths_cannot_escape_the_evidence_folder(candidate):
    root, evidence, manifest_path, manifest, _, _ = candidate
    manifest["source"]["path"] = "../source/pyrightconfig.strict.json"
    write_json(evidence / manifest_path.name, manifest)
    with pytest.raises(ValueError, match="unsafe_artifact"):
        GATE["assess"](root, manifest_path)
