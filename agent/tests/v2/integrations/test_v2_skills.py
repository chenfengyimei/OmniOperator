"""Real objects/SQLite and isolated models for reviewed text skills; no executable packages or network."""

from __future__ import annotations

from omni_test_support.v2.skills import (
    BusinessError as BusinessError,
)
from omni_test_support.v2.skills import (
    Invocation as Invocation,
)
from omni_test_support.v2.skills import (
    Model as Model,
)
from omni_test_support.v2.skills import (
    ModelReply as ModelReply,
)
from omni_test_support.v2.skills import (
    asyncio as asyncio,
)
from omni_test_support.v2.skills import (
    capture as capture,
)
from omni_test_support.v2.skills import (
    delete as delete,
)
from omni_test_support.v2.skills import (
    enable as enable,
)
from omni_test_support.v2.skills import (
    io as io,
)
from omni_test_support.v2.skills import (
    json as json,
)
from omni_test_support.v2.skills import (
    markdown as markdown,
)
from omni_test_support.v2.skills import (
    model_instance as model_instance,
)
from omni_test_support.v2.skills import (
    open_instance as open_instance,
)
from omni_test_support.v2.skills import (
    package as package,
)
from omni_test_support.v2.skills import (
    pytest as pytest,
)
from omni_test_support.v2.skills import (
    s as s,
)
from omni_test_support.v2.skills import (
    setup as setup,
)
from omni_test_support.v2.skills import (
    update as update,
)
from omni_test_support.v2.skills import (
    wait_state as wait_state,
)
from omni_test_support.v2.skills import (
    zipfile as zipfile,
)


def test_owner_import_edit_history_rollback_and_review_are_one_object_truth(tmp_path):
    async def scenario():
        instance = await open_instance(tmp_path, model=Model(), capabilities=())
        try:
            agent, original = await setup(instance)
            assert original["state"] == "disabled"
            source = await instance.objects.publish(agent, package(), "application/zip")
            imported = await instance.skills.import_object(
                agent, source["id"], "zip", original["id"], original["revision"]
            )
            assert len(imported["files"]) == 2 and imported["content_revision"] == 2
            assert (
                "Reference text"
                in (await instance.skills.get(agent, imported["id"], path="references/guide.md"))["content"]
            )
            enabled = await enable(instance, agent, imported)
            edited = await instance.skills.save(
                agent, markdown(text="New reviewed procedure."), enabled["id"], enabled["revision"]
            )
            assert edited["state"] == "disabled" and len(edited["files"]) == 2
            with pytest.raises(BusinessError) as conflict:
                await instance.skills.save(agent, markdown(), enabled["id"], enabled["revision"])
            assert conflict.value.code == "revision_conflict"
            rolled = await instance.skills.rollback(agent, edited["id"], original["revision_id"], edited["revision"])
            assert rolled["content_revision"] == 4 and rolled["state"] == "disabled" and len(rolled["files"]) == 1
            assert len((await instance.skills.history(agent, rolled["id"]))["items"]) == 4
            other = (await instance._database.write(lambda u: u.create_agent("Other")))["id"]
            with pytest.raises(BusinessError):
                await instance.skills.get(other, rolled["id"])
            with pytest.raises(BusinessError):
                await instance._database.write(
                    lambda u: u.connection.execute(
                        delete(s.skill_revisions).where(s.skill_revisions.c.id == original["revision_id"])
                    )
                )
            assert not (await instance._database.integrity())["foreign_keys"]
        finally:
            assert (await instance.close())["complete"]

    asyncio.run(scenario())


def test_selected_skill_is_in_actual_model_request_and_cannot_be_reopened(tmp_path):
    async def scenario():
        model = Model()
        instance = await open_instance(tmp_path, model=model, capabilities=())
        try:
            agent, row = await setup(instance)
            row = await enable(instance, agent, row)
            turn = await instance.application.submit_turn(agent, None, "explicit", "hello", skill_ids=(row["id"],))
            assert (await wait_state(instance.application, agent, turn["run_id"], {"succeeded", "failed"}))[
                "state"
            ] == "succeeded"
            assert "Follow the approved procedure" in str(model.requests[0]["messages"])
            report = await instance.skills.run_report(agent, turn["run_id"])
            assert report["captured"] and report["items"][0]["active"] and report["items"][0]["available"]
            with pytest.raises(BusinessError):
                await instance._database.write(
                    lambda u: u.connection.execute(delete(s.audit).where(s.audit.c.id == row["review_audit_id"]))
                )
            assert (await instance.application.submit_turn(agent, None, "explicit", "hello", skill_ids=(row["id"],)))[
                "duplicate"
            ]
            with pytest.raises(BusinessError) as conflict:
                await instance.application.submit_turn(agent, None, "explicit", "hello")
            assert conflict.value.code == "idempotency_conflict"
            with pytest.raises(BusinessError):
                await instance._database.write(
                    lambda u: u.connection.execute(
                        update(s.runs).where(s.runs.c.id == turn["run_id"]).values(skill_capture_epoch=None)
                    )
                )
        finally:
            await instance.close()

    asyncio.run(scenario())


def test_active_instruction_order_follows_activation_not_catalog_uuid_order(tmp_path):
    async def scenario():
        model = Model()
        instance = await open_instance(tmp_path, model=model, capabilities=())
        try:
            agent, first = await setup(instance)
            first = await enable(instance, agent, first)
            second = await instance.skills.save(agent, markdown("second-skill"))
            second = await enable(instance, agent, second)
            selected = sorted([first, second], key=lambda r: r["id"], reverse=True)
            turn = await instance.application.submit_turn(
                agent, None, "ordered", "hello", skill_ids=tuple(r["id"] for r in selected)
            )
            assert (await wait_state(instance.application, agent, turn["run_id"], {"succeeded", "failed"}))[
                "state"
            ] == "succeeded"
            instructions = model.requests[0]["messages"][0]["content"]
            assert instructions.index(selected[0]["name"]) < instructions.index(selected[1]["name"])
        finally:
            await instance.close()

    asyncio.run(scenario())


def test_model_progressive_disclosure_uses_captured_revision_and_still_requires_write_approval(tmp_path):
    async def scenario():
        model = Model()
        instance = await open_instance(tmp_path, model=model)
        try:
            agent, row = await setup(instance)
            row = await enable(instance, agent, row)
            model.replies = [
                ModelReply(
                    "load",
                    (Invocation("load", "skill_activate", {"skill_id": row["id"], "revision_id": row["revision_id"]}),),
                    cost_micro=7,
                ),
                ModelReply(
                    "edit", (Invocation("edit", "skill_create", {"content": markdown("new-skill")}),), cost_micro=7
                ),
            ]
            turn = await instance.application.submit_turn(agent, None, "load", "load a skill", mode="agent")
            await wait_state(instance.application, agent, turn["run_id"], {"waiting_approval"})
            assert "Reviewed procedural skills" in str(model.requests[1]["messages"])
            loaded = json.loads(
                next(m["content"] for m in model.requests[1]["messages"] if m.get("tool_call_id") == "load")
            )
            assert (
                loaded["revision"] == row["revision"]
                and loaded["content_revision"] == 1
                and loaded["files"][0]["path"] == "SKILL.md"
            )
            assert len((await instance.skills.page(agent))["items"]) == 1
            approvals = await instance.application.approvals(agent)
            assert len(approvals) == 1 and approvals[0]["tool"] == "skill_create"
            await instance.application.approve(agent, approvals[0]["id"], True, approvals[0]["policy_revision"])
            await wait_state(instance.application, agent, turn["run_id"], {"succeeded"})
            created = json.loads(
                next(m["content"] for m in model.requests[-1]["messages"] if m.get("tool_call_id") == "edit")
            )
            assert created["skill_id"] != row["id"] and created["revision"] == 1 and created["state"] == "disabled"
            assert sorted(r["state"] for r in (await instance.skills.page(agent))["items"]) == ["disabled", "enabled"]
        finally:
            await instance.close()

    asyncio.run(scenario())


def test_model_can_obtain_editing_revision_and_patch_only_after_approval(tmp_path):
    class Editing:
        stage = 0
        observed = None

        async def complete(self, messages, tools, settings):
            self.stage += 1
            if self.stage == 1:
                return ModelReply("list", (Invocation("list", "skills_list", {}),), cost_micro=7)
            if self.stage == 2:
                row = json.loads(messages[-1]["content"])[0]
                self.observed = row
                assert row["revision"] == 2 and row["content_revision"] == 1
                return ModelReply(
                    "patch",
                    (
                        Invocation(
                            "patch",
                            "skill_patch",
                            {
                                "skill_id": row["skill_id"],
                                "revision": row["revision"],
                                "section": "Pitfalls",
                                "text": "A new owner-reviewed precaution.",
                            },
                        ),
                    ),
                    cost_micro=7,
                )
            receipt = json.loads(messages[-1]["content"])
            assert receipt["revision"] == 3 and receipt["content_revision"] == 2 and receipt["state"] == "disabled"
            return ModelReply("done", cost_micro=7)

    async def scenario():
        model = Editing()
        instance = await open_instance(tmp_path, model=model)
        try:
            agent, row = await setup(instance)
            row = await enable(instance, agent, row)
            turn = await instance.application.submit_turn(agent, None, "edit-from-list", "edit skill", mode="agent")
            await wait_state(instance.application, agent, turn["run_id"], {"waiting_approval", "failed"})
            approvals = await instance.application.approvals(agent)
            assert len(approvals) == 1 and approvals[0]["tool"] == "skill_patch"
            assert (await instance.skills.get(agent, row["id"]))["revision_id"] == row["revision_id"]
            await instance.application.approve(agent, approvals[0]["id"], True, approvals[0]["policy_revision"])
            assert (await wait_state(instance.application, agent, turn["run_id"], {"succeeded", "failed"}))[
                "state"
            ] == "succeeded"
            edited = await instance.skills.get(agent, row["id"])
            assert (
                edited["state"] == "disabled"
                and edited["content_revision"] == 2
                and "owner-reviewed precaution" in edited["content"]
            )
        finally:
            await instance.close()

    asyncio.run(scenario())


def test_model_discovers_and_reads_references_from_actual_skill_tool_text(tmp_path):
    class Reading:
        stage = 0
        target = None

        async def complete(self, messages, tools, settings):
            self.stage += 1
            binding = {"skill_id": self.target["id"], "revision_id": self.target["revision_id"]}
            if self.stage == 1:
                return ModelReply("activate", (Invocation("activate", "skill_activate", binding),), cost_micro=7)
            payload = json.loads(messages[-1]["content"])
            assert payload["revision"] == self.target["revision"]
            if self.stage == 2:
                reference = next(f["path"] for f in payload["files"] if f["path"].startswith("references/"))
                return ModelReply(
                    "reference", (Invocation("reference", "skill_view", {**binding, "path": reference}),), cost_micro=7
                )
            assert "Reference text" in payload["content"] and payload["path"] == "references/guide.md"
            return ModelReply("done", cost_micro=7)

    async def scenario():
        model = Reading()
        instance = await open_instance(tmp_path, model=model)
        try:
            agent, row = await setup(instance)
            source = await instance.objects.publish(agent, package(), "application/zip")
            row = await instance.skills.import_object(agent, source["id"], "zip", row["id"], row["revision"])
            model.target = await enable(instance, agent, row)
            turn = await instance.application.submit_turn(
                agent, None, "references", "read references", mode="retrieval"
            )
            assert (await wait_state(instance.application, agent, turn["run_id"], {"succeeded", "failed"}))[
                "state"
            ] == "succeeded"
            assert model.stage == 3
        finally:
            await instance.close()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "path",
    [
        "../SKILL.md",
        "references/../bad.md",
        "references/a.ps1",
        "references/CON.md",
        "references/a:stream.md",
        "references\\bad.md",
        "/SKILL.md",
    ],
)
def test_import_rejects_cross_platform_paths_and_executable_files(path):
    payload = package(references={path: "not accepted"})
    if "\\" in path:
        # ZipInfo normalizes the writer's host separator; craft the original
        # central/local name bytes so the reader must validate the real input.
        payload = package(references={path.replace("\\", "/"): "not accepted"}).replace(
            path.replace("\\", "/").encode(), path.encode()
        )
    with pytest.raises(BusinessError):
        capture(payload, "zip")


def test_capture_rejects_case_aliases_and_missing_sections():
    with pytest.raises(BusinessError):
        capture(package(references={"references/one.md": "one", "references/ONE.md": "two"}), "zip")
    with pytest.raises(BusinessError):
        capture(markdown().replace("## Verification", "## Missing").encode(), "markdown")


@pytest.mark.parametrize("case", ["symlink", "oversize_reference", "too_many", "original_nul"])
def test_zip_metadata_and_original_names_are_rejected_before_disclosure(case):
    import stat

    if case == "symlink":
        output = io.BytesIO()
        with zipfile.ZipFile(output, "w") as archive:
            archive.writestr("SKILL.md", markdown())
            entry = zipfile.ZipInfo("references/link.md")
            entry.external_attr = (stat.S_IFLNK | 0o777) << 16
            archive.writestr(entry, "../private")
        payload = output.getvalue()
    elif case == "oversize_reference":
        payload = package(references={"references/large.md": "x" * 65537})
    elif case == "too_many":
        payload = package(references={f"references/{i}.md": "text" for i in range(17)})
    else:
        payload = package(references={"references/bad.md": "text"}).replace(b"references/bad.md", b"references/\0ad.md")
    with pytest.raises(BusinessError):
        capture(payload, "zip")


def test_revoke_active_skill_cancels_waiting_approval_and_reenable_never_restores_old_run(tmp_path):
    from omni_test_support.v2.routing import Tool

    from omni.application.engine import Capability
    from omni.domain.contracts import ToolSpec

    async def scenario():
        tool, model = Tool(), Model([ModelReply("write", (Invocation("write", "files_write", {}),), cost_micro=7)])
        instance = await open_instance(
            tmp_path, model=model, capabilities=(Capability(ToolSpec("files_write", "write", {}, True), tool),)
        )
        try:
            agent, row = await setup(instance)
            row = await enable(instance, agent, row)
            turn = await instance.application.submit_turn(agent, None, "pending", "use skill", skill_ids=(row["id"],))
            await wait_state(instance.application, agent, turn["run_id"], {"waiting_approval"})
            row = await instance.skills.state(
                agent, row["id"], row["revision"], "disabled", row["revision_id"], row["manifest_sha256"]
            )
            await wait_state(instance.application, agent, turn["run_id"], {"failed", "interrupted"})
            assert not tool.calls and not await instance.application.approvals(agent)
            await enable(instance, agent, row)
            with pytest.raises(BusinessError) as denied:
                await instance._database.read(lambda u: u.skill_authorize_run(agent, turn["run_id"]))
            assert denied.value.code == "skill_authority_revoked"
            report = await instance.skills.run_report(agent, turn["run_id"])
            assert report["items"][0]["active"] and not report["items"][0]["available"]
        finally:
            await instance.close()

    asyncio.run(scenario())


def test_revocation_during_model_call_keeps_actual_cost_without_publishing_output(tmp_path):
    class Waiting(Model):
        def __init__(self):
            super().__init__()
            self.entered, self.release = asyncio.Event(), asyncio.Event()

        async def complete(self, *args):
            reply = await super().complete(*args)
            self.entered.set()
            await self.release.wait()
            return reply

    async def scenario():
        model = Waiting()
        instance = await open_instance(tmp_path, model=model, capabilities=())
        try:
            agent, row = await setup(instance)
            row = await enable(instance, agent, row)
            turn = await instance.application.submit_turn(agent, None, "inflight", "use skill", skill_ids=(row["id"],))
            await asyncio.wait_for(model.entered.wait(), 5)
            await instance.skills.state(
                agent, row["id"], row["revision"], "disabled", row["revision_id"], row["manifest_sha256"]
            )
            model.release.set()
            await wait_state(instance.application, agent, turn["run_id"], {"failed"})
            assert len(await instance.application.messages(agent, turn["session_id"])) == 1
            calls = await instance.application.query("calls", agent)
            assert len(calls) == 1 and calls[0]["cost_micro"] == 7 and calls[0]["state"] == "settled"
        finally:
            model.release.set()
            await instance.close()

    asyncio.run(scenario())


def test_skill_revoked_during_stream_preparation_prevents_send_and_reclaims_unused_transport(tmp_path):
    from omni_test_support.v2.model_stream import StreamingModel

    class Preparing(StreamingModel):
        def __init__(self):
            super().__init__()
            self.entered, self.release = asyncio.Event(), asyncio.Event()

        async def prepare_stream(self, *args):
            prepared = await super().prepare_stream(*args)
            self.entered.set()
            try:
                await self.release.wait()
            except BaseException:
                await prepared.close()
                raise
            return prepared

    async def scenario():
        model = Preparing()
        instance = await open_instance(tmp_path, model=model, model_streamer=model, capabilities=())
        try:
            agent, row = await setup(instance)
            row = await enable(instance, agent, row)
            turn = await instance.application.submit_turn(agent, None, "preparing", "hello", skill_ids=(row["id"],))
            await asyncio.wait_for(model.entered.wait(), 5)
            await instance.skills.state(
                agent, row["id"], row["revision"], "disabled", row["revision_id"], row["manifest_sha256"]
            )
            model.release.set()
            await wait_state(instance.application, agent, turn["run_id"], {"failed"})
            calls = await instance.application.query("calls", agent)
            assert len(calls) == 1 and calls[0]["state"] == "not_sent" and calls[0]["cost_micro"] == 0
            assert model.send_count == 0 and not model.prepared
        finally:
            model.release.set()
            assert (await instance.close())["complete"]

    asyncio.run(scenario())


def test_platform_and_empty_capture_cannot_gain_later_authority(tmp_path):
    async def scenario():
        instance = await open_instance(tmp_path, model=Model(), capabilities=())
        try:
            agent = (await instance.application.query("agents", ""))[0]["id"]
            await model_instance(instance, agent, model="fixture")
            empty = await instance.application.submit_turn(agent, None, "empty", "hello")
            await wait_state(instance.application, agent, empty["run_id"], {"succeeded"})
            unsupported = "macos" if instance.skills.platform != "macos" else "windows"
            row = await instance.skills.save(agent, markdown(platforms=f"[{unsupported}]"))
            row = await enable(instance, agent, row)
            assert not (await instance.skills.available(agent))["items"]
            with pytest.raises(BusinessError):
                await instance.application.submit_turn(agent, None, "bad-platform", "hello", skill_ids=(row["id"],))
            assert not (await instance.skills.run_report(agent, empty["run_id"]))["items"]
            with pytest.raises(BusinessError):
                await instance._database.write(lambda u: u.skill_capture_run(agent, empty["run_id"]))
        finally:
            await instance.close()

    asyncio.run(scenario())


def test_import_export_preserves_all_text_references_and_rejects_corrupted_objects(tmp_path):
    from omni.domain.objects import Blob

    async def scenario():
        instance = await open_instance(tmp_path, model=Model(), capabilities=())
        try:
            agent = (await instance.application.query("agents", ""))[0]["id"]
            source = await instance.objects.publish(agent, package(), "application/zip")
            row = await instance.skills.import_object(agent, source["id"], "zip")
            exported = await instance.skills.export(agent, row["id"])
            _, content = await instance.objects.get(agent, exported["object_id"])
            assert capture(content, "zip").files == capture(package(), "zip").files
            stored = await instance._database.read(lambda u: u.skill_get(agent, row["id"]))
            target = stored["files"][0]
            path = instance.paths.objects / Blob(agent, target["sha256"], target["bytes"]).key
            path.write_bytes(b"corrupted fixture")
            with pytest.raises(BusinessError):
                await enable(instance, agent, row)
            assert (await instance._database.read(lambda u: u.skill_get(agent, row["id"])))["state"] == "disabled"
        finally:
            await instance.close()

    asyncio.run(scenario())


def test_cancelled_waiter_keeps_actual_parse_and_install_owned_until_shutdown(tmp_path, monkeypatch):
    from omni.adapters.skill_packages import SkillPackages

    entered, release = asyncio.Event(), asyncio.Event()
    original = SkillPackages.parse

    async def held(self, *args):
        entered.set()
        await release.wait()
        return await original(self, *args)

    monkeypatch.setattr(SkillPackages, "parse", held)

    async def scenario():
        instance = await open_instance(tmp_path, model=Model(), capabilities=())
        try:
            agent = (await instance.application.query("agents", ""))[0]["id"]
            task = asyncio.create_task(instance.skills.save(agent, markdown()))
            await asyncio.wait_for(entered.wait(), 5)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert instance.skills.health()["pending"] == 1
            report = await instance.close(0.01)
            assert not report["complete"] and not instance._database.metrics["closed"]
            release.set()
            closed = await instance.close(10)
            assert closed["complete"], closed
        finally:
            release.set()
            await instance.close()
        reopened = await open_instance(tmp_path, model=Model(), capabilities=())
        try:
            rows = (await reopened.skills.page(agent))["items"]
            assert len(rows) == 1 and rows[0]["state"] == "disabled"
        finally:
            await reopened.close()

    asyncio.run(scenario())


def test_restore_retains_skill_content_but_requires_new_owner_review(tmp_path):
    from omni.adapters.persistence.maintenance import backup, restore

    async def scenario():
        instance = await open_instance(tmp_path / "source", model=Model(), capabilities=())
        try:
            agent, row = await setup(instance)
            row = await enable(instance, agent, row)
        finally:
            await instance.close()
        backup(instance.paths, tmp_path / "snapshot")
        restore(tmp_path / "snapshot", tmp_path / "restored")
        restored = await open_instance(tmp_path / "restored", model=Model(), capabilities=())
        try:
            value = await restored.skills.get(agent, row["id"])
            assert (
                value["state"] == "disabled"
                and value["revision_id"] == row["revision_id"]
                and "Follow the approved" in value["content"]
            )
            assert not (await restored.skills.available(agent))["items"]
        finally:
            await restored.close()

    asyncio.run(scenario())
