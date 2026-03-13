"""任务事件总线 - 线程安全的发布/订阅，用于 SSE 实时推送"""

import queue
import threading
from collections import defaultdict


class TaskEventBus:
    """线程安全的任务事件总线，支持多个订阅者"""

    def __init__(self):
        self._subscribers: dict[int, list[queue.Queue]] = defaultdict(list)
        self._lock = threading.Lock()

    def subscribe(self, task_id: int) -> queue.Queue:
        q = queue.Queue(maxsize=1000)
        with self._lock:
            self._subscribers[task_id].append(q)
        return q

    def unsubscribe(self, task_id: int, q: queue.Queue):
        with self._lock:
            subs = self._subscribers.get(task_id, [])
            if q in subs:
                subs.remove(q)
            if not subs:
                self._subscribers.pop(task_id, None)

    def emit(self, task_id: int, event: dict):
        with self._lock:
            for q in self._subscribers.get(task_id, []):
                try:
                    q.put_nowait(event)
                except queue.Full:
                    pass


event_bus = TaskEventBus()
