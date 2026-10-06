# AI Prompting & Constraint Strategy — Terminal Trivia

**Student:** Ben Schlicting  
**Course:** CS 457 – Computer Networks  
**Purpose:** Force Cursor agents to implement parsers, serializers, and FSM logic that match **protocol_blueprint.md** and **fsm_specification.md** exactly.

---

## 1. Tooling Plan

**Permitted AI tool: Cursor only.** No GitHub Copilot, ChatGPT, Claude (standalone), or other external assistants will be used for this project.

**Why Cursor only:**
- Access to **multiple LLM types** inside one IDE workflow, so model choice can be matched to the task (e.g. fast edits vs. deeper protocol/FSM reasoning) without switching tools or re-pasting context.
- Easy use of **parallel agents** to work on isolated pieces at the same time (for example framing helpers in one agent and FSM transitions in another) while each agent is still bound to the same constraint block and blueprint docs.

| Cursor capability | Intended use |
|---|---|
| Agent / Chat | Scaffold message dispatch, validation, FSM transitions, disconnect handling |
| Inline edits / Composer | Small framing helpers, socket-loop fixes, schema-aligned refactors |
| Parallel agents | Split independent tasks (client vs. server helpers, validation vs. scoring) under identical CONSTRAINTS |

Cursor output is treated as a **draft**. Nothing is committed until it is checked against the blueprint schemas and FSM transition table.

---

## 2. Hard Constraints (Always Paste Into Prompts)

Copy this block into every implementation prompt:

```text
CONSTRAINTS (NON-NEGOTIABLE):
1) Language: Python 3 standard library only for networking (socket, json, struct optional).
2) Transport: TCP only. Do not use UDP, websockets, HTTP, or Redis.
3) Framing: Newline-delimited JSON. Every message is one compact JSON object + "\n".
4) Envelope keys exactly: msg_type, player_id, payload, timestamp.
5) Allowed msg_type values ONLY:
   CONNECT, LOBBY_WAIT, GAME_START, QUESTION, MOVE, STATE_UPDATE, ERROR, DISCONNECT, GAME_OVER
6) MOVE.payload.choice must be one of "A","B","C","D". No board coordinates (no row/col).
7) Server is authoritative. Clients never decide winners/scores.
8) FSM states ONLY:
   INIT, WAITING_FOR_PLAYERS, GAME_START, PLAYER_TURN, EVALUATE_MOVE, GAME_OVER, CLEANUP
9) Invalid / out-of-turn MOVE => send ERROR and remain in current state (do not crash).
10) recv() returning b"" is EOF disconnect. Also catch ConnectionResetError and BrokenPipeError.
11) Do not invent extra message types, fields, or states unless I explicitly ask.
12) Match field names and semantics in docs/protocol_blueprint.md and docs/fsm_specification.md.
```

---

## 3. System / Role Prompt (Reusable)

```text
You are a careful Python networking engineer implementing Terminal Trivia for CS 457.
You write small, testable functions that strictly obey a frozen application protocol and
server FSM. Prefer explicit validation and clear error codes over clever shortcuts.
If a request conflicts with the protocol blueprint, refuse the conflict and follow the blueprint.
```

---

## 4. Task Prompts

### 4.1 Framing / Serialization Helpers

```text
Using the CONSTRAINTS above, implement:

- send_message(sock, message_dict) -> None
  * json.dumps(..., separators=(",", ":")).encode("utf-8") + b"\n"
  * use sock.sendall

- recv_messages(sock, buffer: bytearray) -> list[dict]
  * append sock.recv(4096) into buffer
  * if recv returns b"", raise ConnectionError("EOF")
  * split on b"\n", json.loads each complete line, keep remainder in buffer
  * return list of parsed dicts

Include type hints and short docstrings. No external libraries.
```

### 4.2 Schema Validation

```text
Implement validate_message(msg: dict) -> tuple[bool, str | None] for Terminal Trivia.
Return (True, None) if valid, else (False, error_code) using codes:
MALFORMED, UNKNOWN_TYPE, INVALID_MOVE, OUT_OF_TURN, ALREADY_ANSWERED, STALE_ROUND, ROOM_FULL.

Validate envelope keys and per-type payload fields exactly as in docs/protocol_blueprint.md.
Do not accept tic-tac-toe style payloads with row/col.
```

### 4.3 FSM Transition Function

```text
Implement a pure function:

transition(state, event, context) -> tuple[new_state, list[outbound_messages]]

States and events must match docs/fsm_specification.md.
Events include: PLAYER_CONNECTED, PLAYER_DISCONNECTED, MOVE_RECEIVED, TIMEOUT, ROUND_SCORED.
Outbound messages must use only the allowed msg_type set.
Show how invalid MOVE emits ERROR and keeps the same state.
```

### 4.4 Disconnect Handling

```text
Write handle_client_disconnect(player_id, state, context) that:
1) Distinguishes lobby disconnect vs in-match disconnect
2) On in-match disconnect: mark opponent winner by forfeit and queue GAME_OVER
3) Always leads toward CLEANUP then INIT
4) Is safe to call from EOF (b"") and from ConnectionResetError / BrokenPipeError handlers
```

### 4.5 Client Thin Terminal

```text
Implement a minimal CLI client that:
- connects via TCP to host/port
- sends CONNECT with display_name
- prints LOBBY_WAIT / GAME_START / QUESTION / STATE_UPDATE / ERROR / GAME_OVER
- on user input A/B/C/D during a question, sends MOVE with current round
- on quit, sends DISCONNECT then closes socket
Do not compute scores locally.
```

---

## 5. Anti-Drift Rules

AI models often drift into tic-tac-toe examples from generic course templates. Reject any draft that:

- Uses `row` / `col` board coordinates
- Omits newline framing or assumes one `recv()` = one message
- Adds WebSocket/HTTP APIs
- Skips `ERROR` handling for bad moves
- Ignores EOF `b""` disconnect detection
- Invents states outside the seven required FSM states

**Correction prompt when drift occurs:**

```text
Your previous draft violates the Terminal Trivia protocol.
Remove board coordinates and any extra message types.
Re-implement using newline-delimited JSON and the exact msg_type / FSM lists
from docs/protocol_blueprint.md and docs/fsm_specification.md.
Show a diff-style summary of what you changed to comply.
```

---

## 6. Verification Checklist Before Accepting AI Code

1. Wire format: every send ends with `\n`; receiver buffers until `\n`.
2. Every message has `msg_type`, `player_id`, `payload`, `timestamp`.
3. `MOVE` uses `choice` + `round` only.
4. Invalid moves produce recoverable `ERROR` and do not advance the FSM incorrectly.
5. Disconnect paths cover `DISCONNECT`, EOF `b""`, and socket exceptions.
6. FSM can reach `GAME_OVER` by score, draw, or forfeit, then `CLEANUP` → `INIT`.

---

## 7. Prompt Logging Practice

When an AI-generated function is kept, note in the commit message or a short code comment:

- Which prompt/task block was used (e.g., “framing helpers”)
- Any manual edits required for blueprint compliance

This keeps Sprint 3 implementation auditable against the Sprint 1 design.
