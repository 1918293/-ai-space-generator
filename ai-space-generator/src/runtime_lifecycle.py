from __future__ import annotations

import asyncio
from dataclasses import dataclass
import signal
from typing import Any


@dataclass
class RuntimeLifecycle:
    """Process lifecycle used by startup/readiness probes and SIGTERM drain."""

    started: bool = False
    draining: bool = False

    def mark_started(self) -> None:
        if self.draining:
            raise RuntimeError("CANNOT_START_WHILE_DRAINING")
        self.started = True

    def begin_shutdown(self) -> None:
        self.draining = True

    @property
    def live(self) -> bool:
        return True

    @property
    def startup_ready(self) -> bool:
        return self.started

    @property
    def traffic_ready(self) -> bool:
        return self.started and not self.draining


class ShutdownSignalController:
    """Portable signal-to-event bridge; production Cloud Run uses SIGTERM."""

    def __init__(self) -> None:
        self.event = asyncio.Event()
        self._installed: list[signal.Signals] = []

    def request_shutdown(self) -> None:
        self.event.set()

    def install(self, loop: Any) -> tuple[signal.Signals, ...]:
        for sig in (signal.SIGTERM, signal.SIGINT):
            try:
                loop.add_signal_handler(sig, self.request_shutdown)
                self._installed.append(sig)
            except (NotImplementedError, RuntimeError):
                pass
        return tuple(self._installed)

    def remove(self, loop: Any) -> None:
        for sig in self._installed:
            try:
                loop.remove_signal_handler(sig)
            except (NotImplementedError, RuntimeError):
                pass
        self._installed.clear()
