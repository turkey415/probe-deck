"""Source engine server query (A2S_INFO, A2S_PLAYER): Counter-Strike, Team Fortress 2,
Left 4 Dead 2, Garry's Mod and the rest of the Source and GoldSrc family.

Only builds requests and decodes replies. Sending them is scan.py's job."""
import struct
from dataclasses import dataclass, asdict

HEADER = b"\xff\xff\xff\xff"          # a whole reply in one packet
SPLIT_HEADER = b"\xfe\xff\xff\xff"    # a reply spread over several packets (not handled yet)
INFO_REQUEST = HEADER + b"TSource Engine Query\x00"
PLAYER_REQUEST = HEADER + b"U"
NO_CHALLENGE = b"\xff\xff\xff\xff"   # what A2S_PLAYER sends first, to be given a challenge

SERVER_TYPES = {"d": "dedicated", "l": "listen", "p": "relay"}
ENVIRONMENTS = {"l": "linux", "w": "windows", "m": "mac", "o": "mac"}
THE_SHIP = 2400                       # the one game with extra fields in the middle of the reply


class DecodeError(ValueError):
    """The packet is not a reply this module understands."""


@dataclass
class ServerInfo:
    name: str = ""
    map: str = ""
    folder: str = ""            # game directory, e.g. "csgo" or "cstrike"
    game: str = ""              # the game's own description, e.g. "Counter-Strike 2"
    app_id: int = 0             # Steam application ID, 0 when the server does not say
    players: int = 0
    max_players: int = 0
    bots: int = 0
    server_type: str = ""       # "dedicated", "listen" or "relay"
    environment: str = ""       # "linux", "windows" or "mac"
    password: bool = False
    vac: bool = False
    version: str = ""
    keywords: str = ""
    game_port: int = 0          # 0 when the server does not say; usually the port it answered on
    steam_id: int = 0
    protocol: int = 0
    engine: str = "source"      # "source" or "goldsrc"
    host: str = ""              # filled in by whoever received the reply
    port: int = 0
    ping_ms: float = -1.0       # -1 when not measured

    def as_dict(self):
        return asdict(self)


@dataclass
class Player:
    name: str = ""              # empty for a player still connecting
    score: int = 0              # what the game counts as score: kills in Counter-Strike
    seconds: float = 0.0        # how long they have been on the server

    def as_dict(self):
        return asdict(self)


class _Reader:
    def __init__(self, data, pos=0):
        self.data, self.pos = data, pos

    def left(self):
        return len(self.data) - self.pos

    def _take(self, fmt):
        size = struct.calcsize(fmt)
        if self.left() < size:
            raise DecodeError("reply ends early")
        (value,) = struct.unpack_from(fmt, self.data, self.pos)
        self.pos += size
        return value

    def byte(self):
        return self._take("<B")

    def short(self):
        return self._take("<H")

    def long(self):
        return self._take("<I")

    def longlong(self):
        return self._take("<Q")

    def signed(self):
        return self._take("<i")

    def float(self):
        return self._take("<f")

    def char(self):
        return chr(self.byte())

    def string(self):
        end = self.data.find(b"\x00", self.pos)
        if end < 0:
            raise DecodeError("string has no end")
        text = self.data[self.pos:end].decode("utf-8", "replace")
        self.pos = end + 1
        return text


def info_request(challenge=b""):
    """The A2S_INFO request. Servers that answer with a challenge want it sent back on the end."""
    return INFO_REQUEST + challenge


def challenge_from(data):
    """The 4-byte challenge if this reply is one, else None."""
    if len(data) >= 9 and data[:4] == HEADER and data[4:5] == b"A":
        return data[5:9]
    return None


def player_request(challenge=NO_CHALLENGE):
    """The A2S_PLAYER request. Every server answers the first one with a challenge to send back."""
    return PLAYER_REQUEST + challenge


def decode_players(data):
    """Decode an A2S_PLAYER reply into a list of Player. Raises DecodeError for anything else."""
    if data[:4] == SPLIT_HEADER:
        raise DecodeError("split reply")
    if len(data) < 6 or data[:4] != HEADER or data[4:5] != b"D":
        raise DecodeError("not a player reply")
    r = _Reader(data, 5)
    count = r.byte()
    players = []
    try:
        for _ in range(count):
            r.byte()                            # index: always 0 on many servers, so not kept
            players.append(Player(r.string(), r.signed(), round(r.float(), 1)))
    except DecodeError:
        pass                                    # a cut-off list: keep the players read so far
    return players


def decode_info(data):
    """Decode an A2S_INFO reply into a ServerInfo. Raises DecodeError for anything else."""
    if data[:4] == SPLIT_HEADER:
        raise DecodeError("split reply")
    if len(data) < 6 or data[:4] != HEADER:
        raise DecodeError("not a Source query reply")
    kind = data[4:5]
    r = _Reader(data, 5)
    if kind == b"I":
        return _decode_source(r)
    if kind == b"m":
        return _decode_goldsrc(r)
    raise DecodeError("not an info reply")


def _decode_source(r):
    s = ServerInfo()
    s.protocol = r.byte()
    s.name, s.map, s.folder, s.game = r.string(), r.string(), r.string(), r.string()
    s.app_id = r.short()
    s.players, s.max_players, s.bots = r.byte(), r.byte(), r.byte()
    s.server_type = SERVER_TYPES.get(r.char().lower(), "")
    s.environment = ENVIRONMENTS.get(r.char().lower(), "")
    s.password = r.byte() == 1
    s.vac = r.byte() == 1
    if s.app_id == THE_SHIP:
        r.byte(), r.byte(), r.byte()            # mode, witnesses, duration
    s.version = r.string()
    try:                                        # everything from here on is optional
        if r.left():
            flags = r.byte()
            if flags & 0x80:
                s.game_port = r.short()
            if flags & 0x10:
                s.steam_id = r.longlong()
            if flags & 0x40:
                r.short(), r.string()           # spectator port and name
            if flags & 0x20:
                s.keywords = r.string()
            if flags & 0x01:
                game_id = r.longlong()
                s.app_id = (game_id & 0xFFFFFF) or s.app_id   # the short above cannot hold big IDs
    except DecodeError:
        pass
    return s


def _decode_goldsrc(r):
    """The older reply some GoldSrc servers (Counter-Strike 1.6, Half-Life) still send."""
    s = ServerInfo(engine="goldsrc")
    address = r.string()
    s.name, s.map, s.folder, s.game = r.string(), r.string(), r.string(), r.string()
    s.players, s.max_players = r.byte(), r.byte()
    s.protocol = r.byte()
    s.server_type = SERVER_TYPES.get(r.char().lower(), "")
    s.environment = ENVIRONMENTS.get(r.char().lower(), "")
    s.password = r.byte() == 1
    try:
        if r.byte() == 1:                       # running a mod: skip its details
            r.string(), r.string(), r.byte(), r.long(), r.long(), r.byte(), r.byte()
        s.vac = r.byte() == 1
        s.bots = r.byte()
    except DecodeError:
        pass
    port = address.rpartition(":")[2]
    if port.isdigit():
        s.game_port = int(port)
    return s
