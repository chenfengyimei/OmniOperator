"""命令任务元数据仓储；不执行或终止操作系统进程。"""
from __future__ import annotations

import time
import uuid
from .state import StateStore

FINAL = frozenset({'succeeded','failed','cancelled','timed_out','unknown'})


class CommandJobStore:
    def __init__(self, state: StateStore):
        self.state = state

    def create(self, *, session_id, run_id, owner, owner_identity, command, shell, cwd, timeout_s):
        identifier = uuid.uuid4().hex
        now=time.time()
        with self.state._tx() as conn:
            conn.execute('INSERT INTO command_jobs(job_id,session_id,run_id,owner,owner_identity,command,shell,cwd,timeout_s,status,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
                         (identifier,session_id,run_id,owner,owner_identity,command,shell,cwd,timeout_s,'queued',now,now))
        return self.get(identifier)

    def get(self, identifier, *, session_id=None):
        with self.state._tx() as conn:
            row=conn.execute('SELECT * FROM command_jobs WHERE job_id=?',(identifier,)).fetchone()
        if row is None or (session_id is not None and row['session_id'] != session_id):
            raise KeyError('命令任务不存在')
        return dict(row)

    def list(self, *, session_id=None, limit=100):
        where=' WHERE session_id=?' if session_id is not None else ''
        args=[session_id] if session_id is not None else []
        with self.state._tx() as conn:
            return [dict(row) for row in conn.execute('SELECT * FROM command_jobs'+where+' ORDER BY created_at DESC LIMIT ?',(*args,max(1,min(limit,200))))]

    def update(self, identifier, owner, *, status, process_identity=None, exit_code=None, side_effect_state='none', error_code=''):
        if status not in FINAL | {'queued','running','cancelling'}:
            raise ValueError('任务状态非法')
        with self.state._tx() as conn:
            conn.execute('UPDATE command_jobs SET status=?, updated_at=?, process_identity=COALESCE(?,process_identity), exit_code=?, side_effect_state=?, error_code=? WHERE job_id=? AND owner=?',
                         (status,time.time(),process_identity,exit_code,side_effect_state,error_code,identifier,owner))

    def unfinished(self):
        with self.state._tx() as conn:
            return [dict(row) for row in conn.execute("SELECT * FROM command_jobs WHERE status IN ('queued','running','cancelling')")]
