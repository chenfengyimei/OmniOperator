"""按文件调度测试；崩溃重排仍保留全部未完成用例与原有测试约束。"""

from __future__ import annotations

from xdist.scheduler.loadfile import LoadFileScheduling
from xdist.workermanage import WorkerController


class CrashSafeLoadFileScheduling(LoadFileScheduling):
    """Keep file ownership while allowing a replacement worker to make progress."""

    def _reschedule(self, node: WorkerController) -> None:
        if node.shutting_down:
            return
        # Crash recovery can put completed scopes back in the global queue.
        # Sending an empty unit produces no completion event to refill a worker.
        for scope, unit in list(self.workqueue.items()):
            if all(unit.values()):
                del self.workqueue[scope]
        # A fresh worker may receive a one-test file. It needs a second index
        # (or shutdown) before it can execute its first test.
        while self.workqueue and self._pending_of(self.assigned_work[node]) <= 2:
            super()._reschedule(node)
        if not self.workqueue:
            super()._reschedule(node)
