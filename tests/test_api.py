"""Smoke tests for the public API surface."""

import importlib
import pkgutil

import pyphotonrealtime
from pyphotonrealtime import AppSettings, ClientState, RealtimeClient
from pyphotonrealtime.realtime import MatchmakingCallbacks


def test_all_modules_import() -> None:
    for module in pkgutil.walk_packages(
        pyphotonrealtime.__path__, pyphotonrealtime.__name__ + "."
    ):
        importlib.import_module(module.name)


def test_client_initial_state() -> None:
    client = RealtimeClient()
    assert client.state == ClientState.PeerCreated
    assert not client.in_room
    assert not client.is_connected_and_ready


def test_callbacks_are_dispatched() -> None:
    calls = []

    class Target(MatchmakingCallbacks):
        def on_joined_room(self) -> None:
            calls.append("joined")

    client = RealtimeClient()
    target = Target()
    client.add_callback_target(target)
    client._emit("on_joined_room")
    client._emit("on_left_room")  # default no-op
    client.remove_callback_target(target)
    client._emit("on_joined_room")
    assert calls == ["joined"]


def test_app_settings_defaults() -> None:
    settings = AppSettings(app_id_realtime="abc")
    assert settings.use_name_server
