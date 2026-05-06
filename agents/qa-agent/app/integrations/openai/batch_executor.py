from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from typing import Callable, Sequence, TypeVar


T = TypeVar("T")
R = TypeVar("R")


def chunked(items: Sequence[T], chunk_size: int) -> list[list[T]]:
    size = max(1, chunk_size)
    return [list(items[index : index + size]) for index in range(0, len(items), size)]


def run_chunked_parallel(
    items: Sequence[T],
    *,
    chunk_size: int,
    max_workers: int,
    worker: Callable[[list[T]], list[R]],
) -> list[R]:
    batches = chunked(items, chunk_size)
    if not batches:
        return []
    if len(batches) == 1 or max_workers <= 1:
        nested = [worker(batch) for batch in batches]
    else:
        with ThreadPoolExecutor(max_workers=min(max_workers, len(batches))) as executor:
            nested = list(executor.map(worker, batches))
    return [item for group in nested for item in group]
