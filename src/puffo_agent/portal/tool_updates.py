"""Daemon-owned daily tool checks and durable machine→operator reminders."""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import math
import time
from dataclasses import asdict
from pathlib import Path

import aiohttp
from packaging.version import Version

from ..crypto.http_session import create_remote_http_session
from .control import agent_message, machine_auth
from .control.store import ControlPairing, load_or_create_machine, load_pairings
from .host_assets import atomic_write_private
from .state import home_dir
from .tool_update_sources import Update, _read_json, check_updates

log = logging.getLogger(__name__)
_DAY = 24 * 60 * 60
_RETRY = 60 * 60
_PATH = "/v2/machines/me/messages"


def _recipient_key(pairing: ControlPairing) -> str:
    identity = [pairing.server_url.rstrip("/"), pairing.operator_slug, pairing.operator_root_pubkey]
    return hashlib.sha256(json.dumps(identity).encode()).hexdigest()


def _update_slot(update: Update) -> str:
    return f"{update.tool}:{update.source}:{update.channel}"


def _notification(updates: list[Update]) -> dict:
    lines = ["Local tool updates available / 本机工具有可用更新："]
    for update in updates:
        lines.append(f"- {update.name}: {update.current} → {update.latest} ({update.channel})\n  {update.instructions}")
    lines.append("Update when convenient; nothing has been upgraded automatically. / 请自行选择更新时间；未自动升级。")
    return {"type": "tool.update_available", "text": "\n".join(lines)}


async def _build_envelope(pairing: ControlPairing, updates: list[Update]) -> dict:
    machine = load_or_create_machine()
    recipients = await agent_message.fetch_active_recipients(
        pairing.server_url, machine, pairing.operator_slug, pairing.operator_root_pubkey,
    )
    if not recipients:
        raise RuntimeError("operator has no active recipient devices")
    return agent_message.build_machine_message_envelope(machine, recipients, _notification(updates))


async def _deliver(pairing: ControlPairing, envelope: dict) -> None:
    machine = load_or_create_machine()
    body = json.dumps({"operator_slug": pairing.operator_slug, "envelope": envelope}).encode()
    headers = machine_auth.signed_headers(machine, "POST", _PATH, body)
    headers["content-type"] = "application/json"
    base = pairing.server_url.rstrip("/")
    async with create_remote_http_session(base, timeout=aiohttp.ClientTimeout(total=20)) as session:
        async with session.post(base + _PATH, data=body, headers=headers, allow_redirects=False) as response:
            # A successful socket write is NOT delivery: require the durable
            # server write ACK. Old servers (404) retain the pending envelope.
            if response.status != 200:
                raise RuntimeError(f"machine message rejected: HTTP {response.status}")
            raw = bytearray()
            async for chunk in response.content.iter_chunked(1024):
                raw.extend(chunk)
                if len(raw) > 4096:
                    raise ValueError("machine message acknowledgement too large")
            if json.loads(raw).get("ok") is not True:
                raise ValueError("invalid machine message acknowledgement")


class ToolUpdateChecker:
    """One writer per daemon home; timestamps and outbox survive restarts."""

    def __init__(self, path: Path | None = None) -> None:
        self._path = path if path is not None else home_dir() / "tool_updates.json"

    def _load(self) -> dict:
        if not self._path.exists():
            return {"checked_at": 0, "updates": [], "recipients": {}}
        # A corrupt state must not silently turn into a fresh state and repeat
        # delivered notifications. Log the problem and leave the file intact.
        state = _read_json(self._path)
        stamp = state["checked_at"]
        if not isinstance(stamp, (float, int)) or not math.isfinite(stamp):
            raise ValueError("invalid check timestamp")
        if not isinstance(state["updates"], list) or not isinstance(state["recipients"], dict):
            raise ValueError("invalid update state")
        for item in state["updates"]:
            Update(**item)
        return state

    def _save(self, state: dict) -> None:
        atomic_write_private(self._path, json.dumps(state, ensure_ascii=False))

    async def _tick(self, now: float) -> None:
        pairings = load_pairings()
        if not pairings:
            return
        state = self._load()
        # A backwards clock cannot suppress checks indefinitely.
        if state["checked_at"] == 0 or now < state["checked_at"] or now - state["checked_at"] >= _DAY:
            updates = await check_updates()
            state["updates"] = [asdict(update) for update in updates]
            state["checked_at"] = now
            self._save(state)
        updates = [Update(**item) for item in state["updates"]]
        for pairing in list(pairings.values()):
            try:
                async with asyncio.timeout(60):
                    await self._notify(pairing, updates, state)
            except Exception as exc:  # A failed recipient must not starve another.
                log.warning("tool update notification deferred: %s", type(exc).__name__)

    async def _notify(self, pairing: ControlPairing, updates: list[Update], state: dict) -> None:
        key = _recipient_key(pairing)
        record = state["recipients"].setdefault(key, {"notified": {}, "pending": None})
        pending = record["pending"]
        if pending is None:
            unseen = [u for u in updates if Version(u.latest) > Version(record["notified"].get(_update_slot(u), "0"))]
            if not unseen:
                return
            pending = {"envelope": await _build_envelope(pairing, unseen),
                       "versions": {_update_slot(u): u.latest for u in unseen}}
            record["pending"] = pending
            self._save(state)  # Persist exact encrypted envelope BEFORE sending.
        # Pairings can be removed while remote recipient lookup is in flight.
        active = load_pairings().get(pairing.operator_slug)
        if active is None or _recipient_key(active) != key:
            return
        await _deliver(pairing, pending["envelope"])
        record["notified"].update(pending["versions"])
        record["pending"] = None
        self._save(state)

    async def run_loop(self, stop: asyncio.Event) -> None:
        while not stop.is_set():
            try:
                await self._tick(time.time())
            except Exception as exc:
                log.warning("tool update check deferred: %s", type(exc).__name__)
            try:
                await asyncio.wait_for(stop.wait(), timeout=_RETRY)
            except TimeoutError:
                pass
