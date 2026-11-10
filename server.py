#!/usr/bin/env python3
"""Terminal Trivia game server — Sprint 3 implementation.

Concurrency: one worker thread per client (docs/concurrency_design.md).
FSM: INIT → WAITING_FOR_PLAYERS → GAME_START → PLAYER_TURN → EVALUATE_MOVE
     → GAME_OVER → CLEANUP → INIT (docs/fsm_specification.md).
"""

from __future__ import annotations

import argparse
import socket
import sys
import threading
import time
from typing import Any

from protocol import (
    VALID_CHOICES,
    make_message,
    recv_messages,
    send_message,
    validate_envelope,
)


# Match length and answer window from SOW / FSM specification.
TOTAL_ROUNDS: int = 10
TIME_LIMIT_SEC: int = 15
ROLE_P1: str = "Player_1"
ROLE_P2: str = "Player_2"

# Networking / CS457-flavored trivia bank. Correct key is never sent to clients.
QUESTION_BANK: list[dict[str, Any]] = [
    {
        "prompt": "Which protocol provides reliable ordered byte streams?",
        "options": {"A": "UDP", "B": "TCP", "C": "ICMP", "D": "ARP"},
        "correct": "B",
    },
    {
        "prompt": "Which OSI layer handles routing between networks?",
        "options": {"A": "Physical", "B": "Network", "C": "Session", "D": "Application"},
        "correct": "B",
    },
    {
        "prompt": "What does DNS primarily resolve?",
        "options": {
            "A": "MAC to IP",
            "B": "Port to process",
            "C": "Hostname to IP",
            "D": "IP to ASN",
        },
        "correct": "C",
    },
    {
        "prompt": "Which address family is used for IPv4 sockets?",
        "options": {"A": "AF_UNIX", "B": "AF_INET6", "C": "AF_PACKET", "D": "AF_INET"},
        "correct": "D",
    },
    {
        "prompt": "What transport flag combination starts a TCP three-way handshake?",
        "options": {"A": "FIN/ACK", "B": "SYN", "C": "RST", "D": "PSH"},
        "correct": "B",
    },
    {
        "prompt": "Which device typically forwards frames based on MAC addresses?",
        "options": {"A": "Router", "B": "Hub", "C": "Switch", "D": "Firewall"},
        "correct": "C",
    },
    {
        "prompt": "What is the default port for HTTPS?",
        "options": {"A": "80", "B": "22", "C": "443", "D": "53"},
        "correct": "C",
    },
    {
        "prompt": "Which ICMP message type is commonly used by ping?",
        "options": {
            "A": "Echo Request/Reply",
            "B": "Redirect",
            "C": "Source Quench",
            "D": "Timestamp",
        },
        "correct": "A",
    },
    {
        "prompt": "In CIDR, how many host bits remain in a /24 IPv4 prefix?",
        "options": {"A": "8", "B": "16", "C": "24", "D": "32"},
        "correct": "A",
    },
    {
        "prompt": "Which protocol maps IP addresses to MAC addresses on a LAN?",
        "options": {"A": "DHCP", "B": "ARP", "C": "NTP", "D": "SMTP"},
        "correct": "B",
    },
]


class ClientSession:
    """Per-seat connection metadata owned by the match engine."""

    def __init__(self, sock: socket.socket, addr: tuple[str, int], display_name: str, role: str) -> None:
        self.sock = sock
        self.addr = addr
        self.display_name = display_name
        self.role = role
        self.recv_buffer = bytearray()


class GameServer:
    """Authoritative Terminal Trivia match engine with threaded workers."""

    def __init__(self, port: int) -> None:
        self.port = port
        self.state_lock = threading.RLock()
        self.fsm_state = "INIT"
        self.clients: dict[str, ClientSession] = {}
        self.unseated: dict[socket.socket, bytearray] = {}
        self.scores: dict[str, int] = {ROLE_P1: 0, ROLE_P2: 0}
        self.answers: dict[str, str | None] = {ROLE_P1: None, ROLE_P2: None}
        self.current_round = 0
        self.round_deadline = 0.0
        self.timer_generation = 0
        self.listen_sock: socket.socket | None = None
        self.running = True

    # ------------------------------------------------------------------ CLI helpers
    def log(self, message: str) -> None:
        print(f"[SERVER] {message}", flush=True)

    # ------------------------------------------------------------------ Messaging
    def _safe_send(self, sock: socket.socket, message: dict[str, Any]) -> bool:
        try:
            send_message(sock, message)
            return True
        except OSError as exc:
            self.log(f"Send failed: {exc}")
            return False

    def _send_error(
        self,
        sock: socket.socket,
        code: str,
        detail: str,
        recoverable: bool = True,
    ) -> None:
        msg = make_message(
            "ERROR",
            "SERVER",
            {"code": code, "detail": detail, "recoverable": recoverable},
        )
        self._safe_send(sock, msg)

    def _broadcast(self, message: dict[str, Any]) -> None:
        """Fan-out one framed message to every seated client (caller holds lock)."""
        dead: list[str] = []
        for role, session in self.clients.items():
            if not self._safe_send(session.sock, message):
                dead.append(role)
        for role in dead:
            self._handle_disconnect_locked(role, reason="send_failure")

    # ------------------------------------------------------------------ Match reset
    def _reset_match_tables(self) -> None:
        self.clients.clear()
        self.scores = {ROLE_P1: 0, ROLE_P2: 0}
        self.answers = {ROLE_P1: None, ROLE_P2: None}
        self.current_round = 0
        self.round_deadline = 0.0
        self.timer_generation += 1
        self.fsm_state = "WAITING_FOR_PLAYERS"
        self.log("FSM -> WAITING_FOR_PLAYERS")

    def _cleanup_match(self) -> None:
        """Close seats and return to INIT for the next pair of players."""
        self.log("FSM -> CLEANUP")
        for session in list(self.clients.values()):
            try:
                session.sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                session.sock.close()
            except OSError:
                pass
        self.clients.clear()
        self.scores = {ROLE_P1: 0, ROLE_P2: 0}
        self.answers = {ROLE_P1: None, ROLE_P2: None}
        self.current_round = 0
        self.round_deadline = 0.0
        self.timer_generation += 1
        self.fsm_state = "INIT"
        self.log("FSM -> INIT (ready for next match)")
        self.fsm_state = "WAITING_FOR_PLAYERS"
        self.log("FSM -> WAITING_FOR_PLAYERS")

    # ------------------------------------------------------------------ Game flow
    def _question_payload(self) -> dict[str, Any]:
        q = QUESTION_BANK[self.current_round - 1]
        return {
            "round": self.current_round,
            "prompt": q["prompt"],
            "options": q["options"],
            "time_limit_sec": TIME_LIMIT_SEC,
        }

    def _start_round_timer(self) -> None:
        self.timer_generation += 1
        gen = self.timer_generation
        self.round_deadline = time.monotonic() + TIME_LIMIT_SEC
        thread = threading.Thread(
            target=self._round_timer_worker,
            args=(gen, self.current_round),
            daemon=True,
            name=f"RoundTimer-{self.current_round}",
        )
        thread.start()

    def _round_timer_worker(self, generation: int, round_num: int) -> None:
        time.sleep(TIME_LIMIT_SEC)
        with self.state_lock:
            if generation != self.timer_generation:
                return
            if self.fsm_state != "PLAYER_TURN":
                return
            if self.current_round != round_num:
                return
            self.log(f"Round {round_num} timer expired — forcing EVALUATE_MOVE")
            self._evaluate_move_locked()

    def _enter_game_start_locked(self) -> None:
        self.fsm_state = "GAME_START"
        self.log("FSM -> GAME_START")
        self.scores = {ROLE_P1: 0, ROLE_P2: 0}
        self.answers = {ROLE_P1: None, ROLE_P2: None}
        self.current_round = 1

        p1 = self.clients[ROLE_P1]
        p2 = self.clients[ROLE_P2]

        msg_p1 = make_message(
            "GAME_START",
            "SERVER",
            {
                "role": ROLE_P1,
                "opponent": p2.display_name,
                "total_rounds": TOTAL_ROUNDS,
                "time_limit_sec": TIME_LIMIT_SEC,
            },
        )
        msg_p2 = make_message(
            "GAME_START",
            "SERVER",
            {
                "role": ROLE_P2,
                "opponent": p1.display_name,
                "total_rounds": TOTAL_ROUNDS,
                "time_limit_sec": TIME_LIMIT_SEC,
            },
        )
        self._safe_send(p1.sock, msg_p1)
        self._safe_send(p2.sock, msg_p2)

        self.fsm_state = "PLAYER_TURN"
        self.log(f"FSM -> PLAYER_TURN (round {self.current_round})")
        self._broadcast(make_message("QUESTION", "SERVER", self._question_payload()))
        self._start_round_timer()

    def _answered_flags(self) -> dict[str, bool]:
        return {
            ROLE_P1: self.answers[ROLE_P1] is not None,
            ROLE_P2: self.answers[ROLE_P2] is not None,
        }

    def _evaluate_move_locked(self) -> None:
        """Score the active round and either continue or end the match."""
        if self.fsm_state != "PLAYER_TURN":
            return

        self.fsm_state = "EVALUATE_MOVE"
        self.log(f"FSM -> EVALUATE_MOVE (round {self.current_round})")
        # Invalidate any pending timer for this round.
        self.timer_generation += 1

        correct = QUESTION_BANK[self.current_round - 1]["correct"]
        last_round_result: dict[str, Any] = {"correct_choice": correct}

        for role in (ROLE_P1, ROLE_P2):
            choice = self.answers[role]
            is_correct = choice == correct
            points = 1 if is_correct else 0
            self.scores[role] += points
            last_round_result[role] = {
                "choice": choice,
                "correct": is_correct,
                "points_awarded": points,
            }

        update = make_message(
            "STATE_UPDATE",
            "SERVER",
            {
                "round": self.current_round,
                "scores": dict(self.scores),
                "status": "round_complete",
                "last_round_result": last_round_result,
                "answered": self._answered_flags(),
            },
        )
        self._broadcast(update)

        if self.current_round < TOTAL_ROUNDS:
            self.current_round += 1
            self.answers = {ROLE_P1: None, ROLE_P2: None}
            self.fsm_state = "PLAYER_TURN"
            self.log(f"FSM -> PLAYER_TURN (round {self.current_round})")
            self._broadcast(make_message("QUESTION", "SERVER", self._question_payload()))
            self._start_round_timer()
            return

        self._finish_by_score_locked()

    def _finish_by_score_locked(self) -> None:
        s1 = self.scores[ROLE_P1]
        s2 = self.scores[ROLE_P2]
        if s1 == s2:
            result, winner, reason = "DRAW", None, "score"
        elif s1 > s2:
            result, winner, reason = "WIN", ROLE_P1, "score"
        else:
            result, winner, reason = "WIN", ROLE_P2, "score"

        self.fsm_state = "GAME_OVER"
        self.log(f"FSM -> GAME_OVER ({result}, winner={winner})")
        payload = {
            "result": result,
            "winner": winner,
            "reason": reason,
            "final_scores": dict(self.scores),
            "rounds_played": self.current_round,
        }
        self._broadcast(make_message("GAME_OVER", "SERVER", payload))
        self._cleanup_match()

    def _finish_by_forfeit_locked(self, winner: str, reason: str) -> None:
        self.fsm_state = "GAME_OVER"
        self.log(f"FSM -> GAME_OVER (FORFEIT, winner={winner})")
        payload = {
            "result": "FORFEIT",
            "winner": winner,
            "reason": reason,
            "final_scores": dict(self.scores),
            "rounds_played": max(0, self.current_round - 1),
        }
        self._broadcast(make_message("GAME_OVER", "SERVER", payload))
        self._cleanup_match()

    def _handle_disconnect_locked(self, role: str, reason: str = "opponent_disconnect") -> None:
        if role not in self.clients:
            return

        session = self.clients.pop(role)
        self.log(f"{role} ({session.display_name}) disconnected ({reason})")
        try:
            session.sock.close()
        except OSError:
            pass

        active = {"GAME_START", "PLAYER_TURN", "EVALUATE_MOVE"}
        if self.fsm_state in active:
            opponent = ROLE_P2 if role == ROLE_P1 else ROLE_P1
            if opponent in self.clients:
                self._finish_by_forfeit_locked(opponent, reason)
            else:
                self._cleanup_match()
            return

        if self.fsm_state == "WAITING_FOR_PLAYERS":
            if not self.clients:
                self.fsm_state = "INIT"
                self.log("FSM -> INIT (lobby empty)")
                self.fsm_state = "WAITING_FOR_PLAYERS"
                self.log("FSM -> WAITING_FOR_PLAYERS")
            return

        if self.fsm_state in {"GAME_OVER", "CLEANUP", "INIT"}:
            return

    # ------------------------------------------------------------------ Dispatch
    def _role_for_sock(self, sock: socket.socket) -> str | None:
        for role, session in self.clients.items():
            if session.sock is sock:
                return role
        return None

    def _handle_connect(self, sock: socket.socket, addr: tuple[str, int], msg: dict[str, Any]) -> None:
        payload = msg.get("payload", {})
        display_name = payload.get("display_name")
        if not isinstance(display_name, str) or not display_name.strip():
            self._send_error(sock, "MALFORMED", "CONNECT requires display_name.", False)
            return
        display_name = display_name.strip()[:24]

        with self.state_lock:
            if len(self.clients) >= 2 or self.fsm_state not in {"INIT", "WAITING_FOR_PLAYERS"}:
                self._send_error(sock, "ROOM_FULL", "Match room is full.", False)
                try:
                    sock.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
                try:
                    sock.close()
                except OSError:
                    pass
                return

            if self.fsm_state == "INIT":
                self.fsm_state = "WAITING_FOR_PLAYERS"

            role = ROLE_P1 if ROLE_P1 not in self.clients else ROLE_P2
            session = ClientSession(sock, addr, display_name, role)
            # Adopt any bytes already buffered before seating.
            if sock in self.unseated:
                session.recv_buffer = self.unseated.pop(sock)
            self.clients[role] = session
            self.log(f"Seated {display_name} as {role} from {addr}")

            if len(self.clients) == 1:
                lobby = make_message(
                    "LOBBY_WAIT",
                    "SERVER",
                    {
                        "role": role,
                        "players_connected": 1,
                        "players_needed": 2,
                        "message": "Waiting for Player 2 to connect.",
                    },
                )
                self._safe_send(sock, lobby)
            else:
                self._enter_game_start_locked()

    def _handle_move(self, sock: socket.socket, msg: dict[str, Any]) -> None:
        with self.state_lock:
            role = self._role_for_sock(sock)
            if role is None:
                self._send_error(sock, "MALFORMED", "Player is not seated.", True)
                return

            if self.fsm_state != "PLAYER_TURN":
                self._send_error(sock, "OUT_OF_TURN", "Answers are not accepted outside PLAYER_TURN.", True)
                return

            payload = msg.get("payload", {})
            choice = payload.get("choice")
            round_field = payload.get("round")

            if choice not in VALID_CHOICES:
                self._send_error(sock, "INVALID_MOVE", "Choice must be A, B, C, or D.", True)
                return
            if not isinstance(round_field, int) or round_field != self.current_round:
                self._send_error(sock, "STALE_ROUND", "Round does not match the active question.", True)
                return
            if self.answers[role] is not None:
                self._send_error(sock, "ALREADY_ANSWERED", "You already answered this round.", True)
                return
            if time.monotonic() > self.round_deadline:
                self._send_error(sock, "OUT_OF_TURN", "Answer window has closed.", True)
                return

            self.answers[role] = choice
            self.log(f"{role} answered {choice} for round {self.current_round}")
            # Do not notify the opponent yet — mid-round "answered" updates are distracting
            # while they are still reading the question. Sync happens only after scoring.

            if self.answers[ROLE_P1] is not None and self.answers[ROLE_P2] is not None:
                self._evaluate_move_locked()

    def _handle_disconnect_msg(self, sock: socket.socket, msg: dict[str, Any]) -> None:
        with self.state_lock:
            role = self._role_for_sock(sock)
            if role is None:
                return
            reason_payload = msg.get("payload", {}).get("reason", "forfeit")
            reason = "opponent_forfeit" if reason_payload == "forfeit" else "opponent_disconnect"
            self._handle_disconnect_locked(role, reason=reason)

    def dispatch(self, sock: socket.socket, addr: tuple[str, int], msg: dict[str, Any]) -> None:
        ok, err = validate_envelope(msg)
        if not ok:
            code = err or "MALFORMED"
            self._send_error(sock, code, "Invalid message envelope.", code != "ROOM_FULL")
            return

        msg_type = msg["msg_type"]
        if msg_type == "CONNECT":
            self._handle_connect(sock, addr, msg)
        elif msg_type == "MOVE":
            self._handle_move(sock, msg)
        elif msg_type == "DISCONNECT":
            self._handle_disconnect_msg(sock, msg)
        else:
            self._send_error(sock, "UNKNOWN_TYPE", f"Unsupported client msg_type: {msg_type}", True)

    # ------------------------------------------------------------------ Worker / accept
    def client_worker(self, conn: socket.socket, addr: tuple[str, int]) -> None:
        self.log(f"Worker started for {addr}")
        buffer = bytearray()
        self.unseated[conn] = buffer

        try:
            while self.running:
                try:
                    messages = recv_messages(conn, buffer)
                except ConnectionError:
                    break
                except ValueError:
                    self._send_error(conn, "MALFORMED", "Malformed framed JSON.", True)
                    continue
                except (
                    ConnectionResetError,
                    BrokenPipeError,
                    ConnectionAbortedError,
                    OSError,
                ) as exc:
                    # OSError covers Windows WinError 10038 after CLEANUP closes the socket.
                    self.log(f"Connection lost from {addr}: {exc}")
                    break

                for message in messages:
                    self.dispatch(conn, addr, message)
                    # If this socket was closed during cleanup, stop reading.
                    if conn.fileno() == -1:
                        return
        finally:
            with self.state_lock:
                self.unseated.pop(conn, None)
                role = self._role_for_sock(conn)
                if role is not None:
                    self._handle_disconnect_locked(role, reason="opponent_disconnect")
                else:
                    try:
                        conn.close()
                    except OSError:
                        pass
            self.log(f"Worker exited for {addr}")

    def serve_forever(self) -> None:
        self.listen_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.listen_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.listen_sock.bind(("0.0.0.0", self.port))
        self.listen_sock.listen(5)
        self.log(f"Listening on 0.0.0.0:{self.port}")

        with self.state_lock:
            self._reset_match_tables()

        try:
            while self.running:
                try:
                    conn, addr = self.listen_sock.accept()
                except OSError:
                    break
                worker = threading.Thread(
                    target=self.client_worker,
                    args=(conn, addr),
                    daemon=True,
                    name=f"ClientWorker-{addr[0]}:{addr[1]}",
                )
                worker.start()
        finally:
            self.running = False
            if self.listen_sock is not None:
                try:
                    self.listen_sock.close()
                except OSError:
                    pass
            self.log("Server shut down.")


def validate_port(value: str) -> int:
    """Argparse type: require an integer port in 1024–65535."""
    try:
        port = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("Port must be an integer.") from exc
    if port < 1024 or port > 65535:
        raise argparse.ArgumentTypeError("Port must be in range 1024-65535.")
    return port


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="server.py",
        description="Terminal Trivia authoritative game server (TCP, newline-delimited JSON).",
    )
    parser.add_argument(
        "-p",
        "--port",
        type=validate_port,
        required=True,
        help="Listening port (required; range 1024-65535). Binds to 0.0.0.0.",
    )
    return parser.parse_args(argv)


def main() -> None:
    args = parse_args()
    server = GameServer(port=args.port)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[SERVER] Interrupted — shutting down.", flush=True)
        server.running = False
        if server.listen_sock is not None:
            server.listen_sock.close()
        sys.exit(0)


if __name__ == "__main__":
    main()
