"""Test-wide guard: every ``asyncio.run`` in a test gets an overall timeout.

A Modbus flow that never completes (a request nobody answers, a stuck shutdown) would otherwise hang
the whole pytest run. On timeout the stacks of all pending tasks are dumped to stderr, the main task is
cancelled and the test fails instead of hanging.
"""

import asyncio
import io
import os

import pytest

TEST_TIMEOUT = float(os.environ.get("SIM_TEST_TIMEOUT", "60"))
_real_run = asyncio.run


def _dump_tasks(title: str) -> str:
    out = io.StringIO()
    out.write(f"---- {title}: pending asyncio tasks ----\n")
    for t in asyncio.all_tasks():
        out.write(f"{t!r}\n")
        t.print_stack(file=out)
    return out.getvalue()


async def _guarded(coro):
    task = asyncio.ensure_future(coro)
    done, _ = await asyncio.wait({task}, timeout=TEST_TIMEOUT)
    if done:
        return task.result()
    dump = _dump_tasks(f"test exceeded {TEST_TIMEOUT:.0f} s")
    task.cancel()
    done, _ = await asyncio.wait({task}, timeout=10)
    if not done:
        dump += _dump_tasks("cancellation did not finish within 10 s")
    raise TimeoutError(f"test exceeded {TEST_TIMEOUT:.0f} s\n{dump}")


def _run_with_timeout(main, *, debug=None, **kw):
    return _real_run(_guarded(main), debug=debug, **kw)


@pytest.fixture(autouse=True)
def _asyncio_run_timeout(monkeypatch):
    monkeypatch.setattr(asyncio, "run", _run_with_timeout)
