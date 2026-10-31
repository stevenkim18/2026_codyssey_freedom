"""Opt-in process-local wall intervals, never model-kernel time or public telemetry.

Context propagation follows asyncio tasks/to_thread. A snapshot closes the shared
collector, including active worker intervals, so late completion cannot revise it.
"""
import asyncio
from contextlib import contextmanager
from contextvars import ContextVar
import threading
import time


_STAGES = ("queue", "preprocessing", "model_call", "postprocessing", "rules")
_current = ContextVar("inspection_timing", default=None)


class _Collector:
    def __init__(self, clock):
        self.clock = clock
        self.started = clock()
        self.lock = threading.Lock()
        self.closed = False
        self.outcome = "error"
        self.active = {}
        self.values = {name:{"elapsed_ms":0.0,"completed":0,"failed":0,"active_at_snapshot":0} for name in _STAGES}

    def start(self, name):
        with self.lock:
            if self.closed: return None
            token = object()
            self.active[token] = (name, self.clock())
            return token

    def stop(self, token, failed):
        with self.lock:
            if self.closed or token is None: return
            name, started = self.active.pop(token)
            value = self.values[name]
            value["elapsed_ms"] += max(0.0, self.clock()-started)*1000
            value["failed" if failed else "completed"] += 1

    def snapshot(self):
        with self.lock:
            ended = self.clock()
            self.closed = True
            for name, started in self.active.values():
                self.values[name]["elapsed_ms"] += max(0.0, ended-started)*1000
                self.values[name]["active_at_snapshot"] += 1
            self.active.clear()
            return {"scope":"handler_wall_intervals", "outcome":self.outcome,
                    "total_ms":max(0.0, ended-self.started)*1000,
                    "stages":{name:dict(value) for name,value in self.values.items()}}


def outcome(value):
    if value not in {"complete", "incomplete", "error", "cancelled"}: raise ValueError("invalid timing outcome")
    collector = _current.get()
    if collector is not None:
        with collector.lock:
            if not collector.closed: collector.outcome = value


@contextmanager
def observe(observer, *, clock=None):
    if observer is None:
        yield
        return
    collector = _Collector(clock or time.monotonic)
    token = _current.set(collector)
    try:
        yield
    except asyncio.CancelledError:
        outcome("cancelled")
        raise
    except BaseException:
        outcome("error")
        raise
    finally:
        _current.reset(token)
        report = collector.snapshot()
        try:
            observer(report)
        except BaseException:
            # Trusted diagnostics must not change fail-closed behavior or echo errors.
            pass


@contextmanager
def stage(name):
    if name not in _STAGES: raise ValueError("invalid timing stage")
    collector = _current.get()
    if collector is None:
        yield
        return
    token = collector.start(name)
    failed = True
    try:
        yield
        failed = False
    finally:
        collector.stop(token, failed)
