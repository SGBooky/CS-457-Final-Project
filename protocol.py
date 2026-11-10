"""Shared Terminal Trivia framing and message helpers.

Implements newline-delimited JSON envelopes matching docs/protocol_blueprint.md.
"""

from __future__ import annotations

import json
import time
from typing import Any

ALLOWED_MSG_TYPES: frozenset[str] = frozenset(
    {
        "CONNECT",
        "LOBBY_WAIT",
        "GAME_START",
        "QUESTION",
        "MOVE",
        "STATE_UPDATE",
        "ERROR",
        "DISCONNECT",
        "GAME_OVER",
    }
)


VALID_CHOICES: frozenset[str] = frozenset({"A", "B", "C", "D"})
MAX_LINE_BYTES: int = 8 * 1024


def now_ts() -> int:
    """Return the current Unix epoch seconds."""
    return int(time.time())


def make_message(msg_type: str, player_id: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    """Build a protocol envelope with required keys."""
    return {
        "msg_type": msg_type,
        "player_id": player_id,
        "payload": payload if payload is not None else {},
        "timestamp": now_ts(),
    }


def encode_message(message: dict[str, Any]) -> bytes:
    """Serialize one message to a framed UTF-8 line (JSON + newline)."""
    line = json.dumps(message, separators=(",", ":"), ensure_ascii=False)
    return line.encode("utf-8") + b"\n"


def send_message(sock: Any, message: dict[str, Any]) -> None:
    """Send one framed JSON message with sendall()."""
    sock.sendall(encode_message(message))


def recv_messages(sock: Any, buffer: bytearray) -> list[dict[str, Any]]:
    """Append a recv() chunk into buffer and return complete parsed messages.

    Raises ConnectionError when the peer closes cleanly (EOF / empty recv).
    """
    chunk = sock.recv(4096)
    if not chunk:
        raise ConnectionError("EOF")

    buffer.extend(chunk)
    messages: list[dict[str, Any]] = []

    while True:
        newline_at = buffer.find(b"\n")
        if newline_at < 0:
            break
        if newline_at + 1 > MAX_LINE_BYTES:
            raise ValueError("MALFORMED")

        raw_line = bytes(buffer[:newline_at])
        del buffer[: newline_at + 1]

        if not raw_line.strip():
            continue
        if len(raw_line) > MAX_LINE_BYTES:
            raise ValueError("MALFORMED")

        try:
            parsed = json.loads(raw_line.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("MALFORMED") from exc

        if not isinstance(parsed, dict):
            raise ValueError("MALFORMED")
        messages.append(parsed)

    if len(buffer) > MAX_LINE_BYTES:
        raise ValueError("MALFORMED")

    return messages


def validate_envelope(msg: dict[str, Any]) -> tuple[bool, str | None]:
    """Validate common envelope fields. Returns (ok, error_code)."""
    for key in ("msg_type", "player_id", "payload", "timestamp"):
        if key not in msg:
            return False, "MALFORMED"

    msg_type = msg["msg_type"]
    if not isinstance(msg_type, str) or msg_type not in ALLOWED_MSG_TYPES:
        return False, "UNKNOWN_TYPE"
    if not isinstance(msg["player_id"], str) or not msg["player_id"]:
        return False, "MALFORMED"
    if not isinstance(msg["payload"], dict):
        return False, "MALFORMED"
    if not isinstance(msg["timestamp"], int):
        return False, "MALFORMED"
    return True, None
