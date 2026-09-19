import asyncio
import signal

from src.runtime_lifecycle import RuntimeLifecycle, ShutdownSignalController


def test_runtime_lifecycle_separates_liveness_startup_and_readiness():
    lifecycle = RuntimeLifecycle()
    assert lifecycle.live is True
    assert lifecycle.startup_ready is False
    assert lifecycle.traffic_ready is False

    lifecycle.mark_started()
    assert lifecycle.startup_ready is True
    assert lifecycle.traffic_ready is True

    lifecycle.begin_shutdown()
    assert lifecycle.live is True
    assert lifecycle.startup_ready is True
    assert lifecycle.traffic_ready is False


class FakeLoop:
    def __init__(self):
        self.added = []
        self.removed = []

    def add_signal_handler(self, sig, callback):
        self.added.append((sig, callback))

    def remove_signal_handler(self, sig):
        self.removed.append(sig)


def test_shutdown_signal_controller_installs_sigterm_and_unblocks_drain():
    async def scenario():
        loop = FakeLoop()
        controller = ShutdownSignalController()
        installed = controller.install(loop)
        assert installed == (signal.SIGTERM, signal.SIGINT)
        assert controller.event.is_set() is False

        sigterm_callback = loop.added[0][1]
        sigterm_callback()
        await controller.event.wait()
        assert controller.event.is_set() is True

        controller.remove(loop)
        assert loop.removed == [signal.SIGTERM, signal.SIGINT]

    asyncio.run(scenario())
