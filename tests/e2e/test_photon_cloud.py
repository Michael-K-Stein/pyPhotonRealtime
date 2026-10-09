"""End-to-end tests against the real Photon Cloud.

Opt-in: they only run with ``PHOTON_APP_ID`` set (a Realtime app id from the
Photon dashboard) and are excluded from the default run by the ``e2e`` marker::

    PHOTON_APP_ID=... python -m pytest -m e2e

They verify the wire protocol against live servers, which unit tests (which
only check self-consistency) can't do.
"""

from __future__ import annotations

import os
import time
from typing import TYPE_CHECKING

import pytest

from pyphotonrealtime import AppSettings, ClientState, PhotonPeer, RealtimeClient
from pyphotonrealtime.peer import PeerState, StatusCode

if TYPE_CHECKING:
    from pyphotonrealtime.peer import EventData, OperationResponse

APP_ID = os.environ.get("PHOTON_APP_ID", "")
REGION = os.environ.get("PHOTON_REGION", "eu")
# Override when the hosts file redirects the Name Server (e.g. to a local PA server).
NAME_SERVER = os.environ.get("PHOTON_NAME_SERVER", "ns.photonengine.io:4533")
TIMEOUT_SECONDS = 15.0

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skipif(not APP_ID, reason="PHOTON_APP_ID not set"),
]


def _service_until(client: RealtimeClient, state: ClientState) -> None:
    deadline = time.monotonic() + TIMEOUT_SECONDS
    while client.state != state:
        if time.monotonic() > deadline:
            pytest.fail(f"timed out in {client.state} waiting for {state}")
        client.service()
        time.sleep(1 / 30)


@pytest.mark.xfail(raises=NotImplementedError, reason="M2: connection workflow")
def test_connect_to_master() -> None:
    client = RealtimeClient()
    client.connect_using_settings(
        AppSettings(app_id_realtime=APP_ID, fixed_region=REGION)
    )
    _service_until(client, ClientState.ConnectedToMasterServer)
    client.disconnect()


class _StatusRecorder:
    def __init__(self) -> None:
        self.statuses: list[StatusCode] = []

    def on_status_changed(self, status: StatusCode) -> None:
        self.statuses.append(status)

    def on_operation_response(self, response: OperationResponse) -> None:
        pass

    def on_event(self, event: EventData) -> None:
        pass


def test_peer_init_and_encryption_with_name_server() -> None:
    listener = _StatusRecorder()
    peer = PhotonPeer(listener, keep_alive_interval=0.2)
    assert peer.connect(NAME_SERVER, APP_ID)

    deadline = time.monotonic() + TIMEOUT_SECONDS
    while peer.last_round_trip_time is None or not peer.is_encryption_available:
        if time.monotonic() > deadline:
            pytest.fail(f"timed out; statuses so far: {listener.statuses}")
        peer.service()
        if peer.state == PeerState.Connected and not peer.is_encryption_available:
            peer.establish_encryption()  # No-op while an exchange is in flight.
        time.sleep(1 / 30)

    assert listener.statuses == [StatusCode.Connect, StatusCode.EncryptionEstablished]
    peer.disconnect()
