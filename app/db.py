from __future__ import annotations

import asyncio
import json
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import aiosqlite

SCHEMA = """
CREATE TABLE IF NOT EXISTS conversations (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL DEFAULT '',
    kind TEXT NOT NULL DEFAULT 'chat',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS run_state (
    conversation_id TEXT PRIMARY KEY,
    history_json TEXT,
    updated_at TEXT
);

CREATE TABLE IF NOT EXISTS runs (
    id TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL,
    trigger TEXT NOT NULL,
    status TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    error TEXT
);

CREATE TABLE IF NOT EXISTS approvals (
    id TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL,
    run_id TEXT NOT NULL,
    tool_name TEXT NOT NULL,
    args_json TEXT NOT NULL,
    reason TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    note TEXT,
    created_at TEXT NOT NULL,
    resolved_at TEXT,
    resolved_by TEXT
);

CREATE TABLE IF NOT EXISTS audit (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    conversation_id TEXT,
    run_id TEXT,
    kind TEXT NOT NULL,
    name TEXT,
    payload_json TEXT
);

CREATE TABLE IF NOT EXISTS campaigns (
    id TEXT PRIMARY KEY,
    name TEXT,
    category TEXT,
    status TEXT,
    discount_pct REAL,
    budget REAL,
    starts_at TEXT,
    ends_at TEXT,
    product_ids TEXT,
    notes TEXT,
    created_at TEXT,
    updated_at TEXT
);
"""


def now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


class Store:
    def __init__(self, path: Path) -> None:
        self._path = path
        self._db: aiosqlite.Connection | None = None
        self._lock = asyncio.Lock()

    @property
    def db(self) -> aiosqlite.Connection:
        if self._db is None:
            raise RuntimeError("Store baglantisi acilmadi")
        return self._db

    async def connect(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._db = await aiosqlite.connect(self._path)
        self._db.row_factory = aiosqlite.Row
        await self._db.execute("PRAGMA journal_mode=WAL")
        await self._db.executescript(SCHEMA)
        await self._db.commit()

    async def close(self) -> None:
        if self._db is not None:
            await self._db.close()
            self._db = None

    # ------------------------------------------------------------------ conversations

    async def create_conversation(self, *, kind: str = "chat", title: str = "") -> str:
        conversation_id = new_id("conv")
        ts = now_iso()
        async with self._lock:
            await self.db.execute(
                "INSERT INTO conversations (id, title, kind, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
                (conversation_id, title, kind, ts, ts),
            )
            await self.db.commit()
        return conversation_id

    async def get_conversation(self, conversation_id: str) -> dict | None:
        cursor = await self.db.execute("SELECT * FROM conversations WHERE id = ?", (conversation_id,))
        row = await cursor.fetchone()
        return dict(row) if row else None

    async def list_conversations(self, limit: int = 100) -> list[dict]:
        cursor = await self.db.execute(
            "SELECT * FROM conversations ORDER BY updated_at DESC LIMIT ?", (limit,)
        )
        return [dict(row) for row in await cursor.fetchall()]

    async def set_conversation_title(self, conversation_id: str, title: str) -> None:
        async with self._lock:
            await self.db.execute(
                "UPDATE conversations SET title = ?, updated_at = ? WHERE id = ?",
                (title, now_iso(), conversation_id),
            )
            await self.db.commit()

    async def touch_conversation(self, conversation_id: str) -> None:
        async with self._lock:
            await self.db.execute(
                "UPDATE conversations SET updated_at = ? WHERE id = ?",
                (now_iso(), conversation_id),
            )
            await self.db.commit()

    # ------------------------------------------------------------------ run state

    async def get_history(self, conversation_id: str) -> bytes | None:
        cursor = await self.db.execute(
            "SELECT history_json FROM run_state WHERE conversation_id = ?", (conversation_id,)
        )
        row = await cursor.fetchone()
        if row is None or row["history_json"] is None:
            return None
        return row["history_json"].encode("utf-8")

    async def save_history(self, conversation_id: str, history_json: bytes) -> None:
        async with self._lock:
            await self.db.execute(
                """
                INSERT INTO run_state (conversation_id, history_json, updated_at) VALUES (?, ?, ?)
                ON CONFLICT(conversation_id) DO UPDATE SET history_json = excluded.history_json,
                    updated_at = excluded.updated_at
                """,
                (conversation_id, history_json.decode("utf-8"), now_iso()),
            )
            await self.db.commit()

    # ------------------------------------------------------------------ runs

    async def create_run(self, conversation_id: str, trigger: str) -> str:
        run_id = new_id("run")
        async with self._lock:
            await self.db.execute(
                "INSERT INTO runs (id, conversation_id, trigger, status, started_at) VALUES (?, ?, ?, ?, ?)",
                (run_id, conversation_id, trigger, "running", now_iso()),
            )
            await self.db.commit()
        return run_id

    async def finish_run(self, run_id: str, status: str, error: str | None = None) -> None:
        async with self._lock:
            await self.db.execute(
                "UPDATE runs SET status = ?, finished_at = ?, error = ? WHERE id = ?",
                (status, now_iso(), error, run_id),
            )
            await self.db.commit()

    async def mark_run_running(self, run_id: str) -> None:
        async with self._lock:
            await self.db.execute(
                "UPDATE runs SET status = 'running', finished_at = NULL WHERE id = ?", (run_id,)
            )
            await self.db.commit()

    async def get_run(self, run_id: str) -> dict | None:
        cursor = await self.db.execute("SELECT * FROM runs WHERE id = ?", (run_id,))
        row = await cursor.fetchone()
        return dict(row) if row else None

    async def active_run(self, conversation_id: str) -> dict | None:
        cursor = await self.db.execute(
            """
            SELECT * FROM runs WHERE conversation_id = ? AND status IN ('running', 'waiting_approval')
            ORDER BY started_at DESC LIMIT 1
            """,
            (conversation_id,),
        )
        row = await cursor.fetchone()
        return dict(row) if row else None

    async def list_runs(self, conversation_id: str, limit: int = 20) -> list[dict]:
        cursor = await self.db.execute(
            "SELECT * FROM runs WHERE conversation_id = ? ORDER BY started_at DESC LIMIT ?",
            (conversation_id, limit),
        )
        return [dict(row) for row in await cursor.fetchall()]

    # ------------------------------------------------------------------ approvals

    async def add_approvals(self, conversation_id: str, run_id: str, approvals: list[dict]) -> None:
        ts = now_iso()
        async with self._lock:
            for approval in approvals:
                await self.db.execute(
                    """
                    INSERT OR REPLACE INTO approvals
                        (id, conversation_id, run_id, tool_name, args_json, reason, status, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, 'pending', ?)
                    """,
                    (
                        approval["id"],
                        conversation_id,
                        run_id,
                        approval["tool_name"],
                        json.dumps(approval.get("args") or {}, ensure_ascii=False),
                        approval.get("reason"),
                        ts,
                    ),
                )
            await self.db.commit()

    async def list_approvals(
        self, status: str | None = None, conversation_id: str | None = None
    ) -> list[dict]:
        query = "SELECT * FROM approvals WHERE 1=1"
        params: list[Any] = []
        if status:
            query += " AND status = ?"
            params.append(status)
        if conversation_id:
            query += " AND conversation_id = ?"
            params.append(conversation_id)
        query += " ORDER BY created_at DESC"
        cursor = await self.db.execute(query, params)
        return [dict(row) for row in await cursor.fetchall()]

    async def get_approval(self, approval_id: str) -> dict | None:
        cursor = await self.db.execute("SELECT * FROM approvals WHERE id = ?", (approval_id,))
        row = await cursor.fetchone()
        return dict(row) if row else None

    async def resolve_approval(
        self, approval_id: str, status: str, note: str | None = None, resolved_by: str = "user"
    ) -> None:
        async with self._lock:
            await self.db.execute(
                """
                UPDATE approvals SET status = ?, note = ?, resolved_at = ?, resolved_by = ?
                WHERE id = ?
                """,
                (status, note, now_iso(), resolved_by, approval_id),
            )
            await self.db.commit()

    async def pending_approvals_for_run(self, run_id: str) -> list[dict]:
        cursor = await self.db.execute(
            "SELECT * FROM approvals WHERE run_id = ? AND status = 'pending'", (run_id,)
        )
        return [dict(row) for row in await cursor.fetchall()]

    async def approvals_for_run(self, run_id: str) -> list[dict]:
        cursor = await self.db.execute(
            "SELECT * FROM approvals WHERE run_id = ? ORDER BY created_at", (run_id,)
        )
        return [dict(row) for row in await cursor.fetchall()]

    async def pending_approval_count(self) -> int:
        cursor = await self.db.execute("SELECT COUNT(*) AS n FROM approvals WHERE status = 'pending'")
        row = await cursor.fetchone()
        return int(row["n"]) if row else 0

    # ------------------------------------------------------------------ audit

    async def audit(
        self,
        kind: str,
        *,
        conversation_id: str | None = None,
        run_id: str | None = None,
        name: str | None = None,
        payload: Any = None,
    ) -> None:
        async with self._lock:
            await self.db.execute(
                "INSERT INTO audit (ts, conversation_id, run_id, kind, name, payload_json) VALUES (?, ?, ?, ?, ?, ?)",
                (
                    now_iso(),
                    conversation_id,
                    run_id,
                    kind,
                    name,
                    json.dumps(payload, ensure_ascii=False, default=str) if payload is not None else None,
                ),
            )
            await self.db.commit()

    async def budget_delta_since(self, since_iso: str) -> float:
        cursor = await self.db.execute(
            "SELECT payload_json FROM audit WHERE kind = 'budget_change' AND ts >= ?", (since_iso,)
        )
        total = 0.0
        for row in await cursor.fetchall():
            try:
                total += abs(float(json.loads(row["payload_json"] or "{}").get("delta", 0)))
            except (TypeError, ValueError, json.JSONDecodeError):
                continue
        return total

    async def list_audit(self, *, days: int = 7, kind: str | None = None, limit: int = 500) -> list[dict]:
        cutoff = (datetime.now(UTC) - timedelta(days=days)).isoformat(timespec="seconds")
        query = "SELECT * FROM audit WHERE ts >= ?"
        params: list[Any] = [cutoff]
        if kind:
            query += " AND kind = ?"
            params.append(kind)
        query += " ORDER BY id DESC LIMIT ?"
        params.append(limit)
        cursor = await self.db.execute(query, params)
        return [dict(row) for row in await cursor.fetchall()]

    # ------------------------------------------------------------------ campaigns

    async def upsert_campaign(self, campaign: dict) -> None:
        ts = now_iso()
        async with self._lock:
            await self.db.execute(
                """
                INSERT INTO campaigns
                    (id, name, category, status, discount_pct, budget, starts_at, ends_at,
                     product_ids, notes, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    name = excluded.name, category = excluded.category, status = excluded.status,
                    discount_pct = excluded.discount_pct, budget = excluded.budget,
                    starts_at = excluded.starts_at, ends_at = excluded.ends_at,
                    product_ids = excluded.product_ids, notes = excluded.notes,
                    updated_at = excluded.updated_at
                """,
                (
                    campaign["id"],
                    campaign.get("name"),
                    campaign.get("category"),
                    campaign.get("status"),
                    campaign.get("discount_pct"),
                    campaign.get("budget"),
                    campaign.get("starts_at"),
                    campaign.get("ends_at"),
                    json.dumps(campaign.get("product_ids") or [], ensure_ascii=False),
                    campaign.get("notes"),
                    ts,
                    ts,
                ),
            )
            await self.db.commit()

    def _campaign_row(self, row: aiosqlite.Row) -> dict:
        campaign = dict(row)
        campaign["product_ids"] = json.loads(campaign.get("product_ids") or "[]")
        return campaign

    async def get_campaign(self, campaign_id: str) -> dict | None:
        cursor = await self.db.execute("SELECT * FROM campaigns WHERE id = ?", (campaign_id,))
        row = await cursor.fetchone()
        return self._campaign_row(row) if row else None

    async def list_campaigns(self, status: str | None = None) -> list[dict]:
        if status and status != "all":
            cursor = await self.db.execute(
                "SELECT * FROM campaigns WHERE status = ? ORDER BY updated_at DESC", (status,)
            )
        else:
            cursor = await self.db.execute("SELECT * FROM campaigns ORDER BY updated_at DESC")
        return [self._campaign_row(row) for row in await cursor.fetchall()]

    async def count_active_campaigns(self) -> int:
        cursor = await self.db.execute("SELECT COUNT(*) AS n FROM campaigns WHERE status = 'active'")
        row = await cursor.fetchone()
        return int(row["n"]) if row else 0
