"""独立后台命令执行器：审批后入队、有界进程/日志、会话归属与诚实回收。"""
from __future__ import annotations

import codecs
import json
import os
import queue
import shutil
import subprocess
import threading
import time
import uuid
from dataclasses import asdict
from pathlib import Path

from ...config import CommandJobsConfig
from ...memory.command_jobs import FINAL, CommandJobStore
from ...system.process_identity import (
    ProcessIdentity,
    confirmed_exited,
    inspect_process,
    terminate_verified_tree,
)
from .danger import hard_blocked_reason
from .job_logs import CommandLog
from .process import user_env


def _spawn(command, shell, cwd):
    if os.name != 'nt':
        raise RuntimeError('后台命令当前仅验收 Windows')
    options={'powershell':['powershell.exe','-NoLogo','-NoProfile','-NonInteractive','-Command'],
             'pwsh':['pwsh.exe','-NoLogo','-NoProfile','-NonInteractive','-Command'],
             'cmd':['cmd.exe','/D','/S','/C']}
    args=options[shell]
    executable=shutil.which(args[0])
    if not executable:raise RuntimeError('所选 Shell 未安装')
    if shell != 'cmd':
        command='[Console]::OutputEncoding=[System.Text.UTF8Encoding]::new(); '+command
    return subprocess.Popen([executable,*args[1:],command],cwd=cwd,env=user_env(),stdin=subprocess.DEVNULL,
                            stdout=subprocess.PIPE,stderr=subprocess.PIPE,
                            creationflags=subprocess.CREATE_NO_WINDOW|subprocess.CREATE_NEW_PROCESS_GROUP)


class CommandJobManager:
    def __init__(self, store: CommandJobStore, log_dir: Path, *, root: Path, config: CommandJobsConfig,
                 spawner=_spawn, inspector=inspect_process, terminator=terminate_verified_tree, exited=confirmed_exited):
        self.store,self.log_dir,self.root,self.config=store,Path(log_dir),Path(root).resolve(),config
        self._spawn,self._inspect,self._terminate,self._exited=spawner,inspector,terminator,exited
        self.owner=uuid.uuid4().hex
        identity=self._inspect(os.getpid())
        self._owner_identity=json.dumps(asdict(identity)) if identity else ''
        self._queue=queue.Queue(maxsize=config.max_waiting)
        self._lock=threading.RLock()
        self._tasks={};self._workers=[];self._closed=False
        self._recover()

    def _recover(self):
        for row in self.store.unfinished():
            try:
                identity=ProcessIdentity(**json.loads(row['owner_identity']))
                if self._inspect(identity.pid)==identity:continue
            except (ValueError,TypeError):pass
            queued=row['status']=='queued'
            self.store.update(row['job_id'],row['owner'],status='cancelled' if queued else 'unknown',
                              side_effect_state='none' if queued else 'unknown',error_code='owner_lost')

    def _cleanup_logs(self):
        self.log_dir.mkdir(parents=True,exist_ok=True)
        rows=self.store.list(limit=200)
        active={row['job_id'] for row in self.store.unfinished()}
        now=time.time();files=[]
        for row in rows:
            if row['job_id'] in active:continue
            for suffix in ('.jsonl','.previous.jsonl'):
                path=self.log_dir/(row['job_id']+suffix)
                if path.is_symlink():raise OSError('日志路径非法')
                if not path.is_file():continue
                if now-row['updated_at']>self.config.retention_days*86400:path.unlink()
                else:files.append((row['updated_at'],path))
        total=sum(p.stat().st_size for p in self.log_dir.glob('*.jsonl') if p.is_file())
        for _,path in sorted(files,key=lambda item:item[0]):
            if total+self.config.log_bytes <= self.config.total_log_bytes:break
            size=path.stat().st_size;path.unlink();total-=size
        if total+self.config.log_bytes>self.config.total_log_bytes:
            raise RuntimeError('命令日志总额已满，请先核查活跃任务')

    def start(self, command: str, *, session_id: int, run_id: str, shell='powershell', cwd=None, timeout_s=None, parent=None):
        if not command.strip() or len(command)>16000:raise ValueError('命令为空或过长')
        blocked=hard_blocked_reason(command)
        if blocked:raise ValueError(blocked)
        if shell not in {'powershell','pwsh','cmd'}:raise ValueError('Shell 不在固定选项中')
        working=(self.root/str(cwd or '.')).resolve(strict=True)
        if not working.is_dir() or not working.is_relative_to(self.root):raise ValueError('任务目录必须在工作区内')
        timeout=float(timeout_s if timeout_s is not None else self.config.default_timeout_s)
        if not 1<=timeout<=7200:raise ValueError('超时必须为 1–7200 秒')
        with self._lock:
            if self._closed:raise RuntimeError('命令管理器正在关闭')
            if self._has_residual():
                raise RuntimeError('存在回收未知的命令或输出线程，先核查残留，禁止继续启动')
            if getattr(parent,'cancelled',False):raise RuntimeError('所属运行已取消，未创建任务')
            if self._queue.full():raise RuntimeError('后台任务等待队列已满')
            self._cleanup_logs()
            for identifier, previous in list(self._tasks.items()):
                if len(self._tasks)>100 and previous['done'].is_set() and not any(t.is_alive() for t in previous.get('readers',[])):
                    self._tasks.pop(identifier)
            row=self.store.create(session_id=session_id,run_id=run_id,owner=self.owner,owner_identity=self._owner_identity,
                                  command=command,shell=shell,cwd=str(working),timeout_s=timeout)
            task={'row':row,'cancel':threading.Event(),'done':threading.Event(),'parent':parent,
                  'log':CommandLog(self.log_dir,row['job_id'],self.config.log_bytes)}
            self._tasks[row['job_id']]=task
            self._queue.put_nowait(task)
            if not self._workers:
                for _ in range(self.config.max_running):
                    thread=threading.Thread(target=self._worker,daemon=True,name='command-job')
                    self._workers.append(thread);thread.start()
            return self.public(row)

    def _has_residual(self):
        return any((task['done'].is_set() or task.get('uncertain',False)) and
                   ((task.get('process') is not None and task['process'].poll() is None) or
                    any(thread.is_alive() for thread in task.get('readers',[]))) for task in self._tasks.values())

    @staticmethod
    def public(row):
        return {key:value for key,value in row.items() if key not in {'owner','owner_identity','process_identity'}}

    def _worker(self):
        while True:
            try:task=self._queue.get(timeout=.1)
            except queue.Empty:
                if self._closed:return
                continue
            try:self._execute(task)
            except Exception:
                task['uncertain']=True
                self.store.update(task['row']['job_id'],self.owner,status='unknown',side_effect_state='unknown',error_code='worker_failure')
            finally:
                task['done'].set();self._queue.task_done()

    def _cancelled(self, task):
        return task['cancel'].is_set() or getattr(task['parent'],'cancelled',False) or self._closed

    def _execute(self, task):
        row=task['row'];identifier=row['job_id'];proc=None;readers=[];read_errors=[]
        try:
            with self._lock:
                if self._cancelled(task) or self.store.get(identifier)['status']=='cancelled':
                    self.store.update(identifier,self.owner,status='cancelled');return
                if self._has_residual():
                    self.store.update(identifier,self.owner,status='cancelled',error_code='process_residual_guard');return
                proc=self._spawn(row['command'],row['shell'],row['cwd'])
                identity=self._inspect(proc.pid)
                task['process'],task['identity']=proc,identity
                self.store.update(identifier,self.owner,status='running',process_identity=json.dumps(asdict(identity)) if identity else '',side_effect_state='unknown')
            task['readers']=readers
            def drain(pipe, stream):
                decoder=codecs.getincrementaldecoder('utf-8')(errors='replace')
                try:
                    read=getattr(pipe,'read1',pipe.read)
                    while data:=read(4096):
                        text=decoder.decode(data)
                        if stream not in read_errors:
                            try:task['log'].append(stream,text)
                            except Exception:read_errors.append(stream)  # 继续排空，不能让子进程堵住。
                    tail=decoder.decode(b'',final=True)
                    if tail:task['log'].append(stream,tail)
                except Exception:read_errors.append(stream)
                finally:pipe.close()
            for stream in ('stdout','stderr'):
                worker=threading.Thread(target=drain,args=(getattr(proc,stream),stream),daemon=True)
                readers.append(worker);worker.start()
            deadline=time.monotonic()+row['timeout_s'];reason=''
            while proc.poll() is None:
                if self._cancelled(task):reason='cancelled';break
                if time.monotonic()>=deadline:reason='timed_out';break
                time.sleep(.03)
            reclaimed=True
            if reason:
                try:
                    if identity is None:raise RuntimeError('进程身份不可核验')
                    self._terminate(identity,timeout_s=3)
                    reclaimed=self._exited(identity)
                except Exception:reclaimed=False
            for reader in readers:reader.join(timeout=.5)
            if any(reader.is_alive() for reader in readers):reclaimed=False
            status=reason or ('succeeded' if proc.poll()==0 else 'failed')
            if not reclaimed:status='unknown'
            task['uncertain']=not reclaimed
            self.store.update(identifier,self.owner,status=status,exit_code=proc.poll(),
                              side_effect_state='unknown' if reason or not reclaimed else 'applied',
                              error_code='process_residual' if not reclaimed else ('log_incomplete' if read_errors else ''))
        except Exception:
            task['uncertain']=proc is not None
            if proc is not None:
                try:
                    identity=task.get('identity')
                    if identity:self._terminate(identity,timeout_s=3)
                except Exception:pass
            self.store.update(identifier,self.owner,status='unknown' if proc else 'failed',
                              side_effect_state='unknown' if proc else 'none',error_code='execution_error' if proc else 'spawn_failed')

    def list(self, *, session_id=None):
        return [self.public(row) for row in self.store.list(session_id=session_id)]

    def read(self, identifier, *, session_id=None, after=0, limit=16384):
        if type(after) is not int or after<0:raise ValueError('输出序号非法')
        row=self.store.get(identifier,session_id=session_id)
        with self._lock:task=self._tasks.get(identifier)
        log=task['log'] if task else CommandLog(self.log_dir,identifier,self.config.log_bytes)
        return {'job':self.public(row),**log.read(after,min(max(int(limit),8192),65536))}

    def cancel(self, identifier, *, session_id=None):
        row=self.store.get(identifier,session_id=session_id)
        if row['status'] in FINAL:return self.public(row)
        with self._lock:
            task=self._tasks.get(identifier)
            if task is None:raise RuntimeError('任务属于其它运行态，请从原入口取消；未按 PID 终止')
            task['cancel'].set()
            self.store.update(identifier,self.owner,status='cancelled' if row['status']=='queued' else 'cancelling',
                              side_effect_state='none' if row['status']=='queued' else 'unknown')
        return self.public(self.store.get(identifier))

    def close(self, wait_s=5):
        deadline=time.monotonic()+max(0,wait_s)
        with self._lock:
            self._closed=True
            tasks=list(self._tasks.values())
            for task in tasks:task['cancel'].set()
        for thread in self._workers:thread.join(max(0,deadline-time.monotonic()))
        residual=[task['row']['job_id'] for task in tasks if not task['done'].is_set() or
                  (task.get('process') is not None and task['process'].poll() is None) or
                  any(thread.is_alive() for thread in task.get('readers',[]))]
        if any(thread.is_alive() for thread in self._workers):residual.append('command_workers')
        return {'complete':not residual,'residual':residual}
