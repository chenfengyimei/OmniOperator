"""Owned process groups and Windows Job Objects. Never terminate an unverified PID."""

from __future__ import annotations

import asyncio
import ctypes
import os
import signal
import sys
from dataclasses import dataclass

import psutil


class WindowsJob:
    api: ctypes.CDLL

    def __init__(self, pid: int) -> None:
        if sys.platform != "win32":
            raise OSError("Windows Job Objects are unavailable on this platform")
        from ctypes import wintypes as w

        class Basic(ctypes.Structure):
            _fields_ = [
                ("process_time", ctypes.c_int64),
                ("job_time", ctypes.c_int64),
                ("flags", w.DWORD),
                ("minimum", ctypes.c_size_t),
                ("maximum", ctypes.c_size_t),
                ("active", w.DWORD),
                ("affinity", ctypes.c_size_t),
                ("priority", w.DWORD),
                ("scheduling", w.DWORD),
            ]

        class IO(ctypes.Structure):
            _fields_ = [
                (name, ctypes.c_uint64)
                for name in ("read_ops", "write_ops", "other_ops", "read_bytes", "write_bytes", "other_bytes")
            ]

        class Extended(ctypes.Structure):
            _fields_ = [
                ("basic", Basic),
                ("io", IO),
                ("process_memory", ctypes.c_size_t),
                ("job_memory", ctypes.c_size_t),
                ("peak_process", ctypes.c_size_t),
                ("peak_job", ctypes.c_size_t),
            ]

        self.api = ctypes.WinDLL("kernel32", use_last_error=True)
        signatures = {
            "CreateJobObjectW": ([ctypes.c_void_p, w.LPCWSTR], w.HANDLE),
            "SetInformationJobObject": ([w.HANDLE, ctypes.c_int, ctypes.c_void_p, w.DWORD], w.BOOL),
            "OpenProcess": ([w.DWORD, w.BOOL, w.DWORD], w.HANDLE),
            "AssignProcessToJobObject": ([w.HANDLE, w.HANDLE], w.BOOL),
            "TerminateJobObject": ([w.HANDLE, w.UINT], w.BOOL),
            "CloseHandle": ([w.HANDLE], w.BOOL),
            "QueryInformationJobObject": ([w.HANDLE, ctypes.c_int, ctypes.c_void_p, w.DWORD, ctypes.c_void_p], w.BOOL),
        }
        for name, (args, result) in signatures.items():
            function = getattr(self.api, name)
            function.argtypes, function.restype = args, result
        self.handle = self.api.CreateJobObjectW(None, None)
        process = self.api.OpenProcess(0x0100 | 0x0001, False, pid)
        try:
            limits = Extended()
            limits.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            if (
                not self.handle
                or not process
                or not self.api.SetInformationJobObject(self.handle, 9, ctypes.byref(limits), ctypes.sizeof(limits))
                or not self.api.AssignProcessToJobObject(self.handle, process)
            ):
                raise OSError("Cannot assign owned worker to Windows Job Object")
        except BaseException:
            self.close()
            raise
        finally:
            if process:
                self.api.CloseHandle(process)

    def terminate(self) -> bool:
        return bool(self.handle and self.api.TerminateJobObject(self.handle, 1))

    def close(self) -> None:
        if self.handle:
            self.api.CloseHandle(self.handle)
            self.handle = None

    def active_count(self) -> int | None:
        class Accounting(ctypes.Structure):
            _fields_ = [
                ("user", ctypes.c_int64),
                ("kernel", ctypes.c_int64),
                ("period_user", ctypes.c_int64),
                ("period_kernel", ctypes.c_int64),
                ("faults", ctypes.c_uint32),
                ("total", ctypes.c_uint32),
                ("active", ctypes.c_uint32),
                ("terminated", ctypes.c_uint32),
            ]

        info = Accounting()
        if not self.handle or not self.api.QueryInformationJobObject(
            self.handle, 1, ctypes.byref(info), ctypes.sizeof(info), None
        ):
            return None
        return info.active


@dataclass
class OwnedProcess:
    process: asyncio.subprocess.Process
    created: float
    job: WindowsJob | None
    reclaimed: bool = False

    @classmethod
    async def spawn(cls, argv: list[str], **kwargs) -> OwnedProcess:
        if os.name == "nt":
            kwargs["creationflags"] = 0x08000000  # CREATE_NO_WINDOW
        else:
            kwargs["start_new_session"] = True
        process = await asyncio.create_subprocess_exec(*argv, **kwargs)
        try:
            identity = psutil.Process(process.pid).create_time()
            job = WindowsJob(process.pid) if os.name == "nt" else None
            return cls(process, identity, job)
        except BaseException:
            if process.returncode is None:
                process.kill()
            await process.wait()
            raise

    async def terminate(self, timeout_s: float = 5) -> bool:
        if self.reclaimed:
            return True
        deadline = asyncio.get_running_loop().time() + max(0, timeout_s)
        try:
            if self.job:
                if not self.job.terminate():
                    return False
                while self.job.active_count() != 0:
                    if self.job.active_count() is None or asyncio.get_running_loop().time() >= deadline:
                        return False
                    await asyncio.sleep(0.01)
            elif self.process.returncode is None:
                current = psutil.Process(self.process.pid)
                if current.create_time() != self.created:
                    return False
                if sys.platform != "win32":
                    os.killpg(self.process.pid, signal.SIGKILL)
            elif sys.platform != "win32":
                # An exited leader does not prove its descendants exited. Do not
                # kill an unverifiable/reused process group or claim reclamation.
                try:
                    os.killpg(self.process.pid, 0)
                except ProcessLookupError:
                    pass
                else:
                    return False
            async with asyncio.timeout(max(0, deadline - asyncio.get_running_loop().time())):
                await self.process.wait()
            if sys.platform != "win32":
                while not self._group_quiet():
                    if asyncio.get_running_loop().time() >= deadline:
                        return False
                    await asyncio.sleep(0.01)
            self.reclaimed = True
            return True
        except (OSError, psutil.Error, TimeoutError):
            return False
        finally:
            if self.job and self.reclaimed:
                self.job.close()

    def _group_quiet(self) -> bool:
        if sys.platform == "win32":
            return False
        try:
            os.killpg(self.process.pid, 0)
        except ProcessLookupError:
            return True
        for process in psutil.process_iter(["pid", "status"]):
            try:
                if os.getpgid(process.pid) == self.process.pid and process.info["status"] != psutil.STATUS_ZOMBIE:
                    return False
            except (ProcessLookupError, psutil.NoSuchProcess):
                continue
            except (PermissionError, psutil.AccessDenied):
                return False
        return True
