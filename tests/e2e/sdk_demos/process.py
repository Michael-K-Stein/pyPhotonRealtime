"""Run a built SDK demo as a subprocess and watch its console output."""

from __future__ import annotations

import os
import queue
import re
import subprocess
import threading
import time
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from pathlib import Path

    from pyphotonrealtime import RealtimeClient

TIMEOUT_SECONDS = 20.0


class DemoProcess:
    """A demo subprocess; ``press`` feeds menu keys to stdin-driven demos."""

    def __init__(
        self, exe: Path, env: dict[str, str], *, exit_key: str | None = "0"
    ) -> None:
        """Start ``exe`` with ``env`` added to the environment.

        ``exit_key`` is pressed on close for a clean disconnect; None kills.
        """
        self._proc = subprocess.Popen(  # noqa: S603 -- our own build output
            [str(exe)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            env={**os.environ, **env},
        )
        self._exit_key = exit_key
        self.output = ""
        self._chunks: queue.Queue[bytes] = queue.Queue()
        threading.Thread(target=self._pump, daemon=True).start()

    def _pump(self) -> None:
        assert self._proc.stdout is not None
        while chunk := os.read(self._proc.stdout.fileno(), 4096):
            self._chunks.put(chunk)

    def press(self, key: str) -> None:
        assert self._proc.stdin is not None
        self._proc.stdin.write(f"{key}\n".encode())
        self._proc.stdin.flush()

    def poll_output(self, wait: float = 0.0) -> None:
        """Collect whatever the demo printed (waiting up to ``wait`` seconds)."""
        try:
            chunk = (
                self._chunks.get(timeout=wait) if wait else self._chunks.get_nowait()
            )
        except queue.Empty:
            return
        self.output += chunk.decode("utf-8", "replace")

    def expect(
        self, pattern: str, client: RealtimeClient | None = None
    ) -> re.Match[str]:
        """Wait for ``pattern`` in the demo's output, servicing ``client``.

        Returns:
            The first match.
        """
        deadline = time.monotonic() + TIMEOUT_SECONDS
        while not (match := re.search(pattern, self.output)):
            if time.monotonic() > deadline:
                pytest.fail(f"demo never printed {pattern!r}; output:\n{self.output}")
            if client is not None:
                client.service()
            self.poll_output(1 / 30)
        return match

    def close(self) -> None:
        if self._proc.poll() is not None:
            return
        if self._exit_key is not None:
            try:
                self.press(self._exit_key)
                self._proc.wait(timeout=5)
            except (OSError, subprocess.TimeoutExpired):
                pass
            else:
                return
        self._proc.kill()
        self._proc.wait()
