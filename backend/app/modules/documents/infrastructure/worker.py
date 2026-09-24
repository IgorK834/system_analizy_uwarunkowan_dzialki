"""Izolowany worker procesowy dla kosztownego PDF/OCR."""

from __future__ import annotations

import asyncio
import multiprocessing
import sys
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass
from typing import Any, Callable, TypeVar

T = TypeVar("T")


class DocumentWorkerError(RuntimeError):
    """Kontrolowany błąd izolowanego przetwarzania dokumentu."""


class DocumentWorkerTimeoutError(DocumentWorkerError):
    """Worker przekroczył limit czasu."""


class DocumentPageLimitError(DocumentWorkerError):
    """Dokument przekroczył jawny limit liczby stron."""


@dataclass(frozen=True)
class WorkerLimits:
    """Limity zasobów pojedynczego zadania dokumentowego."""

    timeout_seconds: float = 120.0
    cpu_seconds: int = 90
    max_pages: int = 120
    memory_mb: int = 1536


def _run_with_resource_limits(
    function: Callable[..., T],
    args: tuple[Any, ...],
    limits: WorkerLimits,
) -> T:
    # RLIMIT_AS jest wiarygodny w docelowym kontenerze Linux. macOS ma inną
    # semantykę pamięci wirtualnej, dlatego lokalnie pozostaje limit czasu/stron.
    if sys.platform.startswith("linux"):
        import resource

        memory_bytes = limits.memory_mb * 1024 * 1024
        resource.setrlimit(resource.RLIMIT_AS, (memory_bytes, memory_bytes))
        resource.setrlimit(
            resource.RLIMIT_CPU,
            (limits.cpu_seconds, limits.cpu_seconds + 1),
        )
    return function(*args)


class ProcessDocumentWorker:
    """Uruchamia jedno zadanie w osobnym procesie z ograniczonymi zasobami."""

    def __init__(self, limits: WorkerLimits | None = None) -> None:
        self.limits = limits or WorkerLimits()

    async def run(
        self,
        function: Callable[..., T],
        *args: Any,
    ) -> T:
        context = multiprocessing.get_context("spawn")
        executor = ProcessPoolExecutor(max_workers=1, mp_context=context)
        loop = asyncio.get_running_loop()
        future = loop.run_in_executor(
            executor,
            _run_with_resource_limits,
            function,
            args,
            self.limits,
        )
        try:
            return await asyncio.wait_for(
                future, timeout=self.limits.timeout_seconds
            )
        except TimeoutError as exc:
            future.cancel()
            _terminate_executor_workers(executor)
            raise DocumentWorkerTimeoutError(
                "Przetwarzanie dokumentu przekroczyło limit czasu."
            ) from exc
        except DocumentWorkerError:
            raise
        except Exception as exc:
            raise DocumentWorkerError(
                f"Worker dokumentu zakończył się błędem {type(exc).__name__}."
            ) from exc
        finally:
            # Każde wywołanie ma własny executor. Musimy zaczekać na jego
            # wątek zarządzający i proces, bo pozostawione wątki blokowały
            # późniejsze portale TestClient i w runtime kumulowały zasoby.
            executor.shutdown(wait=True, cancel_futures=True)


def _terminate_executor_workers(executor: ProcessPoolExecutor) -> None:
    """Kończy proces po timeout; Python 3.13 nie ma jeszcze publicznego API."""
    processes = getattr(executor, "_processes", None)
    if not isinstance(processes, dict):
        return
    for process in processes.values():
        if process.is_alive():
            process.terminate()
