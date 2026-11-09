"""Owner Android device grants, ephemeral observations and shared approved actions; no platform I/O."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable, Mapping
from typing import Protocol

from ..domain.android import (
    ACCESS_SPEC,
    MODEL_SPEC,
    OBSERVE_SPEC,
    POLICY,
    RESTORE_SPEC,
    SPEC,
    access_request,
    action_request,
    device,
    digest,
    identity,
    invalid,
)
from ..domain.contracts import JSON, ActionResult, BusinessError, Effect, InvocationContext, Outcome
from ..domain.desktop import CapturedFrame
from .objects import ObjectService
from .ports import Clock, DatabasePort, Ids, Record


class AndroidPort(Protocol):
    async def execute(
        self,
        name: str,
        args: dict,
        context: InvocationContext,
        *,
        before_dispatch: Callable[[], Awaitable[None]],
        action: dict | None = None,
        environment: Callable[[str, dict], Awaitable[None]] | None = None,
    ) -> ActionResult | CapturedFrame: ...
    def health(self) -> dict: ...
    def quiescent(self) -> bool: ...
    def stop_accepting(self) -> None: ...
    async def close(self, timeout_s: float) -> bool: ...


class AndroidService:
    def __init__(
        self,
        db: DatabasePort,
        backend: AndroidPort,
        objects: ObjectService,
        clock: Clock,
        ids: Ids,
        *,
        accepting: Callable[[], bool],
    ):
        self._db, self._backend, self._objects, self._clock, self._ids = db, backend, objects, clock, ids
        self._accepting = accepting
        self._probes: dict[str, dict] = {}
        self._observations: dict[str, dict] = {}
        self._bindings: dict[str, dict] = {}
        self._pending: set[asyncio.Task] = set()
        self._leases: list[Record] = []
        self._closing = False
        self._admission = asyncio.Lock()

    async def _owned(self, coroutine):
        if self._closing or len(self._pending) >= 8:
            coroutine.close()
            raise BusinessError("android_busy", "Android admission is closed or full", status=503)
        task = asyncio.create_task(coroutine, name="android-use-case")
        self._pending.add(task)

        def done(future):
            self._pending.discard(future)
            if not future.cancelled():
                future.exception()

        task.add_done_callback(done)
        return await asyncio.shield(task)

    def _sweep(self):
        for values in (self._probes, self._observations, self._bindings):
            for key in list(values):
                if values[key]["expires"] <= self._clock.monotonic():
                    values.pop(key)
        if len(self._probes) + len(self._observations) + len(self._bindings) >= 64:
            raise BusinessError("android_view_limit", "Close expired Android views before continuing", status=503)

    def _context(self, agent_id: str) -> InvocationContext:
        return InvocationContext(agent_id, self._ids.new(), self._ids.new(), 1)

    async def probe(self, agent_id: str, config_id: str, consent: bool) -> dict:
        if consent is not True:
            raise invalid()
        return await self._owned(self._probe(agent_id, config_id))

    async def _probe(self, agent_id, config_id):
        self._sweep()
        config = await self._db.read(lambda u: u.android_connection(agent_id, config_id))

        async def guard():
            if self._closing or await self._db.read(lambda u: u.android_connection(agent_id, config_id)) != config:
                raise BusinessError("android_configuration_changed", "Android probe authority changed")

        result = await self._backend.execute(
            "android_probe", {"connection": config}, self._context(agent_id), before_dispatch=guard
        )
        if not isinstance(result, ActionResult) or result.status != Outcome.OK:
            raise BusinessError(
                "android_device_unavailable",
                "Device is unavailable; inspect ADB connection and authorization",
                status=503,
            )
        await guard()
        target = device(result.data.get("device"))
        key = self._ids.new()
        self._probes[key] = {
            "agent_id": agent_id,
            "config": config,
            "device": target,
            "expires": self._clock.monotonic() + 60,
        }
        return {"probe_id": key, "device": target, "expires_in_s": 60}

    async def grant(self, agent_id: str, probe_id: str, consent: bool) -> Record:
        if consent is not True:
            raise invalid()
        return await self._owned(self._grant(agent_id, identity(probe_id)))

    async def _grant(self, agent_id, probe_id):
        probe = self._probes.get(probe_id)
        if not probe or probe["agent_id"] != agent_id or probe["expires"] <= self._clock.monotonic():
            raise BusinessError("android_probe_expired", "Probe expired or belongs to another Agent")
        row = await self._db.write(
            lambda u: u.android_grant(
                agent_id, probe["config"]["config_id"], probe["config"]["revision_id"], probe["device"]
            )
        )
        self._probes.pop(probe_id, None)
        return row

    async def page(self, agent_id: str, after: str = "", limit: int = 50):
        rows = await self._db.read(lambda u: u.android_grants(agent_id, after, limit))
        return {
            "items": rows,
            "next_after": rows[-1]["id"] if len(rows) == limit else None,
            "capability": self._backend.health(),
        }

    async def revoke(self, agent_id: str, grant_id: str, revision: int):
        row = await self._db.write(lambda u: u.android_revoke(agent_id, grant_id, revision))
        for key in list(self._observations):
            if self._observations[key]["agent_id"] == agent_id and self._observations[key]["grant_id"] == grant_id:
                self._observations.pop(key)
        return row

    async def _authority(self, agent_id: str, grant_id: str) -> dict[str, dict]:
        if self._closing:
            raise BusinessError("android_closing", "Android authority was revoked")
        value = await self._db.read(lambda u: u.android_authorize(agent_id, grant_id))
        grant, connection = value.get("grant"), value.get("connection")
        if not isinstance(grant, dict) or not isinstance(connection, dict):
            raise invalid("android_authority_invalid")
        return {"grant": grant, "connection": connection}

    async def observe(self, agent_id: str, grant_id: str):
        return await self._owned(self._observe(agent_id, identity(grant_id)))

    async def unicode_profile(self, agent_id: str, grant_id: str):
        return await self._db.read(lambda u: u.android_unicode_profile(agent_id, identity(grant_id)))

    async def save_unicode_profile(self, agent_id: str, grant_id: str, value: dict, revision: int):
        return await self._owned(
            self._db.write(lambda u: u.android_unicode_save(agent_id, identity(grant_id), value, revision))
        )

    async def environments(self, agent_id: str, after: str = "", limit: int = 50):
        rows = await self._db.read(lambda u: u.android_unicode_environments(agent_id, after, limit))
        return {"items": rows, "next_after": rows[-1]["id"] if len(rows) == limit else None}

    async def restore_ime(self, agent_id: str, environment_id: str, value: dict):
        if (
            not isinstance(value, dict)
            or set(value) != {"consent", "session_id", "request_id", "revision"}
            or value["consent"] is not True
        ):
            raise invalid()
        if not self._accepting():
            raise BusinessError("not_ready", "Application is not accepting work", status=503)
        request = {**value, "environment_id": identity(environment_id)}
        identity(value["session_id"])
        identity(value["request_id"])
        fingerprint = digest(request)
        async with self._admission:
            prior = await self._db.read(lambda u: u.owner_command_replay(agent_id, value["request_id"], fingerprint))
            if prior:
                return prior
            row = await self._db.read(lambda u: u.android_unicode_environment(agent_id, environment_id))
            if row["state"] != "pending" or type(value["revision"]) is not int or row["revision"] != value["revision"]:
                raise BusinessError("android_ime_recovery_changed", "IME obligation changed; refresh before recovery")
            arguments = {
                "kind": "restore_ime",
                "environment_id": environment_id,
                "revision": row["revision"],
                "target_serial": row["device_serial"],
                "original_ime": row["original_ime"],
            }
            return await self._db.write(
                lambda u: u.accept_owner_command(
                    agent_id, value["session_id"], value["request_id"], fingerprint, arguments, tool=RESTORE_SPEC.name
                )
            )

    async def _model_authority(self, context: InvocationContext, access_id: str) -> dict[str, dict]:
        if self._closing:
            raise BusinessError("android_closing", "Android admission is closed")
        value = await self._db.read(lambda u: u.android_access_authorize(context.agent_id, context.run_id, access_id))
        parsed: dict[str, dict] = {}
        for key in ("grant", "connection", "access"):
            item = value.get(key)
            if not isinstance(item, dict):
                raise invalid("android_authority_invalid")
            parsed[key] = item
        if (
            value.get("session_id") != context.session_id
            or parsed["access"]["policy_revision"] != context.policy_revision
        ):
            raise BusinessError("android_run_denied", "Model context does not match its approved run")
        return parsed

    async def _observe(self, agent_id, grant_id, *, context=None, access_id=""):
        self._sweep()
        authority = (
            await self._model_authority(context, access_id) if context else await self._authority(agent_id, grant_id)
        )

        async def guard():
            current = (
                await self._model_authority(context, access_id)
                if context
                else await self._authority(agent_id, grant_id)
            )
            if current != authority:
                raise BusinessError("android_grant_changed", "Android device grant changed")

        result = await self._backend.execute(
            "android_observe",
            {
                "connection": authority["connection"],
                "device": authority["grant"]["device"],
                **({"allowed_packages": authority["access"]["packages"]} if context else {}),
            },
            context or self._context(agent_id),
            before_dispatch=guard,
        )
        await guard()
        if not isinstance(result, ActionResult) or result.status != Outcome.OK:
            raise BusinessError("android_observation_failed", "Android observation could not be verified", status=503)
        data = dict(result.data)
        raw_nodes = data.get("nodes")
        if (
            data.get("device") != authority["grant"]["device"]
            or not isinstance(raw_nodes, list)
            or len(raw_nodes) > 128
            or any(not isinstance(node, dict) for node in raw_nodes)
        ):
            raise invalid("android_protocol_invalid")
        observation_id = self._ids.new()
        unicode_authority: dict | None = None
        unicode_code = "android_unicode_unconfigured"
        try:
            unicode_authority = await self._db.read(lambda u: u.android_unicode_authority(agent_id, grant_id))
            unicode_code = "configured_not_probed"
        except BusinessError as error:
            unicode_code = error.code
        nodes: dict[str, dict] = {}
        for node in raw_nodes:
            if not isinstance(node, dict):
                raise invalid("android_protocol_invalid")
            nodes[self._ids.new()] = node
        self._observations[observation_id] = {
            "agent_id": agent_id,
            "grant_id": grant_id,
            "authority": authority,
            "nodes": nodes,
            "window": data["window"],
            "tree_sha256": data["tree_sha256"],
            "expires": self._clock.monotonic() + 60,
            "model_run_id": context.run_id if context else None,
            "model_access_id": access_id,
            "unicode_authority": unicode_authority,
        }
        return {
            "observation_id": observation_id,
            "grant_id": grant_id,
            "window": data["window"],
            "truncated": data["truncated"],
            "expires_in_s": 60,
            "target_serial": authority["grant"]["device"]["serial"],
            "target_user": authority["grant"]["device"]["user_id"],
            "observation_sha256": data["tree_sha256"],
            "unicode_helper": {
                "state": unicode_code,
                "revision": unicode_authority["profile"]["revision"] if unicode_authority else 0,
                "fingerprint": unicode_authority["profile"]["fingerprint"] if unicode_authority else "",
                "limitations": [
                    "Manually enabled helper and device-local consent required",
                    "Append at a collapsed end cursor only; no submit or global input monitoring",
                ],
            },
            "nodes": [
                {
                    "node_id": key,
                    **{k: v for k, v in node.items() if k not in {"path", "proof", "actions"}},
                    "actions": [a for a in node["actions"] if a != "append_unicode" or unicode_authority is not None],
                }
                for key, node in nodes.items()
            ],
        }

    async def dismiss(self, agent_id: str, observation_id: str):
        value = self._observations.get(observation_id)
        if value and value["agent_id"] == agent_id:
            self._observations.pop(observation_id)
        return {"closed": True}

    async def capture(self, agent_id: str, grant_id: str, session_id: str):
        return await self._owned(self._capture(agent_id, identity(grant_id), identity(session_id)))

    async def _capture(self, agent_id, grant_id, session_id):
        authority = await self._authority(agent_id, grant_id)
        epoch = await self._db.read(lambda u: u.capture_session(agent_id, session_id))

        async def guard():
            if await self._authority(agent_id, grant_id) != authority:
                raise BusinessError("android_grant_changed", "Android device grant changed")
            if await self._db.read(lambda u: u.capture_session(agent_id, session_id)) != epoch:
                raise BusinessError("context_invalidated", "Capture conversation changed")

        frame = await self._backend.execute(
            "android_capture",
            {"connection": authority["connection"], "device": authority["grant"]["device"]},
            self._context(agent_id),
            before_dispatch=guard,
        )
        if not isinstance(frame, CapturedFrame):
            raise BusinessError("android_capture_failed", "Android capture unavailable", status=503)
        return await self._objects.publish_capture(
            agent_id, session_id, epoch, self._ids.new(), frame, before_publish=guard
        )

    def _binding(self, agent_id: str, request: dict, context: InvocationContext | None = None):
        observed = self._observations.get(request["observation_id"])
        if (
            not observed
            or observed["agent_id"] != agent_id
            or observed["grant_id"] != request["grant_id"]
            or observed["expires"] <= self._clock.monotonic()
        ):
            raise BusinessError("android_observation_expired", "Observe this device again")
        if observed.get("model_run_id") and (
            context is None
            or observed["model_run_id"] != context.run_id
            or observed["model_access_id"] != request.get("access_id")
        ):
            raise BusinessError("android_observation_scope", "Model observation belongs to another run or access grant")
        node = observed["nodes"].get(request["node_id"])
        if not node or request["kind"] not in node["actions"] or not observed["window"]["unlocked"]:
            raise BusinessError("android_action_unavailable", "Selected control does not support this input")
        return observed, node

    async def submit(self, agent_id: str, value: object):
        request = action_request(value)
        return await self._owned(self._submit(agent_id, request))

    async def _submit(self, agent_id, request):
        if not self._accepting():
            raise BusinessError("not_ready", "Application is not accepting work", status=503)
        fingerprint = digest(request)
        async with self._admission:
            prior = await self._db.read(lambda u: u.owner_command_replay(agent_id, request["request_id"], fingerprint))
            if prior:
                return prior
            self._sweep()
            observed, node = self._binding(agent_id, request)
            await self._authority(agent_id, request["grant_id"])
            await self._unicode_binding(agent_id, request, observed)
            key = self._ids.new()
            target = observed["authority"]["grant"]["device"]
            arguments = {
                "binding_id": key,
                "grant_id": request["grant_id"],
                "kind": request["kind"],
                "text": request["text"],
                "target_application": observed["window"]["package"],
                "policy": POLICY,
                "target_serial": target["serial"],
                "target_user": target["user_id"],
                "target_resource": node["resource_id"],
                "observation_sha256": observed["tree_sha256"],
                "params": request["params"],
            }
            self._bindings[key] = {
                "agent_id": agent_id,
                "request": {k: v for k, v in request.items() if k != "text"},
                "observed": observed,
                "node": node,
                "expires": observed["expires"],
            }
            try:
                return await self._db.write(
                    lambda u: u.accept_owner_command(
                        agent_id, request["session_id"], request["request_id"], fingerprint, arguments, tool=SPEC.name
                    )
                )
            except BaseException:
                self._bindings.pop(key, None)
                raise

    async def invoke(
        self, name: str, arguments: Mapping[str, JSON], *, operation_id: str, context: InvocationContext
    ) -> ActionResult:
        return await self._owned(self._invoke(name, dict(arguments), operation_id, context))

    async def _invoke(self, name, arguments, operation_id, context):
        if name == RESTORE_SPEC.name:
            return await self._recover_ime(arguments, operation_id, context)
        if name == ACCESS_SPEC.name:
            try:
                checked = access_request(arguments)
                row = await self._db.write(
                    lambda u: u.android_access_create(context.agent_id, context.run_id, operation_id, checked)
                )
                return ActionResult(
                    Outcome.OK,
                    json.dumps(
                        {k: row[k] for k in ("id", "grant_id", "run_id", "packages", "kinds", "expires_at")},
                        ensure_ascii=False,
                    ),
                    Effect.APPLIED,
                    verification="run_access_granted",
                )
            except BusinessError as error:
                return ActionResult(Outcome.DENIED, error.message, error_code=error.code)
        if name == OBSERVE_SPEC.name:
            try:
                if set(arguments) - {"access_id", "operation"} or arguments.get("operation", "ui") not in {
                    "ui",
                    "apps",
                }:
                    raise invalid()
                access_id = identity(arguments.get("access_id"))
                authority = await self._model_authority(context, access_id)
                if arguments.get("operation", "ui") == "apps":
                    result = await self._apps(
                        context.agent_id, authority["grant"]["id"], context=context, access_id=access_id
                    )
                else:
                    result = await self._observe(
                        context.agent_id, authority["grant"]["id"], context=context, access_id=access_id
                    )
                return ActionResult(Outcome.OK, json.dumps(result, ensure_ascii=False))
            except BusinessError as error:
                return ActionResult(Outcome.DENIED, error.message, error_code=error.code)
        if name == MODEL_SPEC.name:
            try:
                access_id = identity(arguments.get("access_id"))
                authority = await self._model_authority(context, access_id)
                request = action_request(
                    {
                        "consent": True,
                        "grant_id": authority["grant"]["id"],
                        "session_id": context.session_id,
                        "request_id": operation_id,
                        **{k: arguments.get(k) for k in ("observation_id", "node_id", "kind", "text", "params")},
                    }
                )
                request["access_id"] = access_id
                observed, node = self._binding(context.agent_id, request, context)
                await self._unicode_binding(context.agent_id, request, observed)
                if request["kind"] not in authority["access"]["kinds"] or observed["authority"] != authority:
                    raise BusinessError("android_action_scope", "Input is outside this run's approved scope")
                expected = {
                    "target_serial": authority["grant"]["device"]["serial"],
                    "target_user": authority["grant"]["device"]["user_id"],
                    "target_application": observed["window"]["package"],
                    "target_resource": node["resource_id"],
                    "observation_sha256": observed["tree_sha256"],
                }
                if any(arguments.get(k) != v for k, v in expected.items()) or (
                    request["kind"] == "launch" and request["params"]["package"] not in authority["access"]["packages"]
                ):
                    raise BusinessError(
                        "android_target_changed", "Approval target does not match the authorized observation"
                    )
                binding = {
                    "agent_id": context.agent_id,
                    "request": {k: v for k, v in request.items() if k != "text"},
                    "observed": observed,
                    "node": node,
                }
                return await self._perform(binding, arguments, operation_id, context, name)
            except BusinessError as error:
                return ActionResult(Outcome.DENIED, error.message, error_code=error.code)
        binding = self._bindings.get(str(arguments.get("binding_id")))
        if name != SPEC.name or not binding or binding["agent_id"] != context.agent_id:
            return ActionResult(Outcome.DENIED, "Android observation expired", error_code="android_observation_expired")
        return await self._perform(binding, arguments, operation_id, context, name)

    async def _perform(self, binding, arguments, operation_id, context, tool):
        observed, node = binding["observed"], binding["node"]

        async def guard():
            current, current_node = self._binding(
                context.agent_id, binding["request"], context if tool == MODEL_SPEC.name else None
            )
            current_authority = (
                await self._model_authority(context, binding["request"]["access_id"])
                if tool == MODEL_SPEC.name
                else await self._authority(context.agent_id, arguments["grant_id"])
            )
            if current != observed or current_node != node or current_authority != observed["authority"]:
                raise BusinessError("android_target_changed", "Android target or device authority changed")
            await self._unicode_binding(context.agent_id, binding["request"], observed)
            if tool == MODEL_SPEC.name:
                await self._db.read(
                    lambda u: u.approved_dispatch(context.agent_id, context.run_id, operation_id, arguments, tool)
                )
            else:
                await self._db.read(
                    lambda u: u.owner_dispatch(context.agent_id, context.run_id, operation_id, arguments, tool)
                )
            await self._db.read(lambda u: u.android_dispatch(context.agent_id, observed["grant_id"], operation_id))

        try:
            await guard()
            await self._release_leases()
            if self._leases:
                raise BusinessError("android_cleanup_pending", "Previous Android resources are still owned")
            lease = await self._db.write(
                lambda u: u.lease_acquire(
                    context.agent_id, "android/" + observed["authority"]["grant"]["device_sha256"], context.run_id
                )
            )
            if lease is None:
                raise BusinessError("android_busy", "Android device resource is held")
            self._leases.append(lease)
            args = {
                "connection": observed["authority"]["connection"],
                "device": observed["authority"]["grant"]["device"],
                "window": observed["window"],
                "tree_sha256": observed["tree_sha256"],
                "node": node,
                "kind": arguments["kind"],
                "text": arguments["text"],
                "params": arguments.get("params", {}),
                **(
                    {"allowed_packages": observed["authority"]["access"]["packages"]} if tool == MODEL_SPEC.name else {}
                ),
            }
            options = {}
            if arguments["kind"] == "append_unicode":
                args.update(unicode_profile=observed["unicode_authority"], action_id=operation_id)
                environment_id = None

                async def environment(phase, data):
                    nonlocal environment_id
                    if phase == "planned":
                        row = await self._db.write(
                            lambda u: u.android_unicode_begin(
                                context.agent_id,
                                context.run_id,
                                operation_id,
                                arguments,
                                observed["unicode_authority"]["profile"]["id"],
                                data,
                            )
                        )
                        environment_id = identity(row["id"])
                    elif environment_id is not None:
                        captured_id = environment_id
                        await self._db.write(lambda u: u.android_unicode_observed(context.agent_id, captured_id, data))

                options["environment"] = environment
            result = await self._backend.execute(
                "android_ready", args, context, before_dispatch=guard, action=args, **options
            )
            if not isinstance(result, ActionResult):
                raise invalid("android_protocol_invalid")
            return result
        except BusinessError as error:
            return ActionResult(Outcome.DENIED, "Android input was not dispatched", error_code=error.code)
        finally:
            self._bindings.pop(str(arguments.get("binding_id", "")), None)
            self._observations.pop(binding["request"]["observation_id"], None)
            await self._release_leases()

    async def _unicode_binding(self, agent_id, request, observed):
        if request["kind"] != "append_unicode":
            return
        authority = observed.get("unicode_authority")
        if (
            not authority
            or request["params"]["helper_revision"] != authority["profile"]["revision"]
            or await self._db.read(lambda u: u.android_unicode_authority(agent_id, observed["grant_id"])) != authority
        ):
            raise BusinessError("android_unicode_profile_changed", "Configure the helper and observe again")

    async def _recover_ime(self, arguments, operation_id, context):
        captured = None

        async def guard():
            nonlocal captured
            row = await self._db.read(
                lambda u: u.android_unicode_recovery_dispatch(context.agent_id, context.run_id, operation_id, arguments)
            )
            if captured is not None and captured != row:
                raise BusinessError("android_ime_recovery_changed", "IME restoration obligation changed")
            captured = row

        try:
            await guard()
            await self._release_leases()
            if self._leases:
                raise BusinessError("android_cleanup_pending", "Android resources are still owned")
            row = captured
            if row is None:
                raise invalid()
            lease = await self._db.write(
                lambda u: u.lease_acquire(context.agent_id, "android/" + digest(row["device"]), context.run_id)
            )
            if lease is None:
                raise BusinessError("android_busy", "Android device resource is held")
            self._leases.append(lease)

            async def environment(phase, data):
                await self._db.write(lambda u: u.android_unicode_observed(context.agent_id, row["id"], data))

            result = await self._backend.execute(
                "android_unicode_restore",
                {
                    "connection": {"serial": row["device_serial"], "port": row["server_port"]},
                    "device": row["device"],
                    "environment": {
                        k: row[k] for k in ("prepare_nonce", "original_ime", "original_subtype", "enabled_sha256")
                    },
                },
                context,
                before_dispatch=guard,
                environment=environment,
            )
            if not isinstance(result, ActionResult):
                raise invalid("android_protocol_invalid")
            return result
        except BusinessError as error:
            return ActionResult(Outcome.DENIED, error.message, error_code=error.code)
        finally:
            await self._release_leases()

    async def access_page(self, agent_id: str, after: str = "", limit: int = 50):
        rows = await self._db.read(lambda u: u.android_access_page(agent_id, after, limit))
        return {"items": rows, "next_after": rows[-1]["id"] if len(rows) == limit else None}

    async def revoke_access(self, agent_id: str, access_id: str, revision: int):
        return await self._owned(self._db.write(lambda u: u.android_access_revoke(agent_id, access_id, revision)))

    async def apps(self, agent_id: str, grant_id: str, after: str = ""):
        return await self._owned(self._apps(agent_id, identity(grant_id), after=after))

    async def _apps(self, agent_id, grant_id, *, context=None, access_id="", after=""):
        authority = (
            await self._model_authority(context, access_id) if context else await self._authority(agent_id, grant_id)
        )

        async def guard():
            current = (
                await self._model_authority(context, access_id)
                if context
                else await self._authority(agent_id, grant_id)
            )
            if current != authority:
                raise BusinessError("android_access_revoked", "Application query authority changed")

        args = {
            "connection": authority["connection"],
            "device": authority["grant"]["device"],
            "after": after,
            **({"allowed_packages": authority["access"]["packages"]} if context else {}),
        }
        result = await self._backend.execute(
            "android_apps", args, context or self._context(agent_id), before_dispatch=guard
        )
        await guard()
        if not isinstance(result, ActionResult) or result.status != Outcome.OK:
            raise BusinessError("android_apps_unavailable", "Installed application query failed", status=503)
        return result.data

    async def _release_leases(self):
        if not self._backend.quiescent():
            return
        for lease in list(self._leases):
            await self._db.write(
                lambda u: u.lease_release(
                    str(lease["agent_id"]), str(lease["resource"]), str(lease["token"]), int(str(lease["fence"]))
                )
            )
            self._leases.remove(lease)

    def health(self):
        return {
            "pending": len(self._pending),
            "observations": len(self._observations),
            "leases": len(self._leases),
            "model_authority": "per_run_app_action_scope",
            "closing": self._closing,
            "backend": self._backend.health(),
        }

    def quiescent(self):
        return not self._pending and self._backend.quiescent()

    def stop_accepting(self):
        self._closing = True
        self._probes.clear()
        self._observations.clear()
        self._bindings.clear()

    async def close(self, timeout_s: float):
        self.stop_accepting()
        if self._pending:
            await asyncio.wait(self._pending, timeout=max(0, timeout_s))
        await self._release_leases()
        return not self._pending and not self._leases
