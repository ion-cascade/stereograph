"""Host and Blender share this bounded, versioned JSON transport."""

import json
import math
import struct

VERSION = 1
HOST = "127.0.0.1"
DEFAULT_PORT = 9877
MAX_MESSAGE = 1_048_576
MAX_CODE = 65_536
MAX_TIMEOUT = 120.0
DEFORMATION_TIMEOUT = 600.0
HEADER = struct.Struct("!I")


class InvalidMessage(ValueError):
    """Raised when a peer sends an invalid frame or envelope."""


def encode(value):
    try:
        return json.dumps(value, allow_nan=False, ensure_ascii=True).encode("ascii")
    except (TypeError, ValueError, OverflowError, RecursionError) as exc:
        raise InvalidMessage("Value is not finite, JSON-compatible data") from exc


def decode(payload):
    def reject_constant(value):
        raise InvalidMessage(f"Non-finite JSON constant: {value}")

    try:
        value = json.loads(payload, parse_constant=reject_constant)
    except (UnicodeError, ValueError, RecursionError) as exc:
        raise InvalidMessage("Invalid JSON payload") from exc
    if not isinstance(value, dict):
        raise InvalidMessage("Message must be a JSON object")
    return value


def _read(sock, amount):
    data = bytearray()
    while len(data) < amount:
        part = sock.recv(amount - len(data))
        if not part:
            raise EOFError("Connection closed before the message was complete")
        data.extend(part)
    return bytes(data)


def receive(sock):
    (size,) = HEADER.unpack(_read(sock, HEADER.size))
    if not 0 < size <= MAX_MESSAGE:
        raise InvalidMessage("Message size is outside the protocol limit")
    return decode(_read(sock, size))


def send(sock, value):
    payload = encode(value)
    if len(payload) > MAX_MESSAGE:
        raise InvalidMessage("Encoded message exceeds the protocol limit")
    sock.sendall(HEADER.pack(len(payload)) + payload)


def port_number(value):
    port = int(value)
    if not 1 <= port <= 65535:
        raise ValueError("STEREOGRAPH_PORT must be between 1 and 65535")
    return port


def timeout_limit(operation=None):
    return DEFORMATION_TIMEOUT if operation in ("knee_preview", "knee_apply", "fit_inspect", "cloth_preview", "cloth_apply") else MAX_TIMEOUT


def valid_timeout(value, operation=None):
    return (type(value) in (float, int) and 0.05 <= value <= timeout_limit(operation)
            and math.isfinite(value))


def success(data):
    return {"ok": True, "data": data}


def failure(code, message, execution="not_started"):
    return {"ok": False, "error": {"code": code, "message": message, "execution": execution}}
