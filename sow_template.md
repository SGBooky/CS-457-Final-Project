# CS 457 Final Project — Statement of Work (Sprint 0)

## 1. Student / Project Information

| Field | Value |
| --- | --- |
| Student Name | SG Booky / bbschlicting |
| Course | CS 457 — Final Project |
| Sprint | Sprint 0 |
| Preferred Language | Python 3.x |
| Preferred IDE | Cursor / VS Code |
| Public Repository | https://github.com/SGBooky/CS-457-Final-Project |

## 2. Toolchain Setup (Step 2.1)

- **Python:** Python 3.14.3 installed and verified on the local development system.
- **IDE:** Cursor and Visual Studio Code configured for Python development.
- **Cisco Modeling Labs (CML):** Access verified; CML controller reports healthy compute/controller status with reference platform images available.

## 3. Game Selection (Step 2.3)

**Selected Game:** Terminal Trivia

Terminal Trivia is a turn-based, two-player CLI quiz game. Two networked players compete over a fixed number of rounds by answering multiple-choice questions. Scores accumulate each round; the player with the higher score at the end of the match wins.

## 4. Exact Game Rules

### 4.1 Player Roles & Turn Mechanics

1. The server waits until **exactly two clients** connect and each submits a display name.
2. Player roles are assigned by connection order:
   - **Player 1** = first successful connection
   - **Player 2** = second successful connection
3. A match consists of a fixed number of **rounds** (default: **10 questions**).
4. For each round:
   - The server broadcasts the same multiple-choice question (options A–D) to both players.
   - Players answer independently within a per-question time limit (default: **15 seconds**).
   - Answers are submitted once per round; late or missing answers score **0** for that round.
   - After both answers are received (or the timer expires), the server reveals the correct answer, awards points, and advances to the next round.
5. Turn order for answering is **simultaneous within a round** (not alternating moves). Round progression is strictly sequential: Round *n* completes before Round *n+1* begins.

### 4.2 Scoring

- Correct answer within the time limit: **+1 point**
- Incorrect answer: **0 points**
- No answer / timeout: **0 points**
- Running totals are shown after every round (simple leaderboard).

### 4.3 Victory Conditions

- After all scheduled rounds complete, the player with the **higher total score** is the winner.
- The server announces the final scores and the winner’s display name.

### 4.4 Draw / Tie Conditions

- If both players finish with the **same total score**, the match is a **tie**.
- No sudden-death tiebreaker in the baseline Sprint design; the result is reported as a draw.
- Optional later enhancement (not required for baseline): one bonus sudden-death question if scores are equal.

### 4.5 Disconnect / Forfeit

- If a connected player disconnects mid-match and does not reconnect before the next round starts, that player **forfeits** and the remaining player is declared the winner (provided at least one round has been scored, or both players had connected successfully).

## 5. Target Server Domain Naming (Step 2.4)

| Field | Value |
| --- | --- |
| Authoritative DNS Domain Name | `server.schlicting.edu` |
| Format compliance | `server.[yourlastname].edu` |

This domain will be used for authoritative DNS resolution in later sprints when the game server is reachable by name rather than by raw IP address.

## 6. Design Roadmap (High Level)

| Phase | Focus | Planned Outcome |
| --- | --- | --- |
| Sprint 0 | Environment, SOW, public repo | Toolchain ready; rules and domain locked |
| Early sprints | Local CLI prototype | Two-player Terminal Trivia playable on localhost sockets |
| Mid sprints | Network integration | Client/server over TCP; CML lab topology; DNS for `server.schlicting.edu` |
| Later sprints | Hardening & polish | Reliable scoring, timeouts, disconnect handling, demo readiness |
| Final | Demonstration | End-to-end two-player networked Terminal Trivia demo |

## 7. Sprint 0 Deliverables Checklist

- [x] Public Git repository initialized and accessible to teaching staff
- [x] Language-appropriate `.gitignore` present (Python artifacts, venvs, OS junk, binaries)
- [x] SOW describes a valid two-player CLI game with turn/round order, win conditions, and draw rules
- [x] SOW specifies target domain as `server.schlicting.edu`
- [x] Toolchain verified (Python 3.x, IDE, CML access)

---

*Sprint 0 Statement of Work — Terminal Trivia*
