"""Bounded public CoreFoundation/ServiceManagement reads for a separately owned root worker; no mutation or GUI."""

from __future__ import annotations

import ctypes
import json
import math
from typing import Any, cast


class NativeJobs:
    def __init__(self) -> None:
        self.cf = ctypes.CDLL("/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation")
        self.sm = ctypes.CDLL("/System/Library/Frameworks/ServiceManagement.framework/ServiceManagement")
        p, n, b = ctypes.c_void_p, ctypes.c_long, ctypes.c_bool
        for name, args, result in (
            ("CFRelease", [p], None),
            ("CFGetTypeID", [p], ctypes.c_ulong),
            ("CFStringCreateWithCString", [p, ctypes.c_char_p, ctypes.c_uint32], p),
            ("CFStringGetLength", [p], n),
            ("CFStringGetCString", [p, p, n, ctypes.c_uint32], b),
            ("CFDictionaryGetCount", [p], n),
            ("CFDictionaryGetKeysAndValues", [p, p, p], None),
            ("CFDictionaryGetValue", [p, p], p),
            ("CFArrayGetCount", [p], n),
            ("CFArrayGetValueAtIndex", [p, n], p),
            ("CFBooleanGetValue", [p], b),
            ("CFNumberIsFloatType", [p], b),
            ("CFNumberGetValue", [p, ctypes.c_int, p], b),
        ):
            method = getattr(self.cf, name)
            method.argtypes, method.restype = args, result
        self.types: dict[str, int] = {}
        for kind in ("String", "Dictionary", "Array", "Boolean", "Number", "Null"):
            method = getattr(self.cf, "CF" + kind + "GetTypeID")
            method.argtypes, method.restype = [], ctypes.c_ulong
            self.types[kind] = method()
        self.sm.SMJobCopyDictionary.argtypes, self.sm.SMJobCopyDictionary.restype = [p, p], p
        self.sm.SMCopyAllJobDictionaries.argtypes, self.sm.SMCopyAllJobDictionaries.restype = [p], p
        self.domain = p.in_dll(self.sm, "kSMDomainSystemLaunchd").value
        if not self.domain:
            raise ValueError("native domain unavailable")

    def string(self, value: str) -> int:
        result = self.cf.CFStringCreateWithCString(None, value.encode(), 0x08000100)
        if not result:
            raise ValueError("native allocation unavailable")
        return result

    def text(self, value: int, maximum: int = 4096) -> str:
        if self.cf.CFGetTypeID(value) != self.types["String"]:
            raise ValueError("native string invalid")
        size = self.cf.CFStringGetLength(value)
        if not 0 <= size <= maximum:
            raise ValueError("native string limit")
        buffer = ctypes.create_string_buffer(size * 4 + 1)
        if not self.cf.CFStringGetCString(value, buffer, len(buffer), 0x08000100):
            raise ValueError("native string invalid")
        result = buffer.value.decode("utf-8", errors="strict")
        if len(result.encode()) > maximum or len(result.encode("utf-16-le")) // 2 != size:
            raise ValueError("native string limit")
        return result

    def value(self, pointer: int, depth: int = 0, budget: list[int] | None = None) -> Any:
        budget = [0, 0] if budget is None else budget
        budget[0] += 1
        if not pointer or depth > 8 or budget[0] > 1024:
            raise ValueError("native value limit")
        kind = self.cf.CFGetTypeID(pointer)
        if kind == self.types["String"]:
            result = self.text(pointer)
            budget[1] += len(result.encode())
            if budget[1] > 32768:
                raise ValueError("native text limit")
            return result
        if kind == self.types["Dictionary"]:
            count = self.cf.CFDictionaryGetCount(pointer)
            if not 0 <= count <= 64:
                raise ValueError("native dictionary limit")
            keys, values = (ctypes.c_void_p * count)(), (ctypes.c_void_p * count)()
            self.cf.CFDictionaryGetKeysAndValues(pointer, keys, values)
            mapping: dict[str, Any] = {}
            for key, value in zip(keys, values, strict=True):
                name = self.value(key, depth + 1, budget)
                if not isinstance(name, str) or name in mapping:
                    raise ValueError("native dictionary invalid")
                mapping[name] = self.value(value, depth + 1, budget)
            return mapping
        if kind == self.types["Array"]:
            count = self.cf.CFArrayGetCount(pointer)
            if not 0 <= count <= 64:
                raise ValueError("native array limit")
            return [self.value(self.cf.CFArrayGetValueAtIndex(pointer, i), depth + 1, budget) for i in range(count)]
        if kind == self.types["Boolean"]:
            return bool(self.cf.CFBooleanGetValue(pointer))
        if kind == self.types["Number"]:
            floating = self.cf.CFNumberIsFloatType(pointer)
            number = ctypes.c_double() if floating else ctypes.c_int64()
            if not self.cf.CFNumberGetValue(pointer, 6 if floating else 4, ctypes.byref(number)) or (
                floating and not math.isfinite(number.value)
            ):
                raise ValueError("native number invalid")
            return number.value
        if kind == self.types["Null"]:
            return None
        raise ValueError("native value unsupported")

    def copy(self, label: int) -> dict[str, Any] | None:
        pointer = self.sm.SMJobCopyDictionary(self.domain, label)
        if not pointer:
            return None
        try:
            result = self.value(pointer)
            if not isinstance(result, dict):
                raise ValueError("native job invalid")
            return cast(dict[str, Any], result)
        finally:
            self.cf.CFRelease(pointer)

    def query(self, label: str) -> dict[str, Any]:
        selected, label_key = self.string(label), self.string("Label")
        try:
            first = self.copy(selected)
            if first is not None:
                second = self.copy(selected)
                if first.get("Label") != label or json.dumps(first, sort_keys=True) != json.dumps(
                    second, sort_keys=True
                ):
                    raise ValueError("native definition changed")
                return {"complete": True, "loaded": True, "job": first}
            jobs = self.sm.SMCopyAllJobDictionaries(self.domain)
            if not jobs:
                raise ValueError("native enumeration unavailable")
            try:
                if self.cf.CFGetTypeID(jobs) != self.types["Array"]:
                    raise ValueError("native enumeration invalid")
                count = self.cf.CFArrayGetCount(jobs)
                if not 0 <= count <= 4096:
                    raise ValueError("native enumeration limit")
                for index in range(count):
                    item = self.cf.CFArrayGetValueAtIndex(jobs, index)
                    if not item or self.cf.CFGetTypeID(item) != self.types["Dictionary"]:
                        raise ValueError("native enumeration invalid")
                    name = self.cf.CFDictionaryGetValue(item, label_key)
                    if not name or self.text(name, 256) == label:
                        raise ValueError("native enumeration changed")
            finally:
                self.cf.CFRelease(jobs)
            if self.copy(selected) is not None:
                raise ValueError("native definition changed")
            return {"complete": True, "loaded": False, "job": None}
        finally:
            self.cf.CFRelease(selected)
            self.cf.CFRelease(label_key)
