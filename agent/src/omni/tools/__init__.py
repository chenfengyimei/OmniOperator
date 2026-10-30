"""工具层：registry（注册表）+ builtin（内置工具集）+ 后续各能力域包。

规则：能力域之间互不 import，只通过 registry 汇合。
"""
from .registry import Tool, ToolRegistry

__all__ = ["Tool", "ToolRegistry"]
