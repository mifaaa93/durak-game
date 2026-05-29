"""
Дурак — FastAPI + WebSocket сервер
Запуск: uvicorn server:app --host 0.0.0.0 --port 8000
"""

import asyncio
import json
import random
import string
from typing import Optional
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
    return FileResponse("static/index.html")

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


# ── Комната ───────────────────────────────────────────────────────────────────
class Room:
    def __init__(self, room_id: str, max_players: int):
        self.room_id = room_id
        self.max_players = max_players
        self.connections: dict[str, WebSocket] = {}   # player_id -> ws
        self.players: list[dict] = []                  # [{id, name, hand, out, finish_pos}]
        self.deck: list[dict] = []
        self.trump_suit: str = ""
        self.trump_card: dict = {}
        self.pairs: list[dict] = []   # [{attack, defend|None}]
        self.attacker_idx: int = 0
        self.defender_idx: int = 1
        self.phase: str = "lobby"     # lobby | attack | defend | end
        self.finish_count: int = 0
        self.message: str = ""
        self.host_id: str = ""

    # ── Рассылка ──────────────────────────────────────────────────────────────
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
        """Отправляем каждому игроку его персональное состояние."""
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
        """Строим состояние для конкретного игрока — он видит только свои карты."""
        players_view = []
        for p in self.players:
            is_me = p["id"] == viewer_id
            players_view.append({
                "id": p["id"],
                "name": p["name"],
                "card_count": len(p["hand"]),
                "hand": p["hand"] if is_me else [],   # только свои карты
                "out": p["out"],
                "finish_pos": p["finish_pos"],
            })
        attacker = self.players[self.attacker_idx] if self.players else {}
        defender = self.players[self.defender_idx] if self.players else {}
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
        }

    # ── Вход в комнату ────────────────────────────────────────────────────────
    async def join(self, player_id: str, name: str, ws: WebSocket):
        self.connections[player_id] = ws
        # Если игрок уже есть — переподключение
        existing = next((p for p in self.players if p["id"] == player_id), None)
        if not existing:
            if len(self.players) >= self.max_players:
                await ws.send_text(json.dumps({"type": "error", "msg": "Комната заполнена"}))
                return
            player = {"id": player_id, "name": name, "hand": [], "out": False, "finish_pos": None}
            self.players.append(player)
            if not self.host_id:
                self.host_id = player_id
        await self.send_state()

    # ── Старт игры ────────────────────────────────────────────────────────────
    async def start_game(self, requester_id: str):
        if requester_id != self.host_id:
            return
        if len(self.players) < 2:
            await self.broadcast({"type": "error", "msg": "Нужно минимум 2 игрока"})
            return
        self.deck = make_deck()
        trump = self.deck[-1]
        self.trump_suit = trump["suit"]
        self.trump_card = trump

        # Сброс рук
        for p in self.players:
            p["hand"] = []
            p["out"] = False
            p["finish_pos"] = None
        self.finish_count = 0

        # Раздача по 6 карт
        for _ in range(6):
            for p in self.players:
                if self.deck:
                    p["hand"].append(self.deck.pop(0))

        # Первый ход — у кого наименьший козырь
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

    # ── Ход: атака (и подброс во время защиты) ───────────────────────────────
    async def play_attack(self, player_id: str, card: dict):
        if self.phase not in ("attack", "defend"):
            return await self._err(player_id, "Сейчас нельзя атаковать")
        attacker = self.players[self.attacker_idx]
        if player_id != attacker["id"]:
            return await self._err(player_id, "Сейчас атакует " + attacker["name"])

        # Проверка: карта допустима?
        if self.pairs:
            ranks_on_table = {card_key_rank(p["attack"]) for p in self.pairs}
            for p in self.pairs:
                if p.get("defend"):
                    ranks_on_table.add(card_key_rank(p["defend"]))
            if card["rank"] not in ranks_on_table:
                return await self._err(player_id, "Можно подбрасывать только карты тех же достоинств")

        defender = self.players[self.defender_idx]
        max_cards = min(len(defender["hand"]) + len([p for p in self.pairs if p.get("defend")]), 6)
        if len(self.pairs) >= max_cards:
            return await self._err(player_id, "Больше нельзя подбросить")

        if not self._remove_card(attacker, card):
            return await self._err(player_id, "Такой карты нет в руке")

        self.pairs.append({"attack": card, "defend": None})
        self.phase = "defend"
        self.message = f"{defender['name']} отбивает..."
        await self.send_state()

    # ── Ход: защита ───────────────────────────────────────────────────────────
    async def play_defend(self, player_id: str, attack_card: dict, defend_card: dict):
        if self.phase != "defend":
            return await self._err(player_id, "Сейчас не фаза защиты")
        defender = self.players[self.defender_idx]
        if player_id != defender["id"]:
            return await self._err(player_id, "Сейчас отбивает " + defender["name"])

        pair = next((p for p in self.pairs
                     if card_key(p["attack"]) == card_key(attack_card) and not p.get("defend")), None)
        if not pair:
            return await self._err(player_id, "Эта карта уже отбита или не найдена")
        if not beats(attack_card, defend_card, self.trump_suit):
            return await self._err(player_id, "Карта не бьёт!")
        if not self._remove_card(defender, defend_card):
            return await self._err(player_id, "Такой карты нет в руке")

        pair["defend"] = defend_card
        undefended = [p for p in self.pairs if not p.get("defend")]
        if not undefended:
            self.phase = "attack"
            self.message = "Всё отбито! Можно подбросить или завершить ход."
        await self.send_state()

    # ── Взять карты ───────────────────────────────────────────────────────────
    async def take_cards(self, player_id: str):
        defender = self.players[self.defender_idx]
        if player_id != defender["id"]:
            return await self._err(player_id, "Только защищающийся может взять карты")
        all_cards = [p["attack"] for p in self.pairs] + \
                    [p["defend"] for p in self.pairs if p.get("defend")]
        defender["hand"].extend(all_cards)
        self.pairs = []
        old_def_idx = self.defender_idx
        self.attacker_idx = self._next_active(old_def_idx)
        self.defender_idx = self._next_active(self.attacker_idx)
        self.phase = "attack"
        self._refill()
        self._check_win()
        self.message = f"{defender['name']} взял карты!"
        await self.send_state()

    # ── Завершить ход (отбито) ────────────────────────────────────────────────
    async def end_turn(self, player_id: str):
        attacker = self.players[self.attacker_idx]
        if player_id != attacker["id"]:
            return await self._err(player_id, "Только атакующий может завершить ход")
        undefended = [p for p in self.pairs if not p.get("defend")]
        if undefended:
            return await self._err(player_id, "Есть неотбитые карты!")
        self.pairs = []
        old_def_idx = self.defender_idx
        # After successful defense the defender becomes the new attacker
        self.attacker_idx = old_def_idx
        self.defender_idx = self._next_active(old_def_idx)
        self.phase = "attack"
        self._refill()
        self._check_win()
        self.message = ""
        await self.send_state()

    # ── Сдаться ───────────────────────────────────────────────────────────────
    async def surrender(self, player_id: str):
        player = next((p for p in self.players if p["id"] == player_id), None)
        if not player or player["out"] or self.phase not in ("attack", "defend"):
            return
        # Defender takes all table cards as penalty
        if self.players[self.defender_idx]["id"] == player_id:
            all_cards = [p["attack"] for p in self.pairs] + \
                        [p["defend"] for p in self.pairs if p.get("defend")]
            player["hand"].extend(all_cards)
        self.pairs = []
        player["out"] = True
        player["finish_pos"] = 9999  # Always sorts last (дурак)

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
        self.message = f"{player['name']} сдался!"
        await self.send_state()

    # ── Подброс (переход обратно в атаку) ────────────────────────────────────
    async def throw_more(self, player_id: str):
        attacker = self.players[self.attacker_idx]
        if player_id != attacker["id"]:
            return
        undefended = [p for p in self.pairs if not p.get("defend")]
        if undefended:
            return await self._err(player_id, "Сначала нужно отбить все карты")
        self.phase = "attack"
        self.message = "Подбрасывайте карты или завершайте ход."
        await self.send_state()

    # ── Внутренние методы ─────────────────────────────────────────────────────
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


# ── HTTP: создать комнату ─────────────────────────────────────────────────────
@app.post("/room/create")
async def create_room(max_players: int = 6):
    room_id = "".join(random.choices(string.ascii_uppercase + string.digits, k=6))
    rooms[room_id] = Room(room_id, max_players)
    return {"room_id": room_id}


@app.get("/room/{room_id}/exists")
async def room_exists(room_id: str):
    return {"exists": room_id in rooms}


# ── WebSocket ────────────────────────────────────────────────────────────────
@app.websocket("/ws/{room_id}/{player_id}/{name}")
async def websocket_endpoint(ws: WebSocket, room_id: str, player_id: str, name: str):
    await ws.accept()
    if room_id not in rooms:
        await ws.send_text(json.dumps({"type": "error", "msg": "Комната не найдена"}))
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
            elif action == "end_turn":
                await room.end_turn(player_id)
            elif action == "throw_more":
                await room.throw_more(player_id)
            elif action == "surrender":
                await room.surrender(player_id)
            elif action == "ping":
                await ws.send_text(json.dumps({"type": "pong"}))

    except WebSocketDisconnect:
        room.connections.pop(player_id, None)
        await room.broadcast({
            "type": "player_disconnected",
            "msg": f"Игрок отключился"
        })
