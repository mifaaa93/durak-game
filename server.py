"""
Дурень — FastAPI + WebSocket сервер
Запуск: uvicorn server:app --host 0.0.0.0 --port 8000
"""

import asyncio
import json
import os
import random
import string
from typing import Optional
import httpx
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

# ── Статика (index.html) ──────────────────────────────────────────────────────
app.mount("/static", StaticFiles(directory="static"), name="static")

@app.get("/")
async def root():
    return FileResponse(
        "static/index.html",
        headers={"Cache-Control": "no-cache, no-store, must-revalidate"},
    )

# ── Хранилище комнат ──────────────────────────────────────────────────────────
rooms: dict[str, "Room"] = {}

SUITS = ["♠", "♣", "♥", "♦"]
RANKS = ["6", "7", "8", "9", "10", "J", "Q", "K", "A"]
RED_SUITS = {"♥", "♦"}


def rank_val(r: str) -> int:
    return RANKS.index(r)


def beats(attacker: dict, defender: dict, trump: str) -> bool:
    if defender["suit"] == attacker["suit"]:
        return rank_val(defender["rank"]) > rank_val(attacker["rank"])
    if defender["suit"] == trump and attacker["suit"] != trump:
        return True
    return False


def make_deck() -> list[dict]:
    deck = [{"rank": r, "suit": s} for s in SUITS for r in RANKS]
    random.shuffle(deck)
    return deck


def card_key(c: dict) -> str:
    return c["rank"] + c["suit"]


# ── Кімната ───────────────────────────────────────────────────────────────────
class Room:
    def __init__(self, room_id: str, max_players: int):
        self.room_id = room_id
        self.max_players = max_players
        self.connections: dict[str, WebSocket] = {}
        self.players: list[dict] = []
        self.deck: list[dict] = []
        self.trump_suit: str = ""
        self.trump_card: dict = {}
        self.pairs: list[dict] = []
        self.attacker_idx: int = 0
        self.defender_idx: int = 1
        self.phase: str = "lobby"     # lobby | attack | defend | taking | end
        self.finish_count: int = 0
        self.message: str = ""
        self.host_id: str = ""
        self.disconnect_tasks: dict[str, asyncio.Task] = {}
        self.pass_set: set = set()           # гравці що натиснули пас під час taking
        self.rematch_set: set = set()        # гравці що хочуть зіграти ще раз
        self.departed: dict[str, str] = {}   # player_id → name (пішли з end-екрану)

    # ── Розсилка ──────────────────────────────────────────────────────────────
    async def broadcast(self, msg: dict):
        dead = []
        for pid, ws in self.connections.items():
            try:
                await ws.send_text(json.dumps(msg))
            except Exception:
                dead.append(pid)
        for pid in dead:
            self.connections.pop(pid, None)

    async def send_state(self):
        for player in self.players:
            ws = self.connections.get(player["id"])
            if not ws:
                continue
            state = self._build_state(player["id"])
            try:
                await ws.send_text(json.dumps({"type": "state", "state": state}))
            except Exception:
                pass

    def _build_state(self, viewer_id: str) -> dict:
        players_view = []
        for p in self.players:
            is_me = p["id"] == viewer_id
            players_view.append({
                "id": p["id"],
                "name": p["name"],
                "card_count": len(p["hand"]),
                "hand": p["hand"] if is_me else [],
                "out": p["out"],
                "finish_pos": p["finish_pos"],
            })
        attacker = self.players[self.attacker_idx] if self.players and self.attacker_idx < len(self.players) else {}
        defender = self.players[self.defender_idx] if self.players and self.defender_idx < len(self.players) else {}
        return {
            "room_id": self.room_id,
            "phase": self.phase,
            "my_id": viewer_id,
            "players": players_view,
            "deck_count": len(self.deck),
            "trump_suit": self.trump_suit,
            "trump_card": self.trump_card,
            "pairs": self.pairs,
            "attacker_id": attacker.get("id", ""),
            "defender_id": defender.get("id", ""),
            "message": self.message,
            "host_id": self.host_id,
            "pass_set": list(self.pass_set),
        }

    # ── Таймаут відключення ───────────────────────────────────────────────────
    async def handle_disconnect_timeout(self, player_id: str):
        try:
            await asyncio.sleep(10)
        except asyncio.CancelledError:
            return

        self.disconnect_tasks.pop(player_id, None)
        if player_id in self.connections:
            return

        player = next((p for p in self.players if p["id"] == player_id), None)

        if self.phase in ("attack", "defend", "taking") and player and not player["out"]:
            await self.surrender(player_id)
        elif self.phase == "lobby" and player:
            self.players = [p for p in self.players if p["id"] != player_id]
            if self.host_id == player_id:
                self.host_id = self.players[0]["id"] if self.players else ""
            if self.connections:
                await self.send_state()

        if not self.connections:
            rooms.pop(self.room_id, None)

    # ── Вхід у кімнату ────────────────────────────────────────────────────────
    async def join(self, player_id: str, name: str, ws: WebSocket):
        task = self.disconnect_tasks.pop(player_id, None)
        if task:
            task.cancel()
        self.connections[player_id] = ws
        existing = next((p for p in self.players if p["id"] == player_id), None)
        if not existing:
            if len(self.players) >= self.max_players:
                await ws.send_text(json.dumps({"type": "error", "msg": "Кімната заповнена"}))
                return
            player = {"id": player_id, "name": name, "hand": [], "out": False, "finish_pos": None}
            self.players.append(player)
            if not self.host_id:
                self.host_id = player_id
        await self.send_state()

    # ── Старт гри ─────────────────────────────────────────────────────────────
    async def start_game(self, requester_id: str):
        if requester_id != self.host_id:
            return
        if len(self.players) < 2:
            await self.broadcast({"type": "error", "msg": "Потрібно мінімум 2 гравці"})
            return
        self.deck = make_deck()
        trump = self.deck[-1]
        self.trump_suit = trump["suit"]
        self.trump_card = trump

        for p in self.players:
            p["hand"] = []
            p["out"] = False
            p["finish_pos"] = None
        self.finish_count = 0
        self.pass_set = set()
        self.rematch_set = set()
        self.departed = {}

        for _ in range(6):
            for p in self.players:
                if self.deck:
                    p["hand"].append(self.deck.pop(0))

        min_val, first = 99, 0
        for i, p in enumerate(self.players):
            for c in p["hand"]:
                if c["suit"] == self.trump_suit and rank_val(c["rank"]) < min_val:
                    min_val = rank_val(c["rank"])
                    first = i
        self.attacker_idx = first
        self.defender_idx = self._next_active(first)
        self.pairs = []
        self.phase = "attack"
        self.message = ""
        await self.send_state()

    # ── Хід: атака (і підкид під час захисту/взяття) ─────────────────────────
    async def play_attack(self, player_id: str, card: dict):
        if self.phase not in ("attack", "defend", "taking"):
            return await self._err(player_id, "Зараз не можна атакувати")
        attacker = self.players[self.attacker_idx]
        if player_id != attacker["id"]:
            return await self._err(player_id, "Зараз атакує " + attacker["name"])

        if self.pairs:
            ranks_on_table = {card_key_rank(p["attack"]) for p in self.pairs}
            for p in self.pairs:
                if p.get("defend"):
                    ranks_on_table.add(card_key_rank(p["defend"]))
            if card["rank"] not in ranks_on_table:
                return await self._err(player_id, "Можна підкидати лише карти тих самих гідностей")

        defender = self.players[self.defender_idx]
        max_cards = min(len(defender["hand"]) + len([p for p in self.pairs if p.get("defend")]), 6)
        if len(self.pairs) >= max_cards:
            return await self._err(player_id, "Більше не можна підкинути")

        if not self._remove_card(attacker, card):
            return await self._err(player_id, "Такої карти немає в руці")

        self.pairs.append({"attack": card, "defend": None})
        if self.phase != "taking":
            self.phase = "defend"
            self.message = f"{defender['name']} відбиває..."
        await self.send_state()

    # ── Хід: захист ───────────────────────────────────────────────────────────
    async def play_defend(self, player_id: str, attack_card: dict, defend_card: dict):
        if self.phase != "defend":
            return await self._err(player_id, "Зараз не фаза захисту")
        defender = self.players[self.defender_idx]
        if player_id != defender["id"]:
            return await self._err(player_id, "Зараз відбиває " + defender["name"])

        pair = next((p for p in self.pairs
                     if card_key(p["attack"]) == card_key(attack_card) and not p.get("defend")), None)
        if not pair:
            return await self._err(player_id, "Ця карта вже відбита або не знайдена")
        if not beats(attack_card, defend_card, self.trump_suit):
            return await self._err(player_id, "Карта не б'є!")
        if not self._remove_card(defender, defend_card):
            return await self._err(player_id, "Такої карти немає в руці")

        pair["defend"] = defend_card
        undefended = [p for p in self.pairs if not p.get("defend")]
        if not undefended:
            self.phase = "attack"
            self.message = "Все відбито! Можна підкинути або завершити хід."
        await self.send_state()

    # ── Взяти карти (ініціюємо фазу taking) ──────────────────────────────────
    async def take_cards(self, player_id: str):
        if self.phase != "defend":
            return
        defender = self.players[self.defender_idx]
        if player_id != defender["id"]:
            return await self._err(player_id, "Лише захисник може взяти карти")

        non_defenders = [p for p in self.players if not p["out"] and p["id"] != defender["id"]]
        if not non_defenders:
            await self._execute_take()
            return

        self.phase = "taking"
        self.pass_set = set()
        self.message = f"{defender['name']} бере карти — підкидайте або пас"
        await self.send_state()

    # ── Пас під час taking ────────────────────────────────────────────────────
    async def pass_throw(self, player_id: str):
        if self.phase != "taking":
            return
        defender = self.players[self.defender_idx]
        if player_id == defender["id"]:
            return
        player = next((p for p in self.players if p["id"] == player_id), None)
        if not player or player["out"]:
            return

        self.pass_set.add(player_id)

        non_defenders = [p for p in self.players if not p["out"] and p["id"] != defender["id"]]
        if all(p["id"] in self.pass_set for p in non_defenders):
            await self._execute_take()
        else:
            remaining = len(non_defenders) - len(self.pass_set)
            self.message = f"Чекаємо пасу ще від {remaining} гравця..."
            await self.send_state()

    # ── Виконати взяття карт ──────────────────────────────────────────────────
    async def _execute_take(self):
        defender = self.players[self.defender_idx]
        all_cards = [p["attack"] for p in self.pairs] + \
                    [p["defend"] for p in self.pairs if p.get("defend")]
        defender["hand"].extend(all_cards)
        self.pairs = []
        self.pass_set = set()
        old_def_idx = self.defender_idx
        self.attacker_idx = self._next_active(old_def_idx)
        self.defender_idx = self._next_active(self.attacker_idx)
        self.phase = "attack"
        self._refill()
        self._check_win()
        self.message = f"{defender['name']} взяв карти!"
        await self.send_state()

    # ── Завершити хід (відбито) ───────────────────────────────────────────────
    async def end_turn(self, player_id: str):
        attacker = self.players[self.attacker_idx]
        if player_id != attacker["id"]:
            return await self._err(player_id, "Лише атакуючий може завершити хід")
        undefended = [p for p in self.pairs if not p.get("defend")]
        if undefended:
            return await self._err(player_id, "Є невідбиті карти!")
        self.pairs = []
        old_def_idx = self.defender_idx
        self.attacker_idx = old_def_idx
        self.defender_idx = self._next_active(old_def_idx)
        self.phase = "attack"
        self._refill()
        self._check_win()
        self.message = ""
        await self.send_state()

    # ── Здатися ───────────────────────────────────────────────────────────────
    async def surrender(self, player_id: str):
        player = next((p for p in self.players if p["id"] == player_id), None)
        if not player or player["out"] or self.phase not in ("attack", "defend", "taking"):
            return
        if self.players[self.defender_idx]["id"] == player_id:
            all_cards = [p["attack"] for p in self.pairs] + \
                        [p["defend"] for p in self.pairs if p.get("defend")]
            player["hand"].extend(all_cards)
        self.pairs = []
        self.pass_set = set()
        player["out"] = True
        player["finish_pos"] = 9999

        was_att = self.players[self.attacker_idx]["id"] == player_id
        was_def = self.players[self.defender_idx]["id"] == player_id
        if was_att:
            self.attacker_idx = self._next_active(self.attacker_idx)
            self.defender_idx = self._next_active(self.attacker_idx)
        elif was_def:
            old_def = self.defender_idx
            self.attacker_idx = self._next_active(old_def)
            self.defender_idx = self._next_active(self.attacker_idx)

        self.phase = "attack"
        self._refill()
        self._check_win()
        self.message = f"{player['name']} здався!"
        await self.send_state()

    # ── Покинути лобі ─────────────────────────────────────────────────────────
    async def leave_room(self, player_id: str):
        if self.phase != "lobby":
            return
        # Якщо лобі відкрите після реваншу — надсилаємо TG-запрошення
        if self.rematch_set:
            rematcher = next(
                (p for p in self.players if p["id"] in self.rematch_set and p["id"] != player_id),
                None,
            )
            if rematcher:
                asyncio.create_task(send_tg_invite(player_id, rematcher["name"], self.room_id))
        self.players = [p for p in self.players if p["id"] != player_id]
        self.connections.pop(player_id, None)
        self.rematch_set.discard(player_id)
        if self.host_id == player_id:
            self.host_id = self.players[0]["id"] if self.players else ""
        if not self.players:
            rooms.pop(self.room_id, None)
        else:
            await self.send_state()

    # ── Реванш: гравець хоче зіграти ще раз ─────────────────────────────────
    async def rematch(self, player_id: str):
        if self.phase != "end":
            return
        self.rematch_set.add(player_id)
        # Повідомляємо всіх хто вже пішов
        rematcher = next((p for p in self.players if p["id"] == player_id), None)
        rematcher_name = rematcher["name"] if rematcher else "Гравець"
        for dep_id in list(self.departed):
            asyncio.create_task(send_tg_invite(dep_id, rematcher_name, self.room_id))
        self.departed.clear()
        if len(self.rematch_set) == 1:
            self.host_id = player_id
            self.phase = "lobby"
            self.message = ""
        await self.send_state()

    # ── Вихід з екрану результатів ────────────────────────────────────────────
    async def leave_end(self, player_id: str):
        if self.phase not in ("end", "lobby"):
            return
        player = next((p for p in self.players if p["id"] == player_id), None)
        player_name = player["name"] if player else ""

        if self.rematch_set:
            # Вже є охочі грати — повідомляємо одразу
            rematcher = next(
                (p for p in self.players if p["id"] in self.rematch_set and p["id"] != player_id),
                None,
            )
            if rematcher:
                asyncio.create_task(send_tg_invite(player_id, rematcher["name"], self.room_id))
        elif player_name:
            # Запам'ятовуємо — повідомимо коли хтось натисне реванш
            self.departed[player_id] = player_name

        self.players = [p for p in self.players if p["id"] != player_id]
        self.connections.pop(player_id, None)
        self.rematch_set.discard(player_id)

        if not self.players:
            rooms.pop(self.room_id, None)
        else:
            await self.send_state()

    # ── Підкид (перехід назад в атаку) ───────────────────────────────────────
    async def throw_more(self, player_id: str):
        attacker = self.players[self.attacker_idx]
        if player_id != attacker["id"]:
            return
        undefended = [p for p in self.pairs if not p.get("defend")]
        if undefended:
            return await self._err(player_id, "Спочатку потрібно відбити всі карти")
        self.phase = "attack"
        self.message = "Підкидайте карти або завершуйте хід."
        await self.send_state()

    # ── Внутрішні методи ──────────────────────────────────────────────────────
    def _next_active(self, from_idx: int) -> int:
        idx = (from_idx + 1) % len(self.players)
        for _ in range(len(self.players)):
            if not self.players[idx]["out"]:
                return idx
            idx = (idx + 1) % len(self.players)
        return idx

    def _remove_card(self, player: dict, card: dict) -> bool:
        for i, c in enumerate(player["hand"]):
            if card_key(c) == card_key(card):
                player["hand"].pop(i)
                return True
        return False

    def _refill(self):
        order = []
        cur = self.attacker_idx
        for _ in range(len(self.players)):
            if not self.players[cur]["out"] and cur != self.defender_idx:
                order.append(cur)
            cur = (cur + 1) % len(self.players)
        order.append(self.defender_idx)
        for idx in order:
            p = self.players[idx]
            while len(p["hand"]) < 6 and self.deck:
                p["hand"].append(self.deck.pop(0))

    def _check_win(self):
        for p in self.players:
            if not p["out"] and not p["hand"] and not self.deck:
                p["out"] = True
                self.finish_count += 1
                p["finish_pos"] = self.finish_count
        active = [p for p in self.players if not p["out"]]
        if len(active) <= 1:
            if active:
                active[0]["out"] = True
                self.finish_count += 1
                active[0]["finish_pos"] = self.finish_count
            self.phase = "end"

    async def _err(self, player_id: str, msg: str):
        ws = self.connections.get(player_id)
        if ws:
            try:
                await ws.send_text(json.dumps({"type": "error", "msg": msg}))
            except Exception:
                pass


def card_key_rank(c: dict) -> str:
    return c["rank"]


# ── Telegram-повідомлення про реванш ─────────────────────────────────────────
async def send_tg_invite(to_player_id: str, from_player_name: str, room_id: str):
    bot_token = os.getenv("BOT_TOKEN")
    server_url = os.getenv("SERVER_URL")
    if not bot_token:
        return
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            await client.post(
                f"https://api.telegram.org/bot{bot_token}/sendMessage",
                json={
                    "chat_id": to_player_id,
                    "text": f"🃏 *{from_player_name}* пропонує зіграти ще раз!",
                    "parse_mode": "Markdown",
                    "reply_markup": {
                        "inline_keyboard": [[{
                            "text": "🎮 Приєднатися →",
                            "web_app": {"url": f"{server_url}?room={room_id}"}
                        }]]
                    },
                },
            )
    except Exception:
        pass


# ── HTTP: створити кімнату ────────────────────────────────────────────────────
@app.post("/room/create")
async def create_room(max_players: int = 6):
    room_id = "".join(random.choices(string.ascii_uppercase + string.digits, k=6))
    rooms[room_id] = Room(room_id, max_players)
    return {"room_id": room_id}


@app.get("/room/{room_id}/exists")
async def room_exists(room_id: str):
    return {"exists": room_id in rooms}


@app.get("/rooms")
async def list_rooms():
    result = []
    for room_id, room in rooms.items():
        if room.phase == "lobby":
            result.append({
                "room_id": room_id,
                "player_count": len(room.players),
                "max_players": room.max_players,
            })
    return result


# ── WebSocket ─────────────────────────────────────────────────────────────────
@app.websocket("/ws/{room_id}/{player_id}/{name}")
async def websocket_endpoint(ws: WebSocket, room_id: str, player_id: str, name: str):
    await ws.accept()
    if room_id not in rooms:
        await ws.send_text(json.dumps({"type": "error", "msg": "Кімнату не знайдено"}))
        await ws.close()
        return

    room = rooms[room_id]
    await room.join(player_id, name, ws)

    try:
        while True:
            raw = await ws.receive_text()
            msg = json.loads(raw)
            action = msg.get("action")

            if action == "start":
                await room.start_game(player_id)
            elif action == "attack":
                await room.play_attack(player_id, msg["card"])
            elif action == "defend":
                await room.play_defend(player_id, msg["attack_card"], msg["defend_card"])
            elif action == "take":
                await room.take_cards(player_id)
            elif action == "pass":
                await room.pass_throw(player_id)
            elif action == "end_turn":
                await room.end_turn(player_id)
            elif action == "throw_more":
                await room.throw_more(player_id)
            elif action == "surrender":
                await room.surrender(player_id)
            elif action == "rematch":
                await room.rematch(player_id)
            elif action == "leave_end":
                await room.leave_end(player_id)
                break
            elif action == "leave_room":
                await room.leave_room(player_id)
                break
            elif action == "ping":
                await ws.send_text(json.dumps({"type": "pong"}))

    except WebSocketDisconnect:
        room.connections.pop(player_id, None)
        task = asyncio.create_task(room.handle_disconnect_timeout(player_id))
        room.disconnect_tasks[player_id] = task
        await room.broadcast({
            "type": "player_disconnected",
            "msg": "Гравець відключився"
        })
