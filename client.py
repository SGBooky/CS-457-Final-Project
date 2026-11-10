#!/usr/bin/env python3
"""Terminal Trivia CLI client — Sprint 3 implementation.

Thin terminal: renders server pushes and submits CONNECT / MOVE / DISCONNECT.
Does not compute authoritative scores or winners locally.
"""

from __future__ import annotations

import argparse
import socket
import sys
import threading
import time
from typing import Any

from protocol import make_message, recv_messages, send_message

VALID_CHOICES = frozenset({"A", "B", "C", "D"})


class TriviaClient:
    """Interactive (or demo) Terminal Trivia client."""

    def __init__(
        self,
        host: str,
        port: int,
        display_name: str,
        demo: bool = False,
        demo_answers: list[str] | None = None,
    ) -> None:
        self.host = host
        self.port = port
        self.display_name = display_name
        self.demo = demo
        self.demo_answers = demo_answers or []
        self.demo_index = 0

        self.sock: socket.socket | None = None
        self.recv_buffer = bytearray()
        self.role: str | None = None
        self.current_round: int | None = None
        self.awaiting_answer = False
        self.game_over = False
        self.lock = threading.Lock()
        self.running = True

    def log(self, message: str) -> None:
        print(message, flush=True)

    def banner(self, title: str) -> None:
        line = "=" * 52
        self.log(f"\n{line}\n  {title}\n{line}")

    def connect_tcp(self) -> None:
        """Resolve hostname via DNS and open a TCP stream socket."""
        try:
            server_ip = socket.gethostbyname(self.host)
        except socket.gaierror:
            print(f"[ERROR] Unable to resolve DNS hostname '{self.host}'.")
            print(
                "Please check your Cisco router DNS configuration "
                "('ip dns server' / 'ip host') or verify domain spelling."
            )
            sys.exit(1)

        self.log(f"[CLIENT] Resolved '{self.host}' -> {server_ip}")
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            self.sock.connect((server_ip, self.port))
        except OSError as exc:
            print(f"[ERROR] Unable to connect to {self.host}:{self.port} ({exc})")
            sys.exit(1)
        self.log(f"[CLIENT] Connected to {self.host}:{self.port}")

    def send(self, message: dict[str, Any]) -> None:
        assert self.sock is not None
        send_message(self.sock, message)

    def send_connect(self) -> None:
        msg = make_message(
            "CONNECT",
            self.display_name,
            {"display_name": self.display_name},
        )
        self.send(msg)

    def send_move(self, choice: str) -> None:
        if self.current_round is None:
            return
        msg = make_message(
            "MOVE",
            self.role or self.display_name,
            {"choice": choice, "round": self.current_round},
        )
        self.send(msg)
        self.awaiting_answer = False
        self.log(f"[CLIENT] Submitted answer: {choice}")

    def send_disconnect(self, reason: str = "quit") -> None:
        if self.sock is None:
            return
        try:
            self.send(
                make_message(
                    "DISCONNECT",
                    self.role or self.display_name,
                    {"reason": reason},
                )
            )
        except OSError:
            pass

    def render_question(self, payload: dict[str, Any]) -> None:
        self.banner(f"Round {payload.get('round')} — Your Turn")
        self.log(payload.get("prompt", ""))
        options = payload.get("options", {})
        for key in ("A", "B", "C", "D"):
            if key in options:
                self.log(f"  {key}) {options[key]}")
        limit = payload.get("time_limit_sec", 15)
        self.log(f"\nAnswer with A/B/C/D within {limit}s (or 'quit' to forfeit).")

    def render_state(self, payload: dict[str, Any]) -> None:
        status = payload.get("status")
        scores = payload.get("scores", {})
        answered = payload.get("answered", {})
        self.log("\n--- Scoreboard ---")
        self.log(f"  Player_1: {scores.get('Player_1', 0)}")
        self.log(f"  Player_2: {scores.get('Player_2', 0)}")

        if status == "waiting_for_answers":
            p1 = "answered" if answered.get("Player_1") else "waiting..."
            p2 = "answered" if answered.get("Player_2") else "waiting..."
            self.log(f"  Status: Waiting for answers (P1: {p1}, P2: {p2})")
            if self.role and answered.get(self.role):
                self.log("  You answered — waiting for the other player...")
        elif status == "round_complete":
            result = payload.get("last_round_result") or {}
            correct = result.get("correct_choice")
            self.log(f"  Round complete. Correct answer: {correct}")
            for role in ("Player_1", "Player_2"):
                entry = result.get(role) or {}
                mark = "correct" if entry.get("correct") else "incorrect"
                self.log(
                    f"  {role}: chose {entry.get('choice')} ({mark}, "
                    f"+{entry.get('points_awarded', 0)})"
                )

    def render_game_over(self, payload: dict[str, Any]) -> None:
        self.banner("GAME OVER")
        self.log(f"Result : {payload.get('result')}")
        self.log(f"Winner : {payload.get('winner')}")
        self.log(f"Reason : {payload.get('reason')}")
        finals = payload.get("final_scores", {})
        self.log(f"Final  : Player_1={finals.get('Player_1')}  Player_2={finals.get('Player_2')}")
        self.log(f"Rounds : {payload.get('rounds_played')}")

    def handle_message(self, msg: dict[str, Any]) -> None:
        msg_type = msg.get("msg_type")
        payload = msg.get("payload", {})

        if msg_type == "LOBBY_WAIT":
            self.role = payload.get("role", self.role)
            self.banner("Lobby")
            self.log(payload.get("message", "Waiting for Player 2..."))
            self.log(f"Assigned role: {self.role}")

        elif msg_type == "GAME_START":
            self.role = payload.get("role", self.role)
            self.banner("Match Starting")
            self.log(f"You are {self.role}")
            self.log(f"Opponent: {payload.get('opponent')}")
            self.log(
                f"Rounds: {payload.get('total_rounds')} | "
                f"Time limit: {payload.get('time_limit_sec')}s"
            )

        elif msg_type == "QUESTION":
            self.current_round = payload.get("round")
            self.awaiting_answer = True
            self.render_question(payload)
            if self.demo:
                self._demo_answer()

        elif msg_type == "STATE_UPDATE":
            self.render_state(payload)

        elif msg_type == "ERROR":
            self.log(
                f"[ERROR] {payload.get('code')}: {payload.get('detail')} "
                f"(recoverable={payload.get('recoverable')})"
            )
            if payload.get("recoverable") is False:
                self.running = False

        elif msg_type == "GAME_OVER":
            self.game_over = True
            self.awaiting_answer = False
            self.render_game_over(payload)
            self.running = False

        else:
            self.log(f"[CLIENT] Ignoring message type: {msg_type}")

    def _demo_answer(self) -> None:
        """Auto-submit the next scripted choice for execution-log demos."""
        if not self.awaiting_answer:
            return
        if self.demo_index < len(self.demo_answers):
            choice = self.demo_answers[self.demo_index]
            self.demo_index += 1
        else:
            choice = "A"
        # Small delay so both clients' output interleaves cleanly in logs.
        time.sleep(0.15)
        self.send_move(choice)

    def reader_loop(self) -> None:
        assert self.sock is not None
        try:
            while self.running:
                try:
                    messages = recv_messages(self.sock, self.recv_buffer)
                except ConnectionError:
                    self.log("[CLIENT] Server closed the connection.")
                    self.running = False
                    break
                except ValueError:
                    self.log("[CLIENT] Received malformed framed data.")
                    continue
                except (ConnectionResetError, BrokenPipeError, ConnectionAbortedError) as exc:
                    self.log(f"[CLIENT] Connection lost: {exc}")
                    self.running = False
                    break

                for message in messages:
                    with self.lock:
                        self.handle_message(message)
        finally:
            self.running = False
            

    def input_loop(self) -> None:
        """Blocking stdin reader for A/B/C/D or quit (interactive mode only)."""
        while self.running:
            try:
                line = sys.stdin.readline()
            except EOFError:
                break
            if line == "":
                # EOF on stdin — keep running until server closes (demo pipes, etc.).
                while self.running:
                    time.sleep(0.1)
                break

            text = line.strip().upper()
            if not text:
                continue
            if text in {"Q", "QUIT", "EXIT"}:
                self.send_disconnect("forfeit")
                self.running = False
                break
            if text in VALID_CHOICES:
                with self.lock:
                    if self.awaiting_answer and self.current_round is not None:
                        self.send_move(text)
                    else:
                        self.log("[CLIENT] No active question — wait for your turn.")
            else:
                self.log("[CLIENT] Enter A, B, C, D, or quit.")

    def run(self) -> None:
        self.connect_tcp()
        assert self.sock is not None
        self.send_connect()

        reader = threading.Thread(target=self.reader_loop, daemon=True, name="Reader")
        reader.start()

        try:
            if self.demo:
                while self.running:
                    time.sleep(0.1)
            else:
                self.input_loop()
        except KeyboardInterrupt:
            self.log("\n[CLIENT] Interrupted — forfeiting.")
            self.send_disconnect("forfeit")
        finally:
            self.running = False
            reader.join(timeout=2.0)
            try:
                self.sock.close()
            except OSError:
                pass
            self.log("[CLIENT] Disconnected.")


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
        prog="client.py",
        description="Terminal Trivia CLI client (TCP, newline-delimited JSON).",
    )
    parser.add_argument(
        "-i",
        "--host",
        required=True,
        help="Server DNS hostname or IP (required; resolved via socket.gethostbyname()).",
    )
    parser.add_argument(
        "-p",
        "--port",
        type=validate_port,
        required=True,
        help="Server port (required; range 1024-65535).",
    )
    parser.add_argument(
        "-n",
        "--name",
        default="Player",
        help="Display name sent in CONNECT (default: Player).",
    )
    parser.add_argument(
        "--demo",
        action="store_true",
        help="Auto-answer questions for local execution-log demos.",
    )
    parser.add_argument(
        "--demo-answers",
        default="",
        help="Comma-separated A/B/C/D answers used with --demo (e.g. B,B,C).",
    )
    return parser.parse_args(argv)


def main() -> None:
    args = parse_args()
    answers = [a.strip().upper() for a in args.demo_answers.split(",") if a.strip()]
    for ans in answers:
        if ans not in VALID_CHOICES:
            print(f"[ERROR] Invalid demo answer '{ans}'. Use A/B/C/D.")
            sys.exit(1)

    client = TriviaClient(
        host=args.host,
        port=args.port,
        display_name=args.name,
        demo=args.demo,
        demo_answers=answers,
    )
    client.run()


if __name__ == "__main__":
    main()
