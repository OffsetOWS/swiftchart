"""Bounded CPU/SQLite workers; crypto jobs never monopolize the HTTP event loop."""
from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from contextvars import ContextVar, copy_context
from functools import partial
from weakref import WeakKeyDictionary

_EXECUTOR = ThreadPoolExecutor(max_workers=2, thread_name_prefix="crypto-work")
_LIMITS = WeakKeyDictionary()
_READ_EXECUTOR = ThreadPoolExecutor(max_workers=1, thread_name_prefix="crypto-read")
_READ_LIMITS = WeakKeyDictionary()
_INTERACTIVE = ContextVar("interactive_crypto_reads", default=False)


@contextmanager
def interactive_crypto_reads():
    """Reserve bounded capacity for short reads while scans occupy bulk workers."""
    token = _INTERACTIVE.set(True)
    try:
        yield
    finally:
        _INTERACTIVE.reset(token)


async def run_crypto_work(function, /, *args, **kwargs):
    loop = asyncio.get_running_loop()
    # Bound outstanding work as well as active threads. Separate loops in tests
    # must not inherit an asyncio primitive bound to an earlier closed loop.
    interactive = _INTERACTIVE.get()
    limits = _READ_LIMITS if interactive else _LIMITS
    executor = _READ_EXECUTOR if interactive else _EXECUTOR
    limit = limits.setdefault(loop, asyncio.Semaphore(2 if interactive else 4))
    await limit.acquire()
    context = copy_context()
    try:
        future = loop.run_in_executor(executor, partial(context.run, function, *args, **kwargs))
    except BaseException:
        limit.release()
        raise
    def completed(done):
        limit.release()
        if not done.cancelled():
            done.exception()  # Consume errors even if the original caller disconnected.
    future.add_done_callback(completed)
    # A disconnected caller must not release its slot while its thread still runs.
    return await asyncio.shield(future)
