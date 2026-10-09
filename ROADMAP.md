# Roadmap

Goal: be able to do from Python everything a Photon Realtime game does, so a
game client (or a bot, test harness or proxy) can be written against it. Once it's stable,
`prison-architect-server` replaces its inline `server/photon` with this package.

## Architecture

The layers follow the official SDKs, so their docs stay usable:

```
RealtimeClient    (realtime/)   state machine, NS -> MS -> GS hops, rooms, callbacks
    |
PhotonPeer        (peer.py)     service loop, op/event queues, keep-alive, timeouts, crypto
    |
Transport         (transport/)  TCP | UDP (ENet-style reliability) | WebSocket(s)
    |
protocol/                       Protocol16/18 types, framing, DH key exchange, AES
```

Threading model: like the SDKs, the client is single-threaded and driven by `service()`.
Every callback runs inside `service()`. An `asyncio` wrapper comes later as a
separate layer and doesn't change the core.

## Milestones

### M0: Fork and scaffold (done)
- [x] Extract `server/photon` (history preserved) into `pyphotonrealtime.protocol`
- [x] Decouple from server-only code (`GamesManager`, logging, settings, queue)
- [x] Skeletons: `Transport`, `PhotonPeer`, `RealtimeClient`, callbacks, options, Room/Player
- [x] Packaging (`pyproject.toml`, src layout, `py.typed`), Python 3.12+
- [x] CI: ruff `ALL` (pinned), `mypy --strict`, pytest on 3.12-3.14, nightly e2e job

### M1: PhotonPeer over TCP (done)
- [x] `connect`: send `InitRequest`, handle `InitResponse`, status callbacks
- [x] `send_operation` / `dispatch_incoming_commands` into `OperationResponse` / `EventData`
- [x] Keep-alive (client-initiated pings) and disconnect timeout
- [x] `establish_encryption` (DH via `photon_enc`), encrypted ops (AES-CBC)
- [x] Traffic stats and round-trip time
- [x] Tests against a fake in-process server (reuse PA server's server-side code as a fixture)
- [x] First e2e test passing: TCP connect + Init to `ns.photonengine.io`
- [x] Buffer partial TCP writes (`TcpTransport.send` currently blocks)

### M2: RealtimeClient connection workflow
- [x] Name Server: `OpGetRegions`, `OpAuthenticate` / `AuthOnce`, region pinging (TCP connect time)
- [x] Master Server hop with token; direct-master mode (`use_name_server=False`)
- [x] Disconnect causes, `reconnect_to_master`
- [x] Custom authentication (`AuthenticationValues`; post data as `str` or `bytes`)
- [ ] e2e: Name Server -> Master Server against Photon Cloud (written; first run is on master CI)

### M3: Matchmaking and lobbies
- [ ] Join/leave lobby, room list and `GameListUpdate` events, lobby stats
- [ ] Create / Join / JoinOrCreate / JoinRandom / Rejoin, then the Game Server hop
- [ ] `reconnect_and_rejoin` (needs the Game Server hop)
- [ ] `OpFindFriends`, expected users, SQL lobby filters

### M4: In-room
- [ ] Join/Leave/PropertiesChanged events, then update `Room`/`Player` and fire callbacks
- [ ] `op_raise_event` (receivers, target actors, caching, interest groups)
- [ ] Set room/actor properties, with expected-properties CAS
- [ ] Master client switching; `op_custom` escape hatch
- [ ] e2e: two clients in one room exchange events and properties

### M5: Protocol completeness
- [ ] Protocol 1.8 (`SerializationProtocol.V18`), the default in v5+ SDKs; audit what's implemented today
- [ ] Custom types registry (`PhotonPeer.RegisterType` equivalent) as a public API
- [ ] UDP transport with reliable/unreliable channels and fragmentation
- [ ] WebSocket(s) transport

### M6: Ergonomics and release
- [ ] `asyncio` facade (`AsyncRealtimeClient`: awaitable connect/join, async event iterator)
- [ ] Plain Python values in the public API (`dict`/`int`/`str`) instead of `*Parameter` wrappers;
      automatic conversion at the peer boundary
- [ ] Docs site, examples (bot, room browser, packet sniffer)
- [ ] Publish `pyPhotonRealtime` to PyPI (trusted publishing on tag)
- [ ] Migrate prison-architect-server onto it

## Clean-up inherited from the fork
- `protocol/event_code.py` has `PrisonArchitectEventCode`. It's game-specific and should move back
  to the PA repo before 1.0.
- `OperationCode` mixes client ops with internal aliases (`Authenticate`/`GameList` share 230);
  split into `OperationCode` / `EventCode` / `ParameterCode` the way the SDK does.
- `protocol/` is exempt from the missing-docstring rules (D100-D107) in `ruff.toml`. Document it
  and drop the exemption. Every other rule, and `mypy --strict`, already applies to it.
- `PacketFactory.event` casts a `PhotonOperationPacket` to `PhotonEventPacket`. Construct the real type.

## Decision: dataclasses vs. pydantic v2

User-facing config (`AppSettings`, `RoomOptions`, `EnterRoomParams`, ...) uses stdlib
`dataclasses` for now.

| | pydantic v2 | dataclasses |
|---|---|---|
| Validation (types, ranges such as `max_players` 0-255) | built in | manual `__post_init__` |
| Load settings from env / JSON / `.env` (`pydantic-settings`) | yes | manual |
| JSON schema / serialization of config | yes | manual |
| Dependency weight | `pydantic-core` Rust wheel (~2 MB); no wheels on some niche platforms/PyPy versions | none |
| Hot-path cost (per event/packet) | model construction is slower | negligible |
| Version conflicts for users embedding the lib | possible (v1/v2 pin clashes) | none |

Conclusion: the wire and peer layers (packets, `EventData`, `OperationResponse`) never use pydantic,
because they're per-packet hot paths. For config, the validation benefit is real but small,
and a hard dependency costs every user. If validation gets painful, add an optional
`pyPhotonRealtime[pydantic]` extra with validated config models, or switch only `AppSettings`.
