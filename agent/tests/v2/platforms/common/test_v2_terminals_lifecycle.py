"""Terminal requester cancellation, lease expiry and output pressure preserve actual ownership and receipts."""

import asyncio
import uuid

import pytest
from omni_test_support.v2.v2_terminal_helpers import FixtureTerminalBackend, configure_workspace, open_request

from omni.adapters.primitives import SystemClock
from omni.composition.instance import open_instance
from omni.domain.contracts import BusinessError


class LeaseClock(SystemClock):
    offset = 0

    def monotonic(self):
        return super().monotonic() + self.offset


async def prepared(tmp_path, *, clock=None):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    backend = FixtureTerminalBackend()
    instance = await open_instance(tmp_path / "home", capabilities=(), terminal_backend=backend, clock=clock)
    agent = (await instance.application.query("agents", ""))[0]["id"]
    await instance._database.write(lambda u: configure_workspace(u, agent, workspace))
    session = await instance.terminals.open(agent, open_request())
    connection = await instance.terminals.attach(agent, session["id"])
    return instance, backend, agent, session, connection


def test_cancelled_input_waiter_retains_real_write_and_shutdown_database_until_receipt(tmp_path):
    async def scenario():
        instance, backend, agent, session, connection = await prepared(tmp_path)
        native = backend.processes[0]
        native.write_gate = asyncio.Event()
        operation = str(uuid.uuid4())
        waiter = asyncio.create_task(
            instance.terminals.input(
                agent,
                session["id"],
                {
                    "operation_id": operation,
                    "connection_id": connection["connection_id"],
                    "data": "one effect\r",
                },
            )
        )
        await asyncio.wait_for(native.write_started.wait(), 5)
        waiter.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiter
        try:
            assert (await instance.terminals.receipt(agent, session["id"], operation))["state"] == "prepared"
            assert instance.terminals.health()["pending"] == 1
            assert not (await instance.close(timeout_s=0.05))["complete"]
            assert not instance._database.metrics["closed"]
            native.write_gate.set()
            async with asyncio.timeout(5):
                while (await instance.terminals.receipt(agent, session["id"], operation))["state"] == "prepared":
                    await asyncio.sleep(0.001)
            assert (await instance.terminals.receipt(agent, session["id"], operation))["state"] == "accepted"
            assert native.writes == [(operation, "one effect\r")]
        finally:
            native.write_gate.set()
            assert (await instance.close())["complete"]

    asyncio.run(scenario())


def test_output_pressure_is_bounded_and_expired_writer_cannot_dispatch_into_new_connection(tmp_path):
    async def scenario():
        clock = LeaseClock()
        instance, backend, agent, session, connection = await prepared(tmp_path, clock=clock)
        service, native = instance.terminals, backend.processes[0]
        try:
            for _ in range(300):
                native.queue.put_nowait("x" * 5000)
            async with asyncio.timeout(5):
                while service._live[session["id"]].sequence < 601:
                    await asyncio.sleep(0.001)
            entry = service._live[session["id"]]
            assert entry.retained <= 524288 and len(entry.chunks) <= 256
            output = await service.output(agent, session["id"], connection["connection_id"], connection["stream_id"])
            assert output["gap"] and output["earliest"] > 1 and len(output["chunks"]) <= 16
            clock.offset += 16
            old_input = {
                "operation_id": str(uuid.uuid4()),
                "connection_id": connection["connection_id"],
                "data": "must not send",
            }
            with pytest.raises(BusinessError) as error:
                await service.input(agent, session["id"], old_input)
            assert error.value.code == "terminal_connection_expired" and not native.writes
            fresh = await service.attach(agent, session["id"])
            assert fresh["connection_id"] != connection["connection_id"]
            await service.detach(agent, session["id"], connection["connection_id"])
            result = await service.input(
                agent,
                session["id"],
                {**old_input, "operation_id": str(uuid.uuid4()), "connection_id": fresh["connection_id"]},
            )
            assert result["state"] == "accepted" and len(native.writes) == 1
            await service.detach(agent, session["id"], fresh["connection_id"])
            assert not native.reclaimed
        finally:
            assert (await instance.close())["complete"]

    asyncio.run(scenario())
