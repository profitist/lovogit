import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from functools import partial

from lovogit.index.api import Toolkit
from lovogit.index.builder import RepoIndex


async def run_index_operation(executor, operation, *args, **kwargs):
    worker = asyncio.get_running_loop().run_in_executor(
        executor, partial(operation, *args, **kwargs)
    )
    cancelled = False
    while True:
        try:
            result = await asyncio.shield(worker)
            break
        except asyncio.CancelledError:
            cancelled = True
        except Exception:
            if cancelled:
                raise asyncio.CancelledError from None
            raise
    if cancelled:
        raise asyncio.CancelledError
    return result


@asynccontextmanager
async def open_index(root, db_path):
    executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="lovogit-index")
    index = []

    def build():
        value = RepoIndex(str(root), str(db_path))
        index.append(value)
        value.build()
        return Toolkit(value)

    try:
        toolkit = await run_index_operation(executor, build)
        yield toolkit, executor
    finally:
        try:
            if index:
                await run_index_operation(
                    executor, index[0].index.storage.connection.close
                )
        finally:
            executor.shutdown(wait=True, cancel_futures=True)
