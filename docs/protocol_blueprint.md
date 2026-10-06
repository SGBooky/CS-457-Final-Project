# Application Protocol Blueprint — Terminal Trivia

**Student:** Ben Schlicting  
**Course:** CS 457 – Computer Networks  
**Game:** Terminal Trivia (2-player CLI quiz)  
**Transport:** TCP  
**Serialization:** Structured JSON (UTF-8)  
**Framing:** Newline-delimited JSON (`\n` / `0x0A`)

---

## 1. Design Goals

This protocol defines every application-layer message exchanged between Terminal Trivia clients and the authoritative game server. The server owns all game state (questions, scores, round timers, win/draw/forfeit outcomes). Clients only:

1. Join with a display name (`CONNECT`)
2. Submit answer choices (`MOVE`)
3. Quit intentionally (`DISCONNECT`)
4. Render server-pushed updates (`LOBBY_WAIT`, `GAME_START`, `QUESTION`, `STATE_UPDATE`, `ERROR`, `GAME_OVER`)

---

## 2. Transport Layer & Packet Framing

### 2.1 Transport Choice

- **Protocol:** TCP (reliable, ordered byte stream)
- **Encoding:** UTF-8
- **Why TCP:** Message loss or reordering would desynchronize scores and round progression across CML client nodes.

### 2.2 Framing Rule (Newline-Delimited JSON)

TCP has no message boundaries. A single `recv()` may return:

- **Coalescing:** multiple complete messages in one chunk
- **Fragmentation:** a partial message split across chunks

**Deterministic framing rule:**

1. Each message is one JSON object with **no embedded newline characters**.
2. The sender appends exactly one newline terminator: `\n` (`0x0A`).
3. The receiver maintains a per-socket byte buffer.
4. On every `recv()`, append bytes to the buffer and scan for `\n`.
5. For each complete line (bytes up to and including `\n`):
   - Strip the terminator
   - `json.loads()` the remaining UTF-8 text
   - Dispatch by `msg_type`
6. Keep any trailing incomplete bytes in the buffer until the next `recv()`.

Compact JSON (`separators=(",", ":")`) is preferred so payloads never contain literal newlines.

### 2.3 Receiver Extraction Logic (Conceptual)

```python
# Per-connection receive buffer (bytes)
buffer = b""

while True:
    chunk = sock.recv(4096)
    if not chunk:
        # TCP EOF: peer closed write-half (see Section 5)
        handle_client_disconnect(player_id)
        break

    buffer += chunk
    while b"\n" in buffer:
        line, buffer = buffer.split(b"\n", 1)
        if not line.strip():
            continue
        message = json.loads(line.decode("utf-8"))
        dispatch(message)
```

### 2.4 Raw On-the-Wire Stream Examples

**Back-to-back coalesced messages (two JSON objects in one TCP segment):**

```text
{"msg_type":"CONNECT","player_id":"Alice","payload":{"display_name":"Alice"},"timestamp":1727000000}\n{"msg_type":"MOVE","player_id":"Alice","payload":{"choice":"B","round":1},"timestamp":1727000005}\n
```

**Fragmentation across two `recv()` calls:**

```text
recv #1: {"msg_type":"MOVE","player_id":"Alice","payload":{"choi
recv #2: ce":"B","round":1},"timestamp":1727000005}\n
```

The buffer holds the partial object until `\n` arrives, then parses exactly one message.

**Server broadcasting round results then next question:**

```text
{"msg_type":"STATE_UPDATE","player_id":"SERVER","payload":{"round":1,"scores":{"Player_1":1,"Player_2":0},"status":"round_complete"},"timestamp":1727000015}\n{"msg_type":"QUESTION","player_id":"SERVER","payload":{"round":2,"prompt":"Which OSI layer handles routing?","options":{"A":"Physical","B":"Network","C":"Session","D":"Application"},"time_limit_sec":15},"timestamp":1727000016}\n
```

---

## 3. Common Envelope Schema

Every message uses this envelope:

| Field | Type | Required | Description |
|---|---|---|---|
| `msg_type` | string | yes | One of the message type enums below |
| `player_id` | string | yes | Client alias (`"Alice"`) or role (`"Player_1"` / `"Player_2"`). Server-originated messages use `"SERVER"`. |
| `payload` | object | yes | Type-specific fields (may be `{}`) |
| `timestamp` | integer | yes | Unix epoch seconds (sender local clock; informational only) |

Generic envelope:

```json
{
  "msg_type": "<TYPE>",
  "player_id": "<ID>",
  "payload": {},
  "timestamp": 1727000000
}
```

---

## 4. Application Message Types

### 4.1 Message Catalog

| Message Type | Direction | Purpose |
|---|---|---|
| `CONNECT` | Client → Server | Join the game room with a display name |
| `LOBBY_WAIT` | Server → Client | Notify first client that Player 2 has not connected yet |
| `GAME_START` | Server → Clients | Game begins; assign `Player_1` / `Player_2` roles and total rounds |
| `QUESTION` | Server → Clients | Broadcast current round question, options, and answer window |
| `MOVE` | Client → Server | Submit multiple-choice answer for the active round |
| `STATE_UPDATE` | Server → Clients | Broadcast scores, round status, and waiting/next-round info |
| `ERROR` | Server → Client | Reject invalid / late / out-of-window / malformed messages |
| `DISCONNECT` | Client → Server | Intentional quit / forfeit notification |
| `GAME_OVER` | Server → Clients | Final outcome (Winner / Draw / Forfeit) and final scores |

---

### 4.2 `CONNECT` — Client → Server

**Purpose:** Request admission to the lobby with a display name.

**Payload fields:**

| Key | Type | Constraints |
|---|---|---|
| `display_name` | string | 1–24 alphanumeric / underscore / hyphen characters |

**Sample:**

```json
{
  "msg_type": "CONNECT",
  "player_id": "Alice",
  "payload": {
    "display_name": "Alice"
  },
  "timestamp": 1727000000
}
```

**Server behavior:**
- If room has 0 players → accept as `Player_1`, reply with `LOBBY_WAIT`
- If room has 1 player → accept as `Player_2`, transition toward `GAME_START`
- If room is full → reply `ERROR` (`ROOM_FULL`) and close socket

---

### 4.3 `LOBBY_WAIT` — Server → Client

**Purpose:** Tell the waiting client that the match is not ready.

**Payload fields:**

| Key | Type | Description |
|---|---|---|
| `role` | string | Assigned role, typically `"Player_1"` |
| `players_connected` | integer | Current count (1) |
| `players_needed` | integer | Required capacity (2) |
| `message` | string | Human-readable lobby status |

**Sample:**

```json
{
  "msg_type": "LOBBY_WAIT",
  "player_id": "SERVER",
  "payload": {
    "role": "Player_1",
    "players_connected": 1,
    "players_needed": 2,
    "message": "Waiting for Player 2 to connect."
  },
  "timestamp": 1727000001
}
```

---

### 4.4 `GAME_START` — Server → Clients

**Purpose:** Announce match start and role assignment.

**Payload fields:**

| Key | Type | Description |
|---|---|---|
| `role` | string | Recipient's role: `"Player_1"` or `"Player_2"` |
| `opponent` | string | Opponent display name |
| `total_rounds` | integer | Fixed match length (10) |
| `time_limit_sec` | integer | Per-round answer window (15) |

**Sample (to Player 1):**

```json
{
  "msg_type": "GAME_START",
  "player_id": "SERVER",
  "payload": {
    "role": "Player_1",
    "opponent": "Bob",
    "total_rounds": 10,
    "time_limit_sec": 15
  },
  "timestamp": 1727000010
}
```

---

### 4.5 `QUESTION` — Server → Clients

**Purpose:** Deliver the current round's trivia prompt to both clients simultaneously.

**Payload fields:**

| Key | Type | Description |
|---|---|---|
| `round` | integer | 1-based round index |
| `prompt` | string | Question text |
| `options` | object | Keys `"A"`, `"B"`, `"C"`, `"D"` → option text |
| `time_limit_sec` | integer | Seconds remaining in answer window |

**Sample:**

```json
{
  "msg_type": "QUESTION",
  "player_id": "SERVER",
  "payload": {
    "round": 1,
    "prompt": "Which protocol provides reliable ordered byte streams?",
    "options": {
      "A": "UDP",
      "B": "TCP",
      "C": "ICMP",
      "D": "ARP"
    },
    "time_limit_sec": 15
  },
  "timestamp": 1727000011
}
```

> Correct answers are **never** sent to clients before scoring.

---

### 4.6 `MOVE` — Client → Server

**Purpose:** Submit an answer selection for the active round. In Terminal Trivia both players may answer during the same `PLAYER_TURN` window (simultaneous play).

**Payload fields:**

| Key | Type | Constraints |
|---|---|---|
| `choice` | string | Exactly one of `"A"`, `"B"`, `"C"`, `"D"` |
| `round` | integer | Must match the server's current round index |

**Sample:**

```json
{
  "msg_type": "MOVE",
  "player_id": "Player_1",
  "payload": {
    "choice": "B",
    "round": 1
  },
  "timestamp": 1727000018
}
```

**Validation rules (server):**
- Reject if FSM is not in `PLAYER_TURN` → `ERROR` (`OUT_OF_TURN`)
- Reject if player already submitted for this round → `ERROR` (`ALREADY_ANSWERED`)
- Reject if `choice` ∉ {A,B,C,D} → `ERROR` (`INVALID_MOVE`)
- Reject if `round` ≠ current round → `ERROR` (`STALE_ROUND`)
- Reject malformed JSON / missing fields → `ERROR` (`MALFORMED`)

---

### 4.7 `STATE_UPDATE` — Server → Clients

**Purpose:** Keep both clients synchronized after scoring or waiting-state changes.

**Payload fields:**

| Key | Type | Description |
|---|---|---|
| `round` | integer | Round just scored or currently active |
| `scores` | object | `{ "Player_1": <int>, "Player_2": <int> }` |
| `status` | string | `"waiting_for_answers"` \| `"round_complete"` \| `"awaiting_next_question"` |
| `last_round_result` | object \| null | Per-player correctness for the completed round |
| `answered` | object | `{ "Player_1": bool, "Player_2": bool }` submission flags |

**Sample (after round scoring):**

```json
{
  "msg_type": "STATE_UPDATE",
  "player_id": "SERVER",
  "payload": {
    "round": 1,
    "scores": {
      "Player_1": 1,
      "Player_2": 0
    },
    "status": "round_complete",
    "last_round_result": {
      "correct_choice": "B",
      "Player_1": {"choice": "B", "correct": true, "points_awarded": 1},
      "Player_2": {"choice": "A", "correct": false, "points_awarded": 0}
    },
    "answered": {
      "Player_1": true,
      "Player_2": true
    }
  },
  "timestamp": 1727000026
}
```

---

### 4.8 `ERROR` — Server → Client

**Purpose:** Notify a single client of a rejected action without crashing the server loop or ending the match (unless noted).

**Payload fields:**

| Key | Type | Description |
|---|---|---|
| `code` | string | Machine-readable error code |
| `detail` | string | Human-readable explanation |
| `recoverable` | boolean | `true` if client may continue; `false` if socket will close |

**Error codes:**

| Code | Meaning |
|---|---|
| `MALFORMED` | JSON parse failure or missing required fields |
| `INVALID_MOVE` | Choice not in A–D |
| `OUT_OF_TURN` | `MOVE` received outside `PLAYER_TURN` |
| `ALREADY_ANSWERED` | Duplicate answer in the same round |
| `STALE_ROUND` | `round` field does not match server round |
| `ROOM_FULL` | Third client attempted to join |
| `UNKNOWN_TYPE` | Unsupported `msg_type` |

**Sample:**

```json
{
  "msg_type": "ERROR",
  "player_id": "SERVER",
  "payload": {
    "code": "OUT_OF_TURN",
    "detail": "Answers are not accepted outside PLAYER_TURN.",
    "recoverable": true
  },
  "timestamp": 1727000020
}
```

---

### 4.9 `DISCONNECT` — Client → Server

**Purpose:** Graceful application-layer departure before socket close.

**Payload fields:**

| Key | Type | Description |
|---|---|---|
| `reason` | string | `"quit"` \| `"forfeit"` \| `"client_shutdown"` |

**Sample:**

```json
{
  "msg_type": "DISCONNECT",
  "player_id": "Player_2",
  "payload": {
    "reason": "forfeit"
  },
  "timestamp": 1727000030
}
```

**Server behavior:**
- If match is active → declare opponent winner by forfeit via `GAME_OVER`, then `CLEANUP`
- If still in lobby → remove waiting player and return toward `WAITING_FOR_PLAYERS` / `INIT`

---

### 4.10 `GAME_OVER` — Server → Clients

**Purpose:** Broadcast terminal match outcome.

**Payload fields:**

| Key | Type | Description |
|---|---|---|
| `result` | string | `"WIN"` \| `"DRAW"` \| `"FORFEIT"` |
| `winner` | string \| null | Winning role, or `null` on draw |
| `reason` | string | `"score"` \| `"opponent_disconnect"` \| `"opponent_forfeit"` |
| `final_scores` | object | Final tallies |
| `rounds_played` | integer | Completed rounds |

**Sample (normal win):**

```json
{
  "msg_type": "GAME_OVER",
  "player_id": "SERVER",
  "payload": {
    "result": "WIN",
    "winner": "Player_1",
    "reason": "score",
    "final_scores": {
      "Player_1": 7,
      "Player_2": 4
    },
    "rounds_played": 10
  },
  "timestamp": 1727000200
}
```

**Sample (forfeit after disconnect):**

```json
{
  "msg_type": "GAME_OVER",
  "player_id": "SERVER",
  "payload": {
    "result": "FORFEIT",
    "winner": "Player_1",
    "reason": "opponent_disconnect",
    "final_scores": {
      "Player_1": 3,
      "Player_2": 2
    },
    "rounds_played": 5
  },
  "timestamp": 1727000150
}
```

---

## 5. Connection Termination & Socket Lifecycle

Connections end either gracefully or abruptly. The protocol and server FSM must handle both.

### 5.1 Application-Layer Disconnect (`DISCONNECT`)

1. Client sends a framed `DISCONNECT` message.
2. Server processes forfeit / lobby removal logic.
3. Server may emit `GAME_OVER` to the remaining opponent.
4. Both sides close sockets (`sock.close()`), triggering a normal TCP FIN handshake.

### 5.2 Transport-Layer Clean Closure (TCP FIN / EOF)

When the peer closes its socket cleanly:

- `recv()` does **not** raise an exception
- `recv()` returns **0 bytes** (`b""`) — POSIX EOF

**Critical rule:** Treat `if not data:` as disconnect. Failing to break causes a busy-loop of empty reads at 100% CPU.

```python
data = sock.recv(4096)
if not data:
    # Remote peer closed connection cleanly (TCP FIN received)
    handle_client_disconnect(player_id)
    sock.close()
```

### 5.3 Abrupt Termination (TCP RST / Network Drops)

Causes include process kill (`kill -9`), power loss, or a severed CML link. Symptoms:

| Exception / Signal | Meaning |
|---|---|
| `ConnectionResetError` | Peer sent RST or crashed |
| `BrokenPipeError` | Local `send()`/`sendall()` to a closed peer |
| `ConnectionAbortedError` | Connection aborted by host / OS |
| `TimeoutError` | Optional socket timeout expired |

Dispatch loops must catch these and trigger the same FSM disconnect path used for EOF:

```python
try:
    chunk = sock.recv(4096)
    if not chunk:
        trigger_state_transition("CLIENT_DISCONNECTED")
except (ConnectionResetError, BrokenPipeError, ConnectionAbortedError) as e:
    logger.warning("Connection lost abruptly: %s", e)
    trigger_state_transition("CLIENT_DISCONNECTED")
```

### 5.4 Forfeit Semantics

| Timing | Action |
|---|---|
| Lobby (`WAITING_FOR_PLAYERS`) | Drop disconnected seat; wait for a new player |
| Active match (`GAME_START` … `EVALUATE_MOVE`) | Opponent wins by forfeit; send `GAME_OVER`; enter `CLEANUP` |
| After `GAME_OVER` | Close remaining sockets; reclaim resources |

---

## 6. End-to-End Happy-Path Sequence

```text
Client1                         Server                          Client2
   |-- CONNECT ------------------>|                                |
   |<--------- LOBBY_WAIT --------|                                |
   |                              |<------------- CONNECT ---------|
   |<--------- GAME_START --------|--------- GAME_START ---------->|
   |<---------- QUESTION ---------|---------- QUESTION ----------->|
   |-- MOVE (choice B) ---------->|                                |
   |                              |<------------- MOVE (choice A) -|
   |<------- STATE_UPDATE -----|------ STATE_UPDATE ------>|
   |          ... rounds 2..10 ...                                 |
   |<--------- GAME_OVER ---------|---------- GAME_OVER ---------->|
```

---

## 7. Implementation Notes for Later Sprints

- Server is authoritative; clients never compute final scores locally.
- Unanswered questions at timeout count as incorrect (`points_awarded: 0`).
- Maximum message size soft-cap: 8 KiB per framed line (reject / disconnect if exceeded).
- Recommended bind target in CML: `server.schlicting.edu` → `192.168.20.100`.
