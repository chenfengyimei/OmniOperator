"""命令输出的有界 JSONL 环：序号续读、跨流串行、最近日志与缺口标记。"""
from __future__ import annotations

import json
from pathlib import Path
import threading


class CommandLog:
    def __init__(self, directory: Path, identifier: str, limit: int):
        self.path = directory / (identifier + '.jsonl')
        self.previous = directory / (identifier + '.previous.jsonl')
        self.limit, self.lock = limit, threading.Lock()
        self.seq = 0

    def append(self, stream: str, text: str):
        with self.lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            for start in range(0,len(text),2048):
                self.seq += 1
                payload = json.dumps({'seq':self.seq,'stream':stream,'text':text[start:start+2048]},ensure_ascii=False)+'\n'
                if self.path.is_symlink() or self.previous.is_symlink():
                    raise OSError('日志路径不能是符号链接')
                if self.path.exists() and self.path.stat().st_size + len(payload.encode()) > self.limit // 2:
                    self.path.replace(self.previous)
                with self.path.open('a',encoding='utf-8') as file:
                    file.write(payload)

    def read(self, after=0, limit=16384):
        with self.lock:
            records=[]
            for path in (self.previous,self.path):
                if path.is_symlink(): raise OSError('日志路径非法')
                if path.exists():
                    if path.stat().st_size > self.limit: raise OSError('日志超出上限')
                    records.extend(json.loads(line) for line in path.read_text(encoding='utf-8').splitlines())
            selected=[];size=0
            for row in records:
                if row['seq']<=after:continue
                row_size=len(row['text'].encode('utf-8'))
                if size+row_size>limit:break
                selected.append(row);size+=row_size
            return {'records':selected,'next_after':selected[-1]['seq'] if selected else after,
                    'gap':bool(records and after<records[0]['seq']-1)}
