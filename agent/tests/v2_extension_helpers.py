"""Static trusted-plugin fixtures and isolated runtime setup; never contacts a real model or account."""

import asyncio
import json

from v2_configuration_helpers import model_instance

from omni.domain.contracts import Invocation, ModelReply

PUBLIC_TOOL = "ext__fixture__echo"


def plugin_source(folder, *, marker=None, call_marker=None, crash=False, version="1", identity="fixture"):
    folder.mkdir(parents=True)
    manifest = {
        "format": "omni-python-extension-1",
        "id": identity,
        "version": version,
        "entry": "plugin.py",
        "tools": [
            {
                "name": "echo",
                "description": "Return the approved local value",
                "parameters": {
                    "type": "object",
                    "properties": {"value": {"type": "string", "maxLength": 1024}},
                    "required": ["value"],
                    "additionalProperties": False,
                },
                "writes": True,
                "timeout_s": 30,
            }
        ],
    }
    (folder / "extension.json").write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    body = "from pathlib import Path\nimport os\n"
    if marker is not None:
        body += f"Path({str(marker)!r}).write_text('imported',encoding='utf-8')\n"
    body += "async def invoke(name, arguments, context):\n"
    if call_marker is not None:
        body += f"    Path({str(call_marker)!r}).write_text(arguments['value'],encoding='utf-8')\n"
    if crash:
        body += "    os._exit(91)\n"
    body += "    print('plugin-private-stderr-canary')\n    return {'status':'ok','text':'plugin result: '+arguments['value'],'effect':'applied','error_code':'','retryable':False,'artifacts':[],'verification':'fixture','data':{'echo':arguments['value']}}\n"
    (folder / "plugin.py").write_text(body, encoding="utf-8")
    return folder


def installation(source, key="install", extension_id=None, revision=0):
    return {"key": key, "source": str(source), "extension_id": extension_id, "revision": revision}


def authorization(extension):
    return {
        "revision": extension["revision"],
        "package_sha256": extension["package_sha256"],
        "catalog_sha256": extension["catalog_sha256"],
        "history_epoch": extension["history_epoch"],
        "tools": [tool["name"] for tool in extension["tools"]],
        "trust": True,
    }


async def installed(instance, source):
    agent = (await instance.application.query("agents", ""))[0]["id"]
    result = await instance.extensions.install(agent, installation(source))
    return agent, result


async def configured(instance, agent):
    await model_instance(instance, agent)


async def wait_run(instance, agent, run_id, states):
    async with asyncio.timeout(15):
        while True:
            run = await instance.application.get_run(agent, run_id)
            if run["state"] in states:
                return run
            await asyncio.sleep(0.01)


class PluginModel:
    def __init__(self):
        self.catalogs = []

    async def complete(self, messages, tools, settings):
        self.catalogs.append(tuple(spec.name for spec in tools))
        if PUBLIC_TOOL in self.catalogs[-1] and not any(message["role"] == "tool" for message in messages):
            return ModelReply("", (Invocation("fixture-call", PUBLIC_TOOL, {"value": "approved-value"}),), cost_micro=2)
        return ModelReply("done", cost_micro=2)
