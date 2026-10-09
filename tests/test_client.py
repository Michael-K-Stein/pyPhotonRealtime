"""``RealtimeClient`` connection workflow against a fake Name/Master Server pair."""

from __future__ import annotations

import socket
import time
from typing import TYPE_CHECKING, Any, cast, override

import pytest

from pyphotonrealtime.protocol.command_code import CommandCode
from pyphotonrealtime.protocol.operation_code import OperationCode
from pyphotonrealtime.protocol.packet.factory import PacketFactory
from pyphotonrealtime.protocol.packet.init import InitRequestPacket, InitResponsePacket
from pyphotonrealtime.protocol.packet.key_exchange import (
    InitEncryptionRequest,
    InitEncryptionResponse,
)
from pyphotonrealtime.protocol.packet.operation_packet import PhotonOperationPacket
from pyphotonrealtime.protocol.packet.packet_stream import PhotonStreamParser
from pyphotonrealtime.protocol.param.dict_param import DictionaryParameter
from pyphotonrealtime.protocol.param.int8_param import Int8Parameter
from pyphotonrealtime.protocol.param.int8_slice_param import Int8SliceParameter
from pyphotonrealtime.protocol.param.parameter_key import ParameterKey
from pyphotonrealtime.protocol.param.slice_param import SliceParameter
from pyphotonrealtime.protocol.param.string_param import StringParameter
from pyphotonrealtime.protocol.photon_enc import generate_dh_keys
from pyphotonrealtime.realtime import (
    AppSettings,
    AuthenticationValues,
    AuthMode,
    ClientState,
    ConnectionCallbacks,
    CustomAuthenticationType,
    DisconnectCause,
    ErrorCode,
    RealtimeClient,
    Region,
    RegionPinger,
    ServerType,
)
from pyphotonrealtime.transport import Transport

if TYPE_CHECKING:
    from collections.abc import Iterator

    from pyphotonrealtime.protocol.enum_lookups import CommandParams
    from pyphotonrealtime.protocol.packet.base import PhotonPacket

APP_ID = "6f869876-bfbc-491e-8fff-4210c966f145"
NAME_SERVER = "ns.example:4533"
MASTER_SERVER = "ms.example:4530"
SECRET = bytes(range(32))


class FakeServer:
    """A Photon server answering Init, key exchange and a few operations."""

    def __init__(self, address: str) -> None:
        self.address = address
        self.parser = PhotonStreamParser()
        self.aes_key: bytes | None = None
        self.operations: list[tuple[int, CommandParams, bool]] = []
        self.auth_return_code = 0
        self.regions: dict[str, str] = {}

    def reset(self) -> None:
        self.parser = PhotonStreamParser()
        self.aes_key = None

    def handle(self, data: bytes) -> bytes:
        self.parser.feed(data)
        out = b""
        for packet in self.parser.parse(aes_key=self.aes_key):
            for reply in self._replies(packet):
                out += reply.serialize()
        return out

    def _replies(self, packet: PhotonPacket) -> list[PhotonPacket]:
        if isinstance(packet, InitRequestPacket):
            return [InitResponsePacket()]
        if isinstance(packet, InitEncryptionRequest):
            server_public, self.aes_key = generate_dh_keys(packet.get_public_key())
            return [InitEncryptionResponse(public_key=server_public)]
        if isinstance(packet, PhotonOperationPacket):
            payload = packet.get_payload()
            encrypted = packet.get_header().is_encrypted()
            self.operations.append(
                (int(payload.operation_code), dict(payload.params), encrypted)
            )
            return_code, params = self.answer(int(payload.operation_code))
            response = PacketFactory.operation(
                CommandCode.EncryptedOperationResponse
                if encrypted
                else CommandCode.OperationResponse,
                operation=payload.operation_code,
                params=params,
                return_code=return_code,
                error_message="nope" if return_code else None,
            )
            if self.aes_key is not None:
                response.set_aes_key(self.aes_key)
            return [response]
        return []

    def answer(self, operation: int) -> tuple[int, CommandParams]:
        if operation == OperationCode.GetRegions:
            return 0, {
                ParameterKey.Region: SliceParameter(
                    [StringParameter(code) for code in self.regions]
                ),
                ParameterKey.Address: SliceParameter(
                    [StringParameter(addr) for addr in self.regions.values()]
                ),
            }
        if self.auth_return_code:
            return self.auth_return_code, {}
        params: CommandParams = {
            ParameterKey.Token: StringParameter("token-1"),
            ParameterKey.UserId: StringParameter("user-1"),
        }
        if self.address == NAME_SERVER:
            params[ParameterKey.Address] = StringParameter(MASTER_SERVER)
            params[ParameterKey.Cluster] = StringParameter("default")
        if operation == OperationCode.AuthOnce:
            params[ParameterKey.EncryptionData] = DictionaryParameter(
                cast(
                    "Any",
                    {
                        Int8Parameter(0): Int8SliceParameter(b"\x00"),
                        Int8Parameter(1): Int8SliceParameter(SECRET),
                    },
                )
            )
        return 0, params


class FakeCloud(Transport):
    """Routes the client's connection to the fake server at that address."""

    def __init__(self, *servers: FakeServer) -> None:
        self.servers = {server.address: server for server in servers}
        self.current: FakeServer | None = None
        self.inbox = b""
        self.connections: list[str] = []

    @override
    def connect(self, host: str, port: int, timeout: float) -> None:
        address = f"{host}:{port}"
        if address not in self.servers:
            raise ConnectionRefusedError
        self.connections.append(address)
        self.current = self.servers[address]
        self.current.reset()
        self.inbox = b""

    @override
    def send(self, data: bytes) -> None:
        assert self.current is not None
        self.inbox += self.current.handle(data)

    @override
    def receive(self) -> bytes:
        data, self.inbox = self.inbox, b""
        return data

    @override
    def close(self) -> None:
        self.current = None

    @property
    @override
    def connected(self) -> bool:
        return self.current is not None


class Events(ConnectionCallbacks):
    def __init__(self) -> None:
        self.calls: list[tuple[str, Any]] = []

    @override
    def on_connected(self) -> None:
        self.calls.append(("connected", None))

    @override
    def on_connected_to_master(self) -> None:
        self.calls.append(("master", None))

    @override
    def on_disconnected(self, cause: DisconnectCause) -> None:
        self.calls.append(("disconnected", cause))

    @override
    def on_region_list_received(self, regions: dict[str, str]) -> None:
        self.calls.append(("regions", regions))

    @override
    def on_custom_authentication_failed(self, debug_message: str) -> None:
        self.calls.append(("auth_failed", debug_message))


@pytest.fixture
def name_server() -> FakeServer:
    return FakeServer(NAME_SERVER)


@pytest.fixture
def master_server() -> FakeServer:
    return FakeServer(MASTER_SERVER)


@pytest.fixture
def cloud(name_server: FakeServer, master_server: FakeServer) -> FakeCloud:
    return FakeCloud(name_server, master_server)


@pytest.fixture
def events() -> Events:
    return Events()


@pytest.fixture
def client(cloud: FakeCloud, events: Events) -> RealtimeClient:
    client = RealtimeClient(transport=cloud)
    client.add_callback_target(events)
    return client


def settings(**kwargs: Any) -> AppSettings:  # noqa: ANN401
    return AppSettings(
        app_id_realtime=APP_ID,
        **{"name_server": "ns.example", "fixed_region": "eu", **kwargs},
    )


def run(client: RealtimeClient, until: ClientState, steps: int = 50) -> None:
    for _ in range(steps):
        if client.state == until:
            return
        client.service()
    pytest.fail(f"stuck in {client.state}, waiting for {until}")


def test_name_server_to_master(
    client: RealtimeClient,
    cloud: FakeCloud,
    name_server: FakeServer,
    master_server: FakeServer,
    events: Events,
) -> None:
    assert client.connect_using_settings(settings(app_version="1.0"))
    assert client.state == ClientState.ConnectingToNameServer
    run(client, ClientState.ConnectedToMasterServer)

    assert cloud.connections == [NAME_SERVER, MASTER_SERVER]
    assert client.server == ServerType.MasterServer
    assert client.is_connected_and_ready
    assert client.user_id == "user-1"
    assert client.current_cluster == "default"
    assert client.master_server_address == MASTER_SERVER
    assert events.calls == [("connected", None), ("master", None)]

    ((code, params, encrypted),) = name_server.operations
    assert (code, encrypted) == (OperationCode.Authenticate, True)
    assert params[ParameterKey.ApplicationId] == StringParameter(APP_ID)
    assert params[ParameterKey.AppVersion] == StringParameter("1.0")
    assert params[ParameterKey.Region] == StringParameter("eu")

    # The Master Server only sees the token from the Name Server.
    ((code, params, _),) = master_server.operations
    assert code == OperationCode.Authenticate
    assert params == {ParameterKey.Token: StringParameter("token-1")}

    client.disconnect()
    run(client, ClientState.Disconnected)
    assert events.calls[-1] == ("disconnected", DisconnectCause.DisconnectByClientLogic)


def test_custom_authentication_values_are_sent(
    client: RealtimeClient, name_server: FakeServer
) -> None:
    auth = AuthenticationValues(
        user_id="alice",
        auth_type=CustomAuthenticationType.Steam,
        auth_post_data=b"ticket",
    )
    auth.add_auth_parameter("user", "a b")
    auth.add_auth_parameter("x", "1")
    client.auth_values = auth
    client.connect_using_settings(settings(enable_lobby_statistics=True))
    run(client, ClientState.ConnectedToMasterServer)

    params = name_server.operations[0][1]
    assert params[ParameterKey.UserId] == StringParameter("alice")
    assert params[ParameterKey.ClientAuthenticationType] == Int8Parameter(1)
    assert params[ParameterKey.ClientAuthenticationParams] == StringParameter(
        "user=a%20b&x=1"
    )
    assert params[ParameterKey.ClientAuthenticationData] == Int8SliceParameter(
        b"ticket"
    )
    assert ParameterKey.LobbyStats in params
    assert auth.token == "token-1"  # noqa: S105


@pytest.mark.parametrize(
    ("return_code", "cause"),
    [
        (ErrorCode.InvalidAuthentication, DisconnectCause.InvalidAuthentication),
        (ErrorCode.MaxCcuReached, DisconnectCause.MaxCcuReached),
        (ErrorCode.InvalidRegion, DisconnectCause.InvalidRegion),
        (
            ErrorCode.CustomAuthenticationFailed,
            DisconnectCause.CustomAuthenticationFailed,
        ),
    ],
)
def test_authentication_failure_disconnects_with_cause(
    client: RealtimeClient,
    name_server: FakeServer,
    events: Events,
    return_code: int,
    cause: DisconnectCause,
) -> None:
    name_server.auth_return_code = return_code
    client.connect_using_settings(settings())
    run(client, ClientState.Disconnected)
    assert client.disconnect_cause == cause
    assert events.calls[-1] == ("disconnected", cause)
    if cause == DisconnectCause.CustomAuthenticationFailed:
        assert ("auth_failed", "nope") in events.calls


def test_connection_refused(client: RealtimeClient, events: Events) -> None:
    client.connect_using_settings(settings(name_server="nowhere.example"))
    run(client, ClientState.Disconnected)
    assert events.calls == [("disconnected", DisconnectCause.ExceptionOnConnect)]


def test_direct_master_mode(
    client: RealtimeClient, cloud: FakeCloud, master_server: FakeServer
) -> None:
    client.connect_using_settings(
        settings(use_name_server=False, server="ms.example", fixed_region=None)
    )
    assert client.state == ClientState.ConnectingToMasterServer
    run(client, ClientState.ConnectedToMasterServer)
    assert cloud.connections == [MASTER_SERVER]
    ((_, params, encrypted),) = master_server.operations
    assert encrypted
    assert params[ParameterKey.ApplicationId] == StringParameter(APP_ID)
    assert ParameterKey.Region not in params


def test_direct_master_mode_needs_server(client: RealtimeClient) -> None:
    with pytest.raises(ValueError, match="server"):
        client.connect_using_settings(settings(use_name_server=False))


def test_auth_once_skips_key_exchange_on_master(
    client: RealtimeClient, name_server: FakeServer, master_server: FakeServer
) -> None:
    client.connect_using_settings(settings(auth_mode=AuthMode.AuthOnce))
    run(client, ClientState.ConnectedToMasterServer)

    ((code, params, _),) = name_server.operations
    assert code == OperationCode.AuthOnce
    assert params[ParameterKey.ExpectedProtocol] == Int8Parameter(1)
    assert master_server.aes_key is None  # No Diffie-Hellman on the Master Server.
    assert client.peer._aes_key == SECRET
    ((_, params, encrypted),) = master_server.operations
    assert params == {ParameterKey.Token: StringParameter("token-1")}
    assert not encrypted


def test_reconnect_to_master_reuses_token(
    client: RealtimeClient, cloud: FakeCloud, master_server: FakeServer
) -> None:
    assert not client.reconnect_to_master()  # No session yet.
    client.connect_using_settings(settings())
    run(client, ClientState.ConnectedToMasterServer)
    client.peer.transport.close()  # Connection drops.
    run(client, ClientState.Disconnected)
    cause = client.disconnect_cause
    assert cause == DisconnectCause.DisconnectByServerReasonUnknown

    assert client.reconnect_to_master()
    run(client, ClientState.ConnectedToMasterServer)
    assert cloud.connections == [NAME_SERVER, MASTER_SERVER, MASTER_SERVER]
    assert master_server.operations[-1][1] == {
        ParameterKey.Token: StringParameter("token-1")
    }
    assert client.disconnect_cause == DisconnectCause.NoCause


@pytest.fixture
def listening_address() -> Iterator[str]:
    with socket.create_server(("127.0.0.1", 0)) as server:
        yield f"127.0.0.1:{server.getsockname()[1]}"


@pytest.fixture
def closed_address() -> str:
    with socket.create_server(("127.0.0.1", 0)) as server:
        port = server.getsockname()[1]
    return f"127.0.0.1:{port}"


def test_best_region_is_picked_by_ping(
    client: RealtimeClient,
    name_server: FakeServer,
    events: Events,
    listening_address: str,
) -> None:
    # Unparseable, so it fails at once (a refused connect takes ~2 s on Windows).
    name_server.regions = {"us/c1": "no-port", "eu": listening_address}
    client.connect_using_settings(settings(fixed_region=None))
    for _ in range(500):
        if client.state == ClientState.ConnectedToMasterServer:
            break
        client.service()
    assert client.state == ClientState.ConnectedToMasterServer
    assert client.cloud_region == "eu"
    assert ("regions", {"us": "no-port", "eu": listening_address}) in (events.calls)
    auth = name_server.operations[-1]
    assert auth[0] == OperationCode.Authenticate
    assert auth[1][ParameterKey.Region] == StringParameter("eu")
    us = client.regions[0]
    assert (us.code, us.cluster, us.ping) == ("us", "c1", None)


def test_region_pinger_gives_up_on_unreachable(closed_address: str) -> None:
    pinger = RegionPinger([Region.from_name_server("EU", closed_address)], timeout=0.05)
    deadline = time.monotonic() + 5
    while not pinger.poll():
        assert time.monotonic() < deadline
        time.sleep(0.01)
    assert pinger.best_region is None


def test_connect_to_region_master(client: RealtimeClient) -> None:
    assert not client.connect_to_region_master("us")  # No settings yet.
    client.connect_using_settings(settings())
    run(client, ClientState.ConnectedToMasterServer)
    client.disconnect()
    run(client, ClientState.Disconnected)
    assert client.connect_to_region_master("us")
    run(client, ClientState.ConnectedToMasterServer)
    assert client.cloud_region == "us"
