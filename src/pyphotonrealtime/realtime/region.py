"""Cloud regions from the Name Server, and picking the closest one."""

from __future__ import annotations

import select
import socket
import time
from dataclasses import dataclass

from pyphotonrealtime.peer import split_address


@dataclass(slots=True)
class Region:
    """One cloud region: its code, optional cluster and Master Server address."""

    code: str
    address: str
    cluster: str = ""
    ping: float | None = None
    """Best TCP connect time to the Master Server in ms; None if unreachable."""

    @classmethod
    def from_name_server(cls, code: str, address: str) -> Region:
        """Parse a ``code[/cluster]`` entry from the ``GetRegions`` response.

        Returns:
            The region, with a lower-case code.
        """
        code, _, cluster = code.partition("/")
        return cls(code=code.lower(), address=address, cluster=cluster)


class _Probe:
    def __init__(self, region: Region, attempts: int, port: int | None) -> None:
        self.region = region
        self.attempts_left = attempts
        self.port = port
        self.sock: socket.socket | None = None
        self.started = 0.0

    def start(self, now: float) -> None:
        self.attempts_left -= 1
        try:
            host, port, _ = split_address(self.region.address)
            family, kind, proto, _, sockaddr = socket.getaddrinfo(
                host, self.port or port, type=socket.SOCK_STREAM
            )[0]
            sock = socket.socket(family, kind, proto)
        except (OSError, ValueError):
            self.attempts_left = 0
            return
        sock.setblocking(False)  # noqa: FBT003
        sock.connect_ex(sockaddr)
        self.sock = sock
        self.started = now

    def finish(self, now: float, *, ok: bool) -> None:
        if self.sock is not None:
            self.sock.close()
            self.sock = None
        if ok:
            rtt = (now - self.started) * 1000
            if self.region.ping is None or rtt < self.region.ping:
                self.region.ping = rtt


class RegionPinger:
    """Measures TCP connect time to every region's Master Server, without blocking.

    The SDKs ping over UDP; a TCP handshake costs one round trip, so it ranks
    regions the same way. Call :meth:`poll` until it returns True.
    """

    def __init__(
        self,
        regions: list[Region],
        *,
        attempts: int = 2,
        timeout: float = 2.0,
        port: int | None = None,
    ) -> None:
        """Prepare to ping ``regions``; nothing is sent before the first ``poll``.

        Args:
            regions: Regions to ping; their ``ping`` is filled in.
            attempts: Pings per region; the best one counts.
            timeout: Seconds before a ping counts as lost.
            port: TCP port to ping instead of the one in each address.
        """
        self.regions = regions
        self.timeout = timeout
        self._probes = [_Probe(region, attempts, port) for region in regions]

    def poll(self) -> bool:
        """Advance the pings.

        Returns:
            Whether every region has been pinged (or given up on).
        """
        now = self._now()
        for probe in self._probes:
            if probe.sock is None and probe.attempts_left > 0:
                probe.start(now)
        pending = {p.sock: p for p in self._probes if p.sock is not None}
        if not pending:
            return True
        sockets = list(pending)
        _, writable, failed = select.select([], sockets, sockets, 0)
        for sock in failed:
            pending.pop(sock).finish(now, ok=False)
        for sock in writable:
            if sock in pending:
                ok = sock.getsockopt(socket.SOL_SOCKET, socket.SO_ERROR) == 0
                pending.pop(sock).finish(now, ok=ok)
        for probe in pending.values():
            if now - probe.started > self.timeout:
                probe.finish(now, ok=False)
        return all(p.sock is None and p.attempts_left == 0 for p in self._probes)

    def close(self) -> None:
        """Abort any pings still in flight."""
        for probe in self._probes:
            probe.attempts_left = 0
            if probe.sock is not None:
                probe.sock.close()
                probe.sock = None

    @property
    def best_region(self) -> Region | None:
        """The region with the lowest ping, or None if none answered."""
        reachable = [r for r in self.regions if r.ping is not None]
        return min(reachable, key=lambda r: r.ping or 0.0, default=None)

    @staticmethod
    def _now() -> float:
        return time.monotonic()
