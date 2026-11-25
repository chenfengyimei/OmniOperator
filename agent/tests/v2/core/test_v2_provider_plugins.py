"""Provider registration through the production instance and its typed injection ports."""

import asyncio
from uuid import uuid4

import pytest
from omni_test_support.v2.v2_delivery_helpers import FakePort

from omni.adapters.persistence.delivery_repository import DeliveryRepositories
from omni.adapters.platform.runtime.paths import InstancePaths
from omni.application.bilibili import PREPARE_SPEC, READ_SPEC
from omni.application.qq import QQService
from omni.composition import instance as bootstrap
from omni.composition import model_providers, platform_providers
from omni.composition.features import desktop as desktop_feature
from omni.composition.instance import open_instance
from omni.composition.lifecycle import Lifetime
from omni.domain.desktop_actions import SPEC as DESKTOP_ACTION


@pytest.mark.parametrize("injected", [False, True])
def test_model_transport_binds_the_kernel_and_probe_with_one_owner(tmp_path, monkeypatch, injected):
    class Transport:
        def __init__(self):
            self.stops = 0
            self.closes = 0

        def health(self):
            return {"enabled": True, "owned": 0}

        def stop_accepting(self):
            self.stops += 1

        async def close(self, timeout_s):
            self.closes += 1
            return True

    transport = Transport()
    factories = []

    def default_provider(secrets, redactor, configure_logging):
        factories.append(secrets)
        return transport

    monkeypatch.setattr(model_providers, "LiteLLMModel", default_provider)

    async def scenario():
        model = object() if injected else None
        options = {"model": model, "model_streamer": transport} if injected else {}
        instance = await open_instance(tmp_path, capabilities=(), **options)
        try:
            calls = instance.application._calls
            primary = model if injected else transport
            assert instance.model_port is primary
            assert calls._model is primary
            assert calls._preparer is (None if injected else transport)
            assert calls._streamer is transport
            assert len(factories) == (0 if injected else 1)
        finally:
            assert (await instance.close())["complete"]
        assert (await instance.close())["complete"]
        assert (transport.stops, transport.closes) == (1, 1)

    asyncio.run(scenario())


class ChannelTransport(FakePort):
    def __init__(self, name, events, *, can_close=True):
        super().__init__()
        self.name, self.events, self.can_close = name, events, can_close

    async def close(self, timeout_s):
        self.events.append(self.name)
        return self.can_close


def test_instance_drains_users_before_closing_shared_channels_and_releasing_leases(tmp_path, monkeypatch):
    events = []
    onebot = ChannelTransport("onebot", events, can_close=False)
    bilibili = ChannelTransport("bilibili", events)
    sender = ChannelTransport("sender", events)
    original = DeliveryRepositories.delivery_release_boot

    def release_leases(repository):
        events.append("release_leases")
        return original(repository)

    async def drain_channel_user(timeout_s):
        events.append("drained")
        return True

    monkeypatch.setattr(DeliveryRepositories, "delivery_release_boot", release_leases)

    async def scenario():
        instance = await open_instance(
            tmp_path,
            capabilities=(),
            qq_transport=onebot,
            bilibili_backend=bilibili,
            delivery_ports={"qq": sender, "android": onebot},
        )
        instance.lifetime.own("fixture_channel_user", 0, drain_channel_user)
        try:
            assert instance.feature_services["qq"] is instance.qq
            assert instance.feature_services["bilibili"] is instance.bilibili
            assert instance.qq._transport is onebot
            assert instance.bilibili._gateway is bilibili
            assert instance.media._bilibili is bilibili
            assert instance.deliveries._ports == {"qq": sender, "bilibili": bilibili, "android": onebot}
            assert not await instance.deliveries.release_boot_leases(1)
            result = await instance.close(15)
            assert result == {"complete": False, "residual": ["onebot"]}
            assert events[0] == "drained"
            assert sorted(events[1:]) == ["bilibili", "onebot", "sender"]
            assert not instance._database.metrics["closed"]
            onebot.can_close = True
            assert (await instance.close(15))["complete"]
            assert events[-2:] == ["onebot", "release_leases"]
            assert (await instance.close())["complete"]
            assert len(events) == 6
        finally:
            onebot.can_close = True
            assert (await instance.close())["complete"]

    asyncio.run(scenario())


def test_startup_failure_reclaims_one_shared_transport(tmp_path, monkeypatch):
    events = []
    transport = ChannelTransport("shared", events)

    class FailedApplication(bootstrap.Application):
        async def start(self):
            raise RuntimeError("test startup failure")

    async def scenario():
        with monkeypatch.context() as patch:
            patch.setattr(bootstrap, "Application", FailedApplication)
            with pytest.raises(RuntimeError, match="test startup failure"):
                await open_instance(
                    tmp_path,
                    capabilities=(),
                    qq_transport=transport,
                    bilibili_backend=transport,
                    delivery_ports={"android": transport},
                )
        assert events == ["shared"]
        reopened = await open_instance(tmp_path, capabilities=())
        assert (await reopened.close())["complete"]

    asyncio.run(scenario())


def test_qq_feature_start_failure_drains_listener_before_closing_shared_transport(tmp_path, monkeypatch):
    events = []
    ready = []
    started = []
    start_kernel = bootstrap.Application.start
    start_qq = QQService.start

    async def record_kernel(application):
        await start_kernel(application)
        ready.append(application)

    def fail_after_start(service):
        assert ready[-1].health()["ready"]
        start_qq(service)
        started.append(service)
        raise RuntimeError("test QQ startup failure")

    class SharedChannel(ChannelTransport):
        async def close(self, timeout_s):
            assert started[0]._worker.done()
            return await super().close(timeout_s)

    async def scenario():
        transport = SharedChannel("shared", events)
        with monkeypatch.context() as patch:
            patch.setattr(bootstrap.Application, "start", record_kernel)
            patch.setattr(QQService, "start", fail_after_start)
            with pytest.raises(RuntimeError, match="test QQ startup failure"):
                await open_instance(tmp_path, qq_transport=transport, bilibili_backend=transport)
        assert events == ["shared"]

        reopened = await open_instance(tmp_path)
        try:
            for spec in (READ_SPEC, PREPARE_SPEC):
                assert reopened.application._tools[spec.name].adapter is reopened.bilibili
        finally:
            assert (await reopened.close())["complete"]

    asyncio.run(scenario())


class NativePort:
    def __init__(self):
        self.closes = 0

    def health(self):
        return {"cleanup_pending": 0}

    def stop_accepting(self):
        pass

    async def close(self, timeout_s):
        self.closes += 1
        return True


@pytest.mark.parametrize(
    ("host", "skill_platform", "macos_transport"),
    [("win32", "windows", "posix"), ("darwin", "macos", "launchservices"), ("linux", "linux", "posix")],
)
def test_platform_provider_selects_host_and_shares_desktop_capacity(
    tmp_path, monkeypatch, host, skill_platform, macos_transport
):
    options = {}

    def capture(name):
        def create(*args, **kwargs):
            options[name] = args, kwargs
            return NativePort()

        return create

    monkeypatch.setattr(platform_providers, "TerminalBackend", capture("terminal"))
    monkeypatch.setattr(platform_providers, "DesktopBackend", capture("desktop"))
    monkeypatch.setattr(platform_providers, "PortalBackend", capture("portal"))
    monkeypatch.setattr(platform_providers, "DesktopControlBackend", capture("control"))

    async def scenario():
        lifetime = Lifetime()
        paths = InstancePaths.resolve(tmp_path)
        provider = platform_providers.platform_provider(host)

        def validate(_):
            pass

        ports = provider.register(lifetime, paths, validate)
        try:
            assert provider.skill_platform == skill_platform
            assert options["terminal"] == ((validate,), {})
            assert options["desktop"][1]["macos_transport"] == macos_transport
            capacity = options["desktop"][1]["capacity"]
            assert options["portal"][1]["capacity"] is capacity is options["control"][1]["capacity"]
            assert options["desktop"][1]["capture_root"] == paths.control / "desktop-captures"
            assert options["portal"][1]["capture_root"] == paths.control / "portal-captures"
        finally:
            assert (await lifetime.close())["complete"]
        assert all(port.closes == 1 for port in (ports.terminal, ports.desktop, ports.portal, ports.control))

    asyncio.run(scenario())


@pytest.mark.parametrize("startup_failure", [False, True])
def test_instance_uses_the_injected_platform_ports_and_closes_them_once(tmp_path, monkeypatch, startup_failure):
    terminal, desktop, portal, control = (NativePort() for _ in range(4))
    options = {
        "terminal_backend": terminal,
        "desktop_backend": desktop,
        "portal_backend": portal,
        "control_backend": control,
    }

    async def scenario():
        if startup_failure:

            def fail_control_service(*args, **kwargs):
                raise RuntimeError("control service startup failure")

            monkeypatch.setattr(desktop_feature, "DesktopControlService", fail_control_service)
            with pytest.raises(RuntimeError, match="control service startup failure"):
                await open_instance(tmp_path, capabilities=(), **options)
        else:
            instance = await open_instance(tmp_path, capabilities=(), **options)
            try:
                feature = instance.feature_services["desktop"]
                assert feature.observations is instance.desktop
                assert feature.portal is instance.desktop_portal
                assert feature.control is instance.desktop_control
                assert feature.actions is instance.desktop_actions
                assert instance.application._owner_tools[DESKTOP_ACTION.name].adapter is feature.actions
                assert DESKTOP_ACTION.name not in instance.application._tools
                assert instance.terminals._backend is terminal
                assert instance.desktop._backend is desktop
                assert instance.desktop_actions._backend is desktop
                assert instance.desktop_portal._backend is portal
                assert instance.desktop_control._backend is control
            finally:
                assert (await instance.close())["complete"]
        assert [port.closes for port in (terminal, desktop, portal, control)] == [1, 1, 1, 1]

    asyncio.run(scenario())


def test_desktop_feature_releases_both_grants_when_a_view_is_revoked(tmp_path, monkeypatch):
    calls = []

    async def scenario():
        instance = await open_instance(tmp_path, capabilities=())
        try:
            agents = await instance.database.read(lambda u: u.query("agents", ""))
            agent = agents[0]["id"]
            view = str(uuid4())
            await instance.desktop.begin(agent, {"consent": True, "view_id": view})

            async def detach_portal(service, agent_id, view_id):
                calls.append((service, agent_id, view_id))
                raise RuntimeError("test portal cleanup is pending")

            async def detach_control(service, agent_id, view_id):
                calls.append((service, agent_id, view_id))

            with monkeypatch.context() as patch:
                patch.setattr(desktop_feature.DesktopPortalService, "detach", detach_portal)
                patch.setattr(desktop_feature.DesktopControlService, "detach", detach_control)
                with pytest.raises(RuntimeError, match="test portal cleanup is pending"):
                    await instance.desktop.forget(agent, view)

            assert calls == [(instance.desktop_portal, agent, view), (instance.desktop_control, agent, view)]
            assert not (await instance.desktop.forget(agent, view))["forgotten"]
        finally:
            assert (await instance.close())["complete"]

    asyncio.run(scenario())
