"""``PhotonServer`` extension points and ``PhotonProxy``."""

from __future__ import annotations

import socket
import time
from typing import TYPE_CHECKING, Any, override

from pyphotonrealtime import AppSettings, ClientState, RealtimeClient
from pyphotonrealtime.protocol.operation_code import OperationCode
from pyphotonrealtime.protocol.packet.operation_packet import PhotonOperationPacket
from pyphotonrealtime.protocol.param.hashtable_param import HashtableParameter
from pyphotonrealtime.protocol.param.int8_param import Int8Parameter
from pyphotonrealtime.realtime import EnterRoomParams, ErrorCode
from pyphotonrealtime.server import (
    Direction,
    GameServer,
    PhotonProxy,
    PhotonServer,
    Role,
)
from pyphotonrealtime.server.rooms import MAX_PLAYERS
from test_server import APP_ID, ROOM, TIMEOUT, Recorder, create, settle, wait

if TYPE_CHECKING:
    from pyphotonrealtime.peer import Parameters
    from pyphotonrealtime.protocol.packet.base import PhotonPacket
    from pyphotonrealtime.server import (
        ProxySession,
    )
    from pyphotonrealtime.server.rooms import Room

OTHER_APP_ID = "11111111-1111-1111-1111-111111111111"


def free_ports(**options: Any) -> PhotonServer:  # noqa: ANN401
    return PhotonServer(
        name_server_port=0, master_server_port=0, game_server_port=0, **options
    )


def connect_to(
    ns_host: str,
    ns_port: int,
    nick: str = "",
    **settings: Any,  # noqa: ANN401
) -> tuple[RealtimeClient, Recorder]:
    client = RealtimeClient()
    client.local_player.nick_name = nick
    recorder = Recorder()
    client.add_callback_target(recorder)
    assert client.connect_using_settings(
        AppSettings(
            app_id_realtime=APP_ID,
            name_server=ns_host,
            name_server_port=ns_port,
            fixed_region="local",
            **settings,
        )
    )
    wait(lambda: client.state == ClientState.ConnectedToMasterServer, client)
    return client, recorder


# -- passthrough -----------------------------------------------------------------------


def test_other_app_ids_are_relayed_upstream() -> None:
    with (
        free_ports() as upstream,
        free_ports(
            app_id=OTHER_APP_ID, passthrough=("127.0.0.1", upstream.name_server_port)
        ) as server,
    ):
        client, _ = connect_to(server.host, server.name_server_port)
        # The upstream Name Server sent the client on to its own Master Server.
        assert str(client.peer.server_address).endswith(
            f":{upstream.master_server_port}"
        )
        assert not list(server.connections_of(Role.MasterServer))
        client.disconnect()


def test_accepted_app_ids_are_served_locally() -> None:
    with (
        free_ports() as upstream,
        free_ports(
            app_id=APP_ID, passthrough=("127.0.0.1", upstream.name_server_port)
        ) as server,
    ):
        client, _ = connect_to(server.host, server.name_server_port)
        assert str(client.peer.server_address).endswith(f":{server.master_server_port}")
        client.disconnect()


# -- idle timeout ----------------------------------------------------------------------


def test_silent_clients_are_dropped() -> None:
    with free_ports(idle_timeout=0.2) as server:
        sock = socket.create_connection((server.host, server.name_server_port))
        sock.settimeout(TIMEOUT)
        with sock:
            started = time.monotonic()
            assert sock.recv(1) == b""
            assert time.monotonic() - started < TIMEOUT


def test_clients_that_keep_alive_stay() -> None:
    with free_ports(idle_timeout=0.5) as server:
        client = RealtimeClient()
        client.peer.keep_alive_interval = 0.1
        assert client.connect_using_settings(
            AppSettings(
                app_id_realtime=APP_ID,
                name_server=server.host,
                name_server_port=server.name_server_port,
                fixed_region="local",
            )
        )
        wait(lambda: client.state == ClientState.ConnectedToMasterServer, client)
        settle(client, seconds=1.0)
        assert client.state == ClientState.ConnectedToMasterServer
        client.disconnect()


# -- handlers --------------------------------------------------------------------------


class TwoPlayerGameServer(GameServer):
    @override
    def create_room(self, name: str, params: Parameters) -> Room:
        room = super().create_room(name, params)
        room.properties.update(
            HashtableParameter({Int8Parameter(MAX_PLAYERS): Int8Parameter(2)})
        )
        return room


def test_custom_game_server_caps_rooms() -> None:
    with free_ports(handlers={Role.GameServer: TwoPlayerGameServer}) as server:
        assert isinstance(server.handlers[Role.GameServer], TwoPlayerGameServer)
        clients = [
            connect_to(server.host, server.name_server_port)[0] for _ in range(3)
        ]
        create(clients[0], max_players=8)
        for client in clients[1:]:
            assert client.op_join_room(EnterRoomParams(room_name=ROOM))
        recorder = Recorder()
        clients[2].add_callback_target(recorder)
        wait(lambda: clients[1].state == ClientState.Joined, *clients)
        wait(lambda: bool(recorder.calls), *clients)
        assert recorder.calls == [("join failed", ErrorCode.GameFull)]
        for client in clients:
            client.disconnect()


# -- proxy -----------------------------------------------------------------------------


def test_proxy_decodes_encrypted_traffic() -> None:
    seen: list[tuple[Direction, int, bool]] = []

    def record(
        session: ProxySession, direction: Direction, packet: PhotonPacket
    ) -> PhotonPacket:
        del session
        if isinstance(packet, PhotonOperationPacket):
            seen.append(
                (
                    direction,
                    int(packet.get_payload().operation_code),
                    packet.get_header().is_encrypted(),
                )
            )
        return packet

    with (
        free_ports() as server,
        PhotonProxy((server.host, server.name_server_port), on_packet=record) as proxy,
    ):
        client, _ = connect_to(proxy.host, proxy.port)
        assert (Direction.ToServer, OperationCode.Authenticate, True) in seen
        assert (Direction.ToClient, OperationCode.Authenticate, True) in seen
        client.disconnect()


def test_proxy_can_drop_and_inject() -> None:
    def deny(
        session: ProxySession, direction: Direction, packet: PhotonPacket
    ) -> PhotonPacket | None:
        if (
            direction == Direction.ToServer
            and isinstance(packet, PhotonOperationPacket)
            and packet.get_payload().operation_code == OperationCode.CreateGame
        ):
            session.send_to_client(_response(packet, ErrorCode.GameIdAlreadyExists))
            return None
        return packet

    with (
        free_ports() as server,
        PhotonProxy((server.host, server.master_server_port), on_packet=deny) as proxy,
    ):
        client, recorder = connect_to(
            server.host,
            server.name_server_port,
            use_name_server=False,
            server=proxy.host,
            port=proxy.port,
        )
        assert client.op_create_room(EnterRoomParams(room_name=ROOM))
        wait(lambda: bool(recorder.calls), client)
        assert recorder.calls == [("create failed", ErrorCode.GameIdAlreadyExists)]
        assert not server.rooms
        client.disconnect()


def _response(request: PhotonOperationPacket, return_code: int) -> PhotonPacket:
    from pyphotonrealtime.protocol.command_code import CommandCode  # noqa: PLC0415
    from pyphotonrealtime.protocol.packet.factory import (  # noqa: PLC0415
        PacketFactory,
    )

    return PacketFactory.operation(
        CommandCode.OperationResponse,
        operation=request.get_payload().operation_code,
        params={},
        return_code=return_code,
        error_message="denied by proxy",
        protocol=request.get_payload().protocol,
    )
