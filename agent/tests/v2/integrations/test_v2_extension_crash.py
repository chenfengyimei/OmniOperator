"""Real process exits prove plugin install/grant atomicity and preserve uncertain dispatched actions without replay."""

from __future__ import annotations

import asyncio
import subprocess
import sys

import pytest
from omni_test_support.v2.extension_repository import (
    grant,
    install,
    install_request,
    metadata,
    new_turn,
    package_source,
    table_rows,
)
from omni_test_support.v2.objects import Clock, opened
from omni_test_support.v2.v2_configuration_helpers import model_uow

from omni.adapters.extensions.packages import capture_directory
from omni.adapters.persistence import schema as s
from omni.domain.contracts import BusinessError

CRASH_HELPER = 'import asyncio\nimport os\nimport sys\nimport time\nfrom pathlib import Path\nfrom omni_test_support.v2.objects import opened\nfrom omni_test_support.v2.extension_repository import authorization, approve_action, install_request, metadata, prepare_action\nfrom omni.adapters.extensions.packages import capture_directory\nfrom omni.adapters.persistence import schema as s\nfrom omni.adapters.persistence.extension_repository import ExtensionRepositories\nfrom omni.domain.contracts import ActionResult, Effect, Outcome\n\nclass Clock:\n    def now(self):\n        return sys.argv[6]\n    def monotonic(self):\n        return time.monotonic()\n\nasync def execute():\n    phase = sys.argv[3]\n    async with opened(Path(sys.argv[1]), Clock()) as (_, database, _, agent):\n        if phase in {"install", "grant"}:\n            original = ExtensionRepositories._record\n            def interrupted(unit, table, *args, **kwargs):\n                result = original(unit, table, *args, **kwargs)\n                if (phase == "install" and table is s.extension_tools) or (phase == "grant" and table is s.grants):\n                    os._exit(151 if phase == "install" else 152)\n                return result\n            ExtensionRepositories._record = interrupted\n            if phase == "install":\n                captured = capture_directory(Path(sys.argv[2]))\n                await database.write(lambda unit: unit.extension_install(\n                    agent, install_request(Path(sys.argv[2])), metadata(captured), sys.argv[4]))\n            else:\n                slot = await database.read(lambda unit: unit.extension_get(agent, sys.argv[4]))\n                await database.write(lambda unit: unit.extension_authorize(agent, slot["id"], authorization(slot)))\n        else:\n            turn = {"run_id": sys.argv[5]}\n            action, binding = await database.write(lambda unit: prepare_action(unit, agent, turn))\n            await database.write(lambda unit: approve_action(unit, agent, action))\n            await database.write(lambda unit: unit.authorize_action(agent, action["id"]))\n            proof = await database.read(lambda unit: unit.extension_dispatch(\n                agent, turn["run_id"], action["id"], binding["name"]))\n            assert proof["arguments_sha256"] == action["arguments_sha256"]\n            if phase == "intent":\n                os._exit(153)\n            if phase == "settlement":\n                original = ExtensionRepositories.audit\n                def interrupted(unit, agent_id, kind, *args, **kwargs):\n                    result = original(unit, agent_id, kind, *args, **kwargs)\n                    if kind == "action.ok":\n                        os._exit(154)\n                    return result\n                ExtensionRepositories.audit = interrupted\n            # Fake external adapter result: this suite exercises persistence,\n            # while real worker execution is covered by the host integration suite.\n            await database.write(lambda unit: unit.settle_action(agent, action["id"],\n                ActionResult(Outcome.OK, "isolated adapter receipt", Effect.APPLIED)))\n            if phase == "committed":\n                os._exit(155)\n\nasyncio.run(execute())\nraise RuntimeError("Requested process-exit hook was not reached")\n'


@pytest.mark.parametrize(
    ("phase", "code"), [("install", 151), ("grant", 152), ("intent", 153), ("settlement", 154), ("committed", 155)]
)
def test_real_exit_preserves_atomic_plugin_metadata_and_never_replays_uncertain_action(tmp_path, phase, code):
    source, clock = package_source(tmp_path / "source", count=2), Clock()
    home = tmp_path / "home"

    async def prepare():
        async with opened(home, clock) as (_, db, objects, agent):
            await db.write(lambda unit: model_uow(unit, agent))
            if phase == "install":
                captured = capture_directory(source)
                package = await objects.publish(agent, captured.content, "application/zip")
                return agent, package["id"], None, None
            installed, _, package, _ = await install(db, objects, agent, source)
            if phase == "grant":
                return agent, package["id"], installed, None
            enabled = await grant(db, agent, installed)
            turn = await db.write(lambda unit: new_turn(unit, agent))
            return agent, package["id"], enabled, turn

    agent, object_id, before, turn = asyncio.run(prepare())
    script = tmp_path / "exit-extension.py"
    script.write_text(CRASH_HELPER, encoding="utf-8")
    process = subprocess.run(
        [
            sys.executable,
            str(script),
            str(home),
            str(source),
            phase,
            object_id if phase == "install" else before["id"],
            turn["run_id"] if turn else "",
            clock.now(),
        ],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert process.returncode == code, process.stderr

    async def recover():
        async with opened(home, clock) as (_, db, _, restored_agent):
            assert restored_agent == agent
            if phase == "install":
                for table in (
                    s.extensions,
                    s.extension_revisions,
                    s.extension_tools,
                    s.extension_install_requests,
                    s.grants,
                ):
                    assert await db.read(lambda unit, table=table: table_rows(unit, table, agent)) == []
                obj = await db.read(lambda unit: unit.object_get(agent, object_id))
                assert obj["references"] == 0
                captured = capture_directory(source)
                installed = await db.write(
                    lambda unit: unit.extension_install(agent, install_request(source), metadata(captured), object_id)
                )
                assert installed["state"] == "disabled" and len(installed["tools"]) == 2
                assert len(await db.read(lambda unit: table_rows(unit, s.extension_install_requests, agent))) == 1
            elif phase == "grant":
                current = await db.read(lambda unit: unit.extension_get(agent, before["id"]))
                assert current == {
                    key: value
                    for key, value in before.items()
                    if key not in {"replayed", "cancel_operations", "retired_authority_epoch"}
                }
                assert current["state"] == "disabled" and current["authority_epoch"] == before["authority_epoch"]
                assert await db.read(lambda unit: table_rows(unit, s.grants, agent)) == []
                audits = await db.read(lambda unit: table_rows(unit, s.audit, agent))
                assert not any(row["kind"] == "extension.authorized" for row in audits)
                granted = await grant(db, agent, current)
                assert granted["enabled"] and len(await db.read(lambda unit: table_rows(unit, s.grants, agent))) == 2
                assert (
                    sum(
                        row["kind"] == "extension.authorized"
                        for row in await db.read(lambda unit: table_rows(unit, s.audit, agent))
                    )
                    == 1
                )
            else:
                actions = await db.read(lambda unit: unit.query("actions", agent))
                assert len(actions) == 1
                action = actions[0]
                assert action["state"] == ("ok" if phase == "committed" else "executing")
                bindings = await db.read(lambda unit: table_rows(unit, s.action_extension_bindings, agent))
                assert len(bindings) == 1 and bindings[0]["action_id"] == action["id"]
                audits_before = await db.read(lambda unit: table_rows(unit, s.audit, agent))
                assert sum(row["kind"] == "action.ok" for row in audits_before) == (1 if phase == "committed" else 0)
                report = await db.write(lambda unit: unit.recover())
                current = await db.read(lambda unit: unit.action(agent, action["id"]))
                assert current["state"] == ("ok" if phase == "committed" else "unknown")
                assert current["effect"] == ("applied" if phase == "committed" else "unknown")
                assert report["unknown_actions"] == (0 if phase == "committed" else 1)
                assert not await db.write(lambda unit: unit.claim_runs(4))
                with pytest.raises(BusinessError):
                    await db.write(lambda unit: unit.authorize_action(agent, action["id"]))
                with pytest.raises(BusinessError):
                    await db.read(
                        lambda unit: unit.extension_dispatch(agent, turn["run_id"], action["id"], action["tool"])
                    )
                await db.write(lambda unit: unit.recover())
                assert (await db.read(lambda unit: unit.action(agent, action["id"])))["state"] == current["state"]
                audits_after = await db.read(lambda unit: table_rows(unit, s.audit, agent))
                assert sum(row["kind"] == "action.ok" for row in audits_after) == (1 if phase == "committed" else 0)
                assert len(await db.read(lambda unit: unit.query("actions", agent))) == 1
            assert (await db.integrity())["foreign_keys"] == 0

    asyncio.run(recover())
