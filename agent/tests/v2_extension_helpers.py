"""Static trusted-plugin fixtures and isolated runtime setup; never contacts a real model or account."""

import asyncio
import json

from v2_configuration_helpers import model_instance

from omni.domain.contracts import Invocation, ModelReply

PUBLIC_TOOL = "ext__fixture__echo"


def plugin_source(
    folder,
    *,
    marker=None,
    call_marker=None,
    crash=False,
    version="1",
    identity="fixture",
    artifact_type=None,
    artifact_name="report.txt",
    artifact_body=b"artifact-body",
    artifact_mode="ok",
    host_mode=None,
):
    folder.mkdir(parents=True)
    tool = {
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
    if artifact_type is not None:
        tool["artifacts"] = [artifact_type]
    manifest = {
        "format": "omni-python-extension-1",
        "id": identity,
        "version": version,
        "entry": "plugin.py",
        "tools": [tool],
    }
    (folder / "extension.json").write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    body = "from pathlib import Path\nimport os\n"
    if marker is not None:
        body += f"Path({str(marker)!r}).write_text('imported',encoding='utf-8')\n"
    body += "async def invoke(name, arguments, context):\n"
    if host_mode is not None:
        body = body.removesuffix("async def invoke(name, arguments, context):\n")
        body += "async def invoke(name, arguments, context, host):\n"
        if host_mode == "happy":
            body += (
                "    info = host.call('context')\n"
                "    try:\n"
                "        host.call('not-allowed')\n"
                "        denied = False\n"
                "    except PermissionError:\n"
                "        denied = True\n"
                "    host.call('log', text='fixture-callback-log')\n"
                "    return {'status':'ok','text':'plugin result: '+arguments['value']+' ctx='+info['tool']+' denied='+str(denied),"
                "'effect':'applied','error_code':'','retryable':False,'artifacts':[],'verification':'fixture',"
                "'data':{'echo':arguments['value'],'host':{'tool':info['tool'],'extension_id':info['extension_id']}}}\n"
            )
        elif host_mode == "over_budget":
            body += (
                "    for _ in range(17):\n"
                "        host.call('log', text='x')\n"
                "    return {'status':'ok','text':'unreachable','effect':'applied','error_code':'','retryable':False,'artifacts':[],'verification':'fixture','data':{}}\n"
            )
        else:
            body += (
                "    host.call('log', text='x' * 2000)\n"
                "    return {'status':'ok','text':'unreachable','effect':'applied','error_code':'','retryable':False,'artifacts':[],'verification':'fixture','data':{}}\n"
            )
    if call_marker is not None:
        body += f"    Path({str(call_marker)!r}).write_text(arguments['value'],encoding='utf-8')\n"
    if crash:
        body += "    os._exit(91)\n"
    body += "    print('plugin-private-stderr-canary')\n"
    if artifact_type is None:
        body += "    return {'status':'ok','text':'plugin result: '+arguments['value'],'effect':'applied','error_code':'','retryable':False,'artifacts':[],'verification':'fixture','data':{'echo':arguments['value']}}\n"
    else:
        root = 'os.environ["OMNI_EXTENSION_ARTIFACTS"]'
        if artifact_mode in {"escape", "missing"}:
            target = (
                f"os.path.join({root}, '..', {artifact_name!r})"
                if artifact_mode == "escape"
                else f"os.path.join({root}, 'absent-' + {artifact_name!r})"
            )
        else:
            target = f"os.path.join({root}, {artifact_name!r})"
        media = "text/plain" if artifact_mode == "undeclared" else artifact_type
        payload = "b'X' * (9 * 1024 * 1024)" if artifact_mode == "oversize" else repr(artifact_body)
        body += (
            f"    Path({target}).write_bytes({payload})\n"
            "    return {'status':'ok','text':'plugin result: '+arguments['value'],'effect':'applied',"
            f"'error_code':'','retryable':False,'artifacts':[],'verification':'fixture',"
            f"'data':{{'echo':arguments['value'],'artifacts':[{{'name':{artifact_name!r},'media_type':{media!r}}}]}}}}\n"
        )
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
