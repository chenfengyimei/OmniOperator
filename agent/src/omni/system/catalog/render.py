"""索引输出：从同一结构生成模块表、API 表和可机器查询的 JSON。"""

from __future__ import annotations

import json


def _link(path: str, line: int | None = None) -> str:
    return f"[{path}](../../{path}" + (f"#L{line}" if line else "") + ")"


def _cell(value: str) -> str:
    return value.replace("|", "\\|").replace("\n", " ")


def render_documents(index: dict) -> dict[str, str]:
    heading = "> 由 `uv run --no-sync python scripts/project_index.py` 生成。不要手改；分类改 system/catalog/schema.py，分层改 ARCHITECTURE.md。\n\n"
    modules = "# 模块与源码索引\n\n" + heading
    modules += f"源码指纹：`{index['source_sha256']}`。包含 {len(index['modules'])} 个职责模块、{len(index['files'])} 个源码/界面文件。\n\n"
    for module in index["modules"]:
        modules += f"## {module['title']} · {module['id']}\n\n分类：{module['category']}。{module['purpose']}\n\n"
        modules += "| 文件 | 行数 | 职责 |\n|---|---:|---|\n"
        for file in index["files"]:
            if file["module"] == module["id"]:
                modules += f"| {_link(file['path'])} | {file['lines']} | {_cell(file['summary'])} |\n"
        modules += "\n文档：" + "、".join(_link(path) for path in module["docs"]) + "\n\n"
        modules += "测试：" + ("、".join(_link(path) for path in module["tests"]) or "关联模块集成验证") + "\n\n"
        modules += (
            "直接静态依赖："
            + ("、".join(f"`{identity}`" for identity in module["dependencies"]) or "无内部模块依赖")
            + "\n\n"
        )
    apis = "# API、对话调用与工具声明\n\n" + heading
    apis += "工作台 API 统一受配对/Host/Origin 规则保护；终端 WS 单独握手校验。社区服务器是独立服务。请求字段为源码声明，完整 schema 可查询 FastAPI OpenAPI。\n\n"
    for flow in index["flows"]:
        apis += f"## {flow['title']}\n\n{flow['summary']}\n\n"
        apis += (
            " → ".join(f"`{step['symbol']}`（{_link(step['source'], step['line'])}）" for step in flow["steps"])
            + "\n\n"
        )
    apis += "## 声明路由\n\n| 服务 | 方法 | 路径 | 处理函数 | 定位 |\n|---|---|---|---|---|\n"
    for route in index["endpoints"]:
        apis += f"| {route['scope']} | {route['method']} | `{route['path']}` | `{route['handler']}` | {_link(route['source'], route['line'])} |\n"
    apis += "\n## 请求模型\n\n"
    for model in index["request_models"]:
        apis += f"### {model['name']}\n\n定位：{_link(model['source'], model['line'])}。\n\n"
        apis += "| 字段 | 类型 | 必填 |\n|---|---|---|\n"
        for field in model["fields"]:
            apis += f"| {field['name']} | `{_cell(field['type'])}` | {'是' if field['required'] else '否'} |\n"
        apis += "\n"
    apis += "## 静态工具声明\n\n不是启用清单；条件注册和插件工具以 `/api/workbench/tools` 的本进程快照为准。SAFE 不等于 read_only。\n\n| 工具 | 审批等级 | 强制人工 | 并发只读 | 模块 | 定位 |\n|---|---|---|---|---|---|\n"
    for tool in index["declared_tools"]:
        apis += f"| `{tool['name']}` | {tool['risk']} | {tool['requires_human']} | {tool['read_only']} | {tool['module']} | {_link(tool['source'], tool['line'])} |\n"
    return {
        "MODULES.md": modules.rstrip() + "\n",
        "API.md": apis.rstrip() + "\n",
        "project.json": json.dumps(index, ensure_ascii=False, indent=2) + "\n",
    }
