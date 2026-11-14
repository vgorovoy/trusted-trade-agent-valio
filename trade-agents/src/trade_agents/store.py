"""Gateway state in SQLite: price-locked offers, orders and the audit trail.

The audit trail is append-only and hash-chained (each entry stores the hash of the
previous one), so buyer and Valio can both verify that no decision was edited later.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import threading
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol

from .clock import current_time
from .config import TZ, local_mode

SCHEMA = """
CREATE TABLE IF NOT EXISTS offers (
  offer_id TEXT PRIMARY KEY, product_id TEXT, data TEXT,
  created_at TEXT, expires_at TEXT
);
CREATE TABLE IF NOT EXISTS orders (
  order_id TEXT PRIMARY KEY, idempotency_key TEXT,
  buyer_id TEXT, agent_id TEXT, mandate_id TEXT,
  status TEXT, total_eur REAL, order_number TEXT,
  data TEXT, created_at TEXT, updated_at TEXT, hold_until TEXT,
  UNIQUE (buyer_id, idempotency_key)
);
CREATE TABLE IF NOT EXISTS audit (
  seq INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, actor TEXT, event TEXT,
  subject TEXT, summary TEXT, data TEXT, prev_hash TEXT, hash TEXT
);
CREATE INDEX IF NOT EXISTS audit_subject ON audit(subject);
CREATE TABLE IF NOT EXISTS valio_reviews (
  order_id TEXT PRIMARY KEY, review TEXT, updated_at TEXT
);
"""


def now() -> datetime:
    return current_time(datetime.now(TZ))


def _hash(prev: str, ts: str, actor: str, event: str, subject: str, summary: str, data: str) -> str:
    h = hashlib.sha256()
    for part in (prev, ts, actor, event, subject, summary, data):
        h.update(part.encode())
        h.update(b"\x1f")
    return h.hexdigest()


class StoreConnectionError(RuntimeError):
    """Configured database unavailable; no fallback or credentials in the message."""


class Store:
    def __init__(self, db_path: Path) -> None:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self.con = sqlite3.connect(db_path, check_same_thread=False)
        self.con.row_factory = sqlite3.Row
        self._migrate()
        self.con.executescript(SCHEMA)
        # Serialises writes, and lets the gateway check-and-insert an order atomically.
        self.lock = threading.RLock()

    def _migrate(self) -> None:
        """Before P2 the idempotency key was unique across all buyers; now per buyer."""
        r = self.con.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'orders'").fetchone()
        if r and "idempotency_key TEXT UNIQUE" in r[0]:
            self.con.executescript(
                "ALTER TABLE orders RENAME TO orders_v1;" + SCHEMA +
                "INSERT INTO orders SELECT * FROM orders_v1; DROP TABLE orders_v1;")

    # -- offers ------------------------------------------------------------

    def save_offer(self, product_id: str, data: dict[str, Any], expires_at: datetime) -> str:
        offer_id = "of_" + uuid.uuid4().hex[:12]
        with self.lock:
            self.con.execute(
                "INSERT INTO offers VALUES (?,?,?,?,?)",
                (offer_id, product_id, json.dumps(data), now().isoformat(), expires_at.isoformat()),
            )
            self.con.commit()
        return offer_id

    def get_offer(self, offer_id: str) -> tuple[dict[str, Any], datetime] | None:
        r = self.con.execute("SELECT * FROM offers WHERE offer_id = ?", (offer_id,)).fetchone()
        if not r:
            return None
        return json.loads(r["data"]), datetime.fromisoformat(r["expires_at"])

    # -- orders ------------------------------------------------------------

    def order_by_key(self, buyer_id: str, idempotency_key: str) -> dict[str, Any] | None:
        """Idempotency keys are scoped to the buyer: another buyer's key finds nothing."""
        r = self.con.execute(
            "SELECT * FROM orders WHERE buyer_id = ? AND idempotency_key = ?",
            (buyer_id, idempotency_key),
        ).fetchone()
        return self._order(r)

    def get_order(self, order_id: str) -> dict[str, Any] | None:
        r = self.con.execute("SELECT * FROM orders WHERE order_id = ?", (order_id,)).fetchone()
        return self._order(r)

    def list_orders(self, status: str | None = None, buyer_id: str | None = None) -> list[dict]:
        q, args = "SELECT * FROM orders WHERE 1=1", []
        if status:
            q += " AND status = ?"
            args.append(status)
        if buyer_id:
            q += " AND buyer_id = ?"
            args.append(buyer_id)
        rows = self.con.execute(q + " ORDER BY created_at DESC", args).fetchall()
        return [self._order(r) for r in rows]

    def create_order(self, rec: dict[str, Any]) -> dict[str, Any]:
        order_id = "ord_" + uuid.uuid4().hex[:12]
        ts = now().isoformat()
        with self.lock:
            self.con.execute(
                "INSERT INTO orders VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (order_id, rec["idempotency_key"], rec["buyer_id"], rec["agent_id"],
                 rec["mandate_id"], rec["status"], rec["total_eur"], rec.get("order_number"),
                 json.dumps(rec["data"]), ts, ts, rec.get("hold_until")),
            )
            self.con.commit()
        return self.get_order(order_id)  # type: ignore[return-value]

    def update_order(self, order_id: str, **fields: Any) -> dict[str, Any]:
        if "data" in fields:
            fields["data"] = json.dumps(fields["data"])
        fields["updated_at"] = now().isoformat()
        sets = ", ".join(f"{k} = ?" for k in fields)
        with self.lock:
            self.con.execute(f"UPDATE orders SET {sets} WHERE order_id = ?",
                             (*fields.values(), order_id))
            self.con.commit()
        return self.get_order(order_id)  # type: ignore[return-value]

    def autonomous_spend_since(self, buyer_id: str, mandate_id: str, since: datetime) -> float:
        """What the agent committed on its own under one mandate since `since`: placed
        orders that no human approved or confirmed."""
        r = self.con.execute(
            "SELECT COALESCE(SUM(total_eur), 0) FROM orders WHERE buyer_id = ? AND "
            "mandate_id = ? AND created_at >= ? AND status = 'placed' AND "
            "COALESCE(json_array_length(json_extract(data, '$.approvals')), 0) = 0 AND "
            "json_extract(data, '$.cart_confirmation') IS NULL",
            (buyer_id, mandate_id, since.isoformat()),
        ).fetchone()
        return round(float(r[0]), 2)

    def next_order_number(self) -> str:
        n = self.con.execute(
            "SELECT COUNT(*) FROM orders WHERE order_number IS NOT NULL"
        ).fetchone()[0]
        return f"VA-{now():%Y%m%d}-{n + 1:05d}"

    @staticmethod
    def _order(r: sqlite3.Row | None) -> dict[str, Any] | None:
        if not r:
            return None
        d = dict(r)
        d["data"] = json.loads(d["data"])
        return d

    # -- Valio's reviews (kept outside order.data: never races with the buyer flow) ----

    def save_valio_review(self, order_id: str, review: dict[str, Any]) -> None:
        with self.lock:
            self.con.execute(
                "INSERT INTO valio_reviews VALUES (?,?,?) ON CONFLICT(order_id) DO UPDATE SET "
                "review = excluded.review, updated_at = excluded.updated_at",
                (order_id, json.dumps(review, ensure_ascii=False, default=str),
                 now().isoformat()))
            self.con.commit()

    def get_valio_review(self, order_id: str) -> dict[str, Any] | None:
        r = self.con.execute("SELECT review FROM valio_reviews WHERE order_id = ?",
                             (order_id,)).fetchone()
        return json.loads(r[0]) if r else None

    # -- audit -------------------------------------------------------------

    def audit(self, actor: str, event: str, subject: str, summary: str,
              data: dict[str, Any] | None = None) -> dict[str, Any]:
        payload = json.dumps(data or {}, sort_keys=True, ensure_ascii=False, default=str)
        with self.lock:
            last = self.con.execute("SELECT hash FROM audit ORDER BY seq DESC LIMIT 1").fetchone()
            prev = last[0] if last else "genesis"
            ts = now().isoformat()
            h = _hash(prev, ts, actor, event, subject, summary, payload)
            cur = self.con.execute(
                "INSERT INTO audit (ts, actor, event, subject, summary, data, prev_hash, hash) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (ts, actor, event, subject, summary, payload, prev, h),
            )
            self.con.commit()
        return {"seq": cur.lastrowid, "ts": ts, "actor": actor, "event": event,
                "subject": subject, "summary": summary, "hash": h}

    def audit_log(self, subject: str | None = None, limit: int = 200) -> list[dict[str, Any]]:
        if subject:
            rows = self.con.execute(
                "SELECT * FROM audit WHERE subject = ? ORDER BY seq LIMIT ?", (subject, limit)
            ).fetchall()
        else:
            rows = self.con.execute(
                "SELECT * FROM audit ORDER BY seq DESC LIMIT ?", (limit,)
            ).fetchall()[::-1]
        return [{**dict(r), "data": json.loads(r["data"])} for r in rows]

    def verify_audit(self) -> tuple[bool, int | None]:
        """Recompute the hash chain. Returns (ok, first broken seq)."""
        prev = "genesis"
        for r in self.con.execute("SELECT * FROM audit ORDER BY seq"):
            h = _hash(prev, r["ts"], r["actor"], r["event"], r["subject"], r["summary"], r["data"])
            if r["prev_hash"] != prev or r["hash"] != h:
                return False, r["seq"]
            prev = r["hash"]
        return True, None


# -- the contract the gateway relies on, and backend selection ------------------------------

class StateStore(Protocol):
    """What the gateway needs from its state store (SQLite `Store`, Postgres `PgStore`).

    - offers: `save_offer` / `get_offer` (price locks with expiry);
    - orders: `create_order` (UNIQUE(buyer_id, idempotency_key)), `order_by_key` (scoped to
      the buyer), `get_order`, `list_orders` (newest first), `update_order`;
    - `autonomous_spend_since`: placed orders under a mandate with no human approval or
      cart confirmation; `next_order_number`;
    - audit: `audit` appends to one hash chain, `audit_log`, `verify_audit`;
    - Valio's advisory reviews: `save_valio_review` (upsert) / `get_valio_review`, stored
      apart from `order.data` so they never overwrite approvals or receipts;
    - `lock`: re-entrant context manager; the gateway holds it while it reads the
      cumulative spend, decides and inserts the order.
    """

    lock: Any

    def save_offer(self, product_id: str, data: dict[str, Any], expires_at: datetime) -> str: ...
    def get_offer(self, offer_id: str) -> tuple[dict[str, Any], datetime] | None: ...
    def order_by_key(self, buyer_id: str, idempotency_key: str) -> dict[str, Any] | None: ...
    def get_order(self, order_id: str) -> dict[str, Any] | None: ...
    def list_orders(self, status: str | None = None,
                    buyer_id: str | None = None) -> list[dict]: ...
    def create_order(self, rec: dict[str, Any]) -> dict[str, Any]: ...
    def update_order(self, order_id: str, **fields: Any) -> dict[str, Any]: ...
    def autonomous_spend_since(self, buyer_id: str, mandate_id: str,
                               since: datetime) -> float: ...
    def next_order_number(self) -> str: ...
    def audit(self, actor: str, event: str, subject: str, summary: str,
              data: dict[str, Any] | None = None) -> dict[str, Any]: ...
    def audit_log(self, subject: str | None = None, limit: int = 200) -> list[dict[str, Any]]: ...
    def verify_audit(self) -> tuple[bool, int | None]: ...
    def save_valio_review(self, order_id: str, review: dict[str, Any]) -> None: ...
    def get_valio_review(self, order_id: str) -> dict[str, Any] | None: ...


def store_url() -> str | None:
    """Postgres URL from `TTA_STORE_URL` or `AIVEN_PG_URL`; never log it."""
    if local_mode():
        return None
    url = os.getenv("TTA_STORE_URL") or os.getenv("AIVEN_PG_URL") or ""
    return url if url.startswith(("postgres://", "postgresql://")) else None


def open_store(db_path: Path) -> StateStore:
    """Postgres (e.g. Aiven) when a URL is configured, else SQLite at `db_path`."""
    url = store_url()
    if url:
        from .store_pg import PgStore

        return PgStore(url)
    return Store(db_path)
