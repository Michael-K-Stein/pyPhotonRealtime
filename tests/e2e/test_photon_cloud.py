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

import pytest

from pyphotonrealtime import AppSettings, ClientState, RealtimeClient

APP_ID = os.environ.get("PHOTON_APP_ID", "")
REGION = os.environ.get("PHOTON_REGION", "eu")
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
