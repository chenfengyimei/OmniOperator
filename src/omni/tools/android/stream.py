"""按设备共享最新 JPEG；预览不产生观察授权，慢客户端跳过旧帧。"""
from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
import threading
import time
import uuid

from PIL import Image


@dataclass(frozen=True)
class PreviewFrame:
    seq: int
    jpeg: bytes
    captured_at: float
    duration_ms: float
    width: int
    height: int


class PreviewFeed:
    def __init__(self, serial):
        self.serial=serial
        self.condition=threading.Condition()
        self.clients={}
        self.frame: PreviewFrame | None=None
        self.error=''
        self.finished=False
        self.worker: threading.Thread | None=None
        self.idle_since=None


class FrameSubscription:
    def __init__(self, feed, identifier):
        self.feed,self.identifier=feed,identifier
        self.closed=False

    def next(self, after=0, wait_s=.25):
        with self.feed.condition:
            if self.closed or self.identifier not in self.feed.clients:return False
            self.feed.clients[self.identifier]=time.monotonic()
            if self.feed.frame is None or self.feed.frame.seq<=after:
                if not self.feed.finished:self.feed.condition.wait(max(0,wait_s))
            frame=self.feed.frame
            if frame and frame.seq>after:return frame
            return False if self.feed.finished else None

    def close(self):
        with self.feed.condition:
            self.closed=True
            self.feed.clients.pop(self.identifier,None)
            if not self.feed.clients:self.feed.idle_since=time.monotonic()
            self.feed.condition.notify_all()


class AndroidFrameHub:
    def __init__(self, capture, *, fps=2, max_clients=2, idle_s=5, max_devices=8):
        self.capture,self.fps,self.max_clients,self.idle_s=capture,fps,max_clients,idle_s
        self.max_devices=max_devices
        self._lock=threading.Lock();self._feeds={};self._closed=False

    def subscribe(self, serial):
        with self._lock:
            if self._closed:raise RuntimeError('看屏服务正在关闭')
            for key,feed in list(self._feeds.items()):
                if feed.finished and not feed.clients and key!=serial:self._feeds.pop(key)
            feed=self._feeds.get(serial)
            if feed:
                with feed.condition:
                    for key,seen in list(feed.clients.items()):
                        if time.monotonic()-seen>max(15,self.idle_s*2):feed.clients.pop(key)
            if feed and feed.finished and not feed.clients:feed=None
            if feed is None:
                if serial not in self._feeds and len(self._feeds)>=self.max_devices:raise RuntimeError('看屏设备数已达上限')
                feed=PreviewFeed(serial);self._feeds[serial]=feed
            with feed.condition:
                if len(feed.clients)>=self.max_clients:raise RuntimeError('该设备看屏连接已达上限')
                identifier=uuid.uuid4().hex;feed.clients[identifier]=time.monotonic();feed.idle_since=None
                feed.condition.notify_all()
                if feed.worker is None:
                    feed.worker=threading.Thread(target=self._produce,args=(feed,),name='android-preview',daemon=True)
                    feed.worker.start()
            return FrameSubscription(feed,identifier)

    def _produce(self, feed):
        seq=0
        try:
            while not self._closed:
                with feed.condition:
                    now=time.monotonic()
                    # 没有消费者续读的连接不能永久占用槽位。
                    for key,seen in list(feed.clients.items()):
                        if now-seen>max(15,self.idle_s*2):feed.clients.pop(key)
                    if not feed.clients:
                        if feed.idle_since is None:feed.idle_since=now
                        remaining=self.idle_s-(now-feed.idle_since)
                        if remaining<=0:break
                        feed.condition.wait(min(.25,remaining));continue
                started=time.monotonic()
                try:
                    png=self.capture(feed.serial)
                    with Image.open(BytesIO(png)) as image:
                        if image.width*image.height>32000000:raise ValueError('屏幕过大')
                        size=max(1,image.width//2),max(1,image.height//2)
                        with image.convert('RGB') as rgb:
                            with rgb.resize(size,Image.Resampling.BILINEAR) as frame:
                                output=BytesIO();frame.save(output,format='JPEG',quality=40)
                    jpeg=output.getvalue()
                    if len(jpeg)>2097152:raise ValueError('预览帧过大')
                    seq+=1
                    with feed.condition:
                        feed.frame=PreviewFrame(seq,jpeg,time.time(),round((time.monotonic()-started)*1000,1),*size)
                        feed.condition.notify_all()
                except Exception as exc:
                    if getattr(exc,'code','')=='device_busy':
                        pass  # 交互操作优先；只跳过帧，不重放动作。
                    else:
                        with feed.condition:feed.error=type(exc).__name__
                        break
                with feed.condition:
                    until=started+1/self.fps
                    while not self._closed and time.monotonic()<until:
                        feed.condition.wait(until-time.monotonic())
        finally:
            with feed.condition:feed.finished=True;feed.condition.notify_all()

    def status(self, serial=None):
        with self._lock:feeds=list(self._feeds.values())
        result=[]
        for feed in feeds:
            if serial is not None and feed.serial!=serial:continue
            with feed.condition:
                frame=feed.frame
                result.append({'serial':feed.serial,'clients':len(feed.clients),'active':bool(feed.clients) and not feed.finished,
                               'state':'failed' if feed.error else 'stopped' if feed.finished else 'streaming' if feed.clients else 'idle',
                               'worker_alive':bool(feed.worker and feed.worker.is_alive()),
                               'seq':frame.seq if frame else 0,'captured_at':frame.captured_at if frame else None,
                               'frame_age_s':round(max(0,time.time()-frame.captured_at),2) if frame else None,
                               'duration_ms':frame.duration_ms if frame else None,'error':feed.error})
        return result

    def close(self, wait_s=5):
        deadline=time.monotonic()+max(0,wait_s)
        with self._lock:self._closed=True;feeds=list(self._feeds.values())
        for feed in feeds:
            with feed.condition:feed.finished=True;feed.condition.notify_all()
        for feed in feeds:
            if feed.worker:feed.worker.join(max(0,deadline-time.monotonic()))
        residual=[feed.serial for feed in feeds if feed.worker and feed.worker.is_alive()]
        return {'complete':not residual,'residual':residual}
