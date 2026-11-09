"""Owned pipe peers with injected storage; no system keyring, native prompt or real secret."""

import asyncio
import uuid

from omni.adapters.workers.credentials import perform
from omni.domain.credential_ipc import control, decode, encode, request


class Store:
    def __init__(self):
        self.values = {"fixture/key": "private-native-read-canary"}
        self.calls = []
        self.fail = False

    def get(self, reference):
        self.calls.append("get")
        return self.values.get(reference)

    def set(self, reference, value):
        self.calls.append("set")
        self.values[reference] = value
        if self.fail:
            raise OSError("private-native-error-canary " + value)

    def delete(self, reference):
        self.calls.append("delete")
        self.values.pop(reference, None)


class Model:
    def __init__(self, fault=None):
        self.store = Store()
        self.fault = fault
        self.initial, self.dispatched = asyncio.Event(), asyncio.Event()
        self.ready_release, self.result_release = asyncio.Event(), asyncio.Event()
        self.ready_release.set()
        self.result_release.set()
        self.cleanup_allowed = True
        self.spawn_release = asyncio.Event()
        self.spawn_release.set()


class Input:
    def __init__(self, process):
        self.process, self.frames, self.closed = process, [], False

    def write(self, data):
        self.frames.append(decode(data))
        self.process.control.feed_data(data)

    async def drain(self):
        return None

    def close(self):
        if not self.closed:
            self.closed = True
            self.process.control.feed_eof()


class Process:
    def __init__(self, model):
        self.control, self.stdout, self.stderr = (asyncio.StreamReader() for _ in range(3))
        self.stdin, self.returncode = Input(self), None

        async def worker():
            try:
                initial = await self.control.readline()
                if not initial:
                    return
                query = request(decode(initial))
                model.initial.set()
                await model.ready_release.wait()
                ready = control(query, "ready")
                if model.fault == "wrong_ready":
                    ready["request_id"] = str(uuid.uuid4())
                self.stdout.feed_data(encode(ready))
                permission = await self.control.readline()
                if not permission or decode(permission) != control(query, "permit"):
                    return
                model.dispatched.set()
                result = perform(query, model.store)
                await model.result_release.wait()
                if model.fault == "lost" or model.fault == "lost_set" and query["operation"] == "set":
                    return
                if model.fault == "stderr":
                    self.stderr.feed_data(b"private-native-error-canary" * 3000)
                if model.fault == "wrong_result":
                    result["request_id"] = str(uuid.uuid4())
                if model.fault == "duplicate":
                    self.stdout.feed_data(encode(result))
                self.stdout.feed_data(encode(result))
                self.returncode = 0
            finally:
                if self.returncode is None:
                    self.returncode = 2
                self.stdout.feed_eof()
                self.stderr.feed_eof()

        self.task = asyncio.create_task(worker())

    async def wait(self):
        await self.task
        return self.returncode


class Owner:
    def __init__(self, process, model):
        self.process, self.model, self.reclaimed = process, model, False

    async def terminate(self, _timeout):
        if not self.model.cleanup_allowed:
            return False
        self.process.stdin.close()
        if not self.process.task.done():
            self.process.task.cancel()
        await asyncio.gather(self.process.task, return_exceptions=True)
        self.reclaimed = True
        return True


class Spawn:
    def __init__(self, model=None):
        self.model = model or Model()
        self.calls, self.owners = [], []

    async def __call__(self, argv, **kwargs):
        self.calls.append((argv, kwargs))
        await self.model.spawn_release.wait()
        owner = Owner(Process(self.model), self.model)
        self.owners.append(owner)
        return owner
