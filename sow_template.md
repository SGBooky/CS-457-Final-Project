# CS 457 Project Statement of Work (SOW) & Protocol Specification Template

**Student Name:** Ben Schlicting  
**Date:** 2026-09-16  
**Course:** CS 457 - Computer Networks  
**Target Server Domain:** `server.schlicting.edu`

---

## 1. Game Selection & Scope (Sprint 0)

> Planning is going to be an iterative process through the sprints so you don't have to have all the details now. Focus on big overview concepts. You will be updating the SOW as we plan.
> You have a lot of freedom to choose a game. There are a couple caveats.

> - It must run in the console. The lab nodes won't be able to handle extensive graphics.
> - It has to be self-contained. You can use a internet-connector to download you code, but because the architecture must run 5 nodes you won't be able to run
> - You are encouraged to use python, but I'm not going to make it a strict requirement. The instructor and TA's ability to help with C or Rust, etc will be diminished in other languages.

### 1.1 Game Overview

- **Chosen Game:** Terminal Trivia
- **Player Capacity:** 2 Players (Simulated via 2 CML Client nodes)
- **Game Summary:** Two networked players compete in a round based CLI quiz. The server presents multiple choice questions to both people each round. Players submit answers independently within a 15 second time limit. Correct answers earn points; after 10 rounds the person with the higher score wins.

### 1.2 Core Game Rules & Win/Draw Conditions

- **Turn Mechanics:** Play proceeds in 10 rounds. Each round, both players receive the same question and may answer simultaneously within a 15-second window. The server waits for both answers then scores the round then broadcasts results then advances to the next round.
- **Victory Condition:** After all scheduled rounds complete the player with the higher total score is the winner.
- **Draw/Tie Condition:** If both players finish with the same total score, the match is declared a tie/draw.

---

## 2. Application-Layer Messaging Protocol Blueprint (Sprint 1 Deliverable)

- **protocol_blueprint.md**

### 2.1 Message Transport & Serialization Format

- **Transport Protocol:** TCP
- **Serialization Format:** JSON
- **Framing Mechanism:** Newline-delimited (`\n`) JSON payloads (one complete JSON object per line)
- **Receiver Rule:** Accumulate bytes in a per-socket buffer until, then json.loads() each complete line.

### 2.2 Message Schema Definitions

#### Message Types:

| Type | Direction | Purpose |
|---|---|---|
| `CONNECT` | Client → Server | Join game room with display name |
| `LOBBY_WAIT` | Server → Client | Waiting for Player 2 |
| `GAME_START` | Server → Clients | Roles assigned; match begins |
| `QUESTION` | Server → Clients | Current round prompt + A–D options |
| `MOVE` | Client → Server | Answer selection (`choice`: `"A"`\|`"B"`\|`"C"`\|`"D"`) |
| `STATE_UPDATE` | Server → Clients | Scores, round results, answer flags |
| `ERROR` | Server → Client | Invalid / out-of-turn / malformed rejection |
| `DISCONNECT` | Client → Server | Intentional quit / forfeit |
| `GAME_OVER` | Server → Clients | WIN / DRAW / FORFEIT + final scores |

#### Example JSON Protocol Schema:

```json
{
  "msg_type": "MOVE",
  "player_id": "Player_1",
  "payload": {
    "choice": "B",
    "round": 1
  },
  "timestamp": 1727000000
}
```

#### Wire Stream Example (back-to-back framed messages):

```text
{"msg_type":"CONNECT","player_id":"Alice","payload":{"display_name":"Alice"},"timestamp":1727000000}\n{"msg_type":"MOVE","player_id":"Alice","payload":{"choice":"B","round":1},"timestamp":1727000005}\n
```

### 2.3 Game State Machine (FSM) Design (Sprint 1 Deliverable)

> **Full specification + Mermaid diagram:**
- **fsm_specification.md**

- **State Transitions:** Detail state flow: `INIT` -> `WAITING_FOR_PLAYERS` -> `GAME_START` -> `PLAYER_TURN` -> `EVALUATE_MOVE` -> `GAME_OVER` -> `CLEANUP` -> (reset) `INIT`
- **Edge cases:** Invalid/out-of-turn `MOVE` -> `ERROR` (Client disconnect) -> forfeit `GAME_OVER` -> `CLEANUP`
- **Diagram:** Mermaid `stateDiagram-v2` in `docs/fsm_specification.md`

### 2.4 AI Prompting Strategy (Sprint 1 Deliverable)

> **Full prompt library:** [`docs/ai_prompts.md`](docs/ai_prompts.md)

Constrained Cursor-only prompts force agents to implement newline-delimited JSON framing, exact message schemas, and the seven-state FSM without drifting into tic-tac-toe board coordinates or alternate transports. Cursor is used exclusively for its multiple LLM options and parallel-agent workflow.

---

## 3. Game Behavior & Server Concurrency Architecture (Sprint 2 Deliverable)

### 3.1 Server Concurrency Strategy
- **Architecture Choice:** [Multi-Threading (`threading.Thread`) OR Non-blocking I/O multiplexing (`select.select` / `selectors`)]
- **Synchronization Logic:** Explain how shared game state and client list are thread-safe (e.g. `threading.Lock`) to prevent race conditions during turn processing.

### 3.2 State & Score Synchronization Across Clients
- **Turn Enforcement:** Detail how the server validates active player ID before processing moves and broadcasts updated turn notifications to all clients.
- **Score & Board Synchronization:** Describe how state broadcasts keep client screens synchronized in real time.

---

## 4. Coding & AI Implementation Plan (Sprint 3)

- **Permitted AI Tools:** Cursor only (multiple LLM types + parallel agents in one workflow)
- **AI Prompting & Constraint Strategy:** See **ai_prompts.md** — system constraints require exact schema/FSM compliance before any generated code is accepted.
- **Implementation Risk Management:** Implement framing + CONNECT/lobby first, then QUESTION/MOVE scoring loop, then disconnect/forfeit paths; keep functions small and testable against the blueprint.

---

## 5. CML Multi-Subnet Topology & Wireshark Deployment Plan (Sprint 4 & 5 Deliverable)

> For now you can use the topology below. We may update this when we get to defining subnets.

### 5.1 Subnet & Router Design
- **Subnet A (Client 1):** `192.168.10.0/24` (Interface `Gi0/1` on Router R1)
- **Subnet B (Client 2):** `192.168.11.0/24` (Interface `Gi0/2` on Router R1)
- **Subnet C (Game Server):** `192.168.20.0/24` (Interface `Gi0/1` on Router R2)
- **Router Backbone:** `10.0.0.0/30` (Interface `Gi0/0` on R1 <-> `Gi0/0` on R2)

### 5.2 DHCP Pools & DNS Configuration Plan
- **Router R1 DHCP Pool 1 (`CLIENT1_POOL`):** Leases `192.168.10.10` - `192.168.10.50`, gateway `192.168.10.1`, DNS `10.0.0.2`.
- **Router R1 DHCP Pool 2 (`CLIENT2_POOL`):** Leases `192.168.11.10` - `192.168.11.50`, gateway `192.168.11.1`, DNS `10.0.0.2`.
- **Router R2 Authoritative DNS:** Configured with `ip dns server` and static host mapping `server.schlicting.edu` -> `192.168.20.100`.

### 5.3 Deployment Strategy & Wireshark Trace Capture
- **CML Deployment Strategy:** Deploy `server.py` onto Subnet C node (`192.168.20.100`) behind Router R2, and `client.py` onto Subnet A and Subnet B nodes behind Router R1.
- **Cisco Infrastructure Configuration:** Router R1 DHCP pools (`CLIENT1_POOL`, `CLIENT2_POOL`) and Router R2 authoritative DNS (`ip host server.schlicting.edu 192.168.20.100`).
- **Wireshark Trace Capture Plan:** Capture DHCP DORA exchange (`dhcp_negotiation.pcap`) and DNS query/response resolution (`dns_lookup.pcap`).
