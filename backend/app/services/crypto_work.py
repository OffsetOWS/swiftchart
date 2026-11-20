"""Bounded CPU/SQLite workers; crypto jobs never monopolize the HTTP event loop."""
from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
from functools import partial
from weakref import WeakKeyDictionary

_EXECUTOR = ThreadPoolExecutor(max_workers=2, thread_name_prefix="crypto-work")
_LIMITS = WeakKeyDictionary()


async def run_crypto_work(function, /, *args, **kwargs):
    loop = asyncio.get_running_loop()
    # Bound outstanding work as well as active threads. Separate loops in tests
    # must not inherit an asyncio primitive bound to an earlier closed loop.
    limit = _LIMITS.setdefault(loop, asyncio.Semaphore(4))
    await limit.acquire()
    context = copy_context()
    try:
        future = loop.run_in_executor(_EXECUTOR, partial(context.run, function, *args, **kwargs))
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
