"""Gateway state in PostgreSQL (e.g. Aiven for PostgreSQL), same contract as `store.Store`.

Selected by `TTA_STORE_URL` or `AIVEN_PG_URL` (`postgres://…?sslmode=require`), optionally
in a dedicated schema (`TTA_STORE_SCHEMA`, e.g. a hosted demo's `demo_20261114`); see
`store.open_store`. Differences from SQLite that matter:

- `lock` is cross-process: entering it opens a transaction and takes a transaction-level
  advisory lock, so the gateway's "read cumulative spend -> decide -> insert order" section
  is serialised across every gateway process sharing the database, not just threads.
- Audit appends take their own advisory lock, so the hash chain stays linear with several
  writers.
- Inside `lock`, statements commit when the block ends even if Python raises (like SQLite,
  where every statement commits at once, e.g. `order.expired` before the GatewayError);
  only a database error rolls the block back.
- A dropped connection (Aiven maintenance, idle timeout) is reopened on the next call
  outside a locked block; `sslmode=require` is added when the URL has no sslmode, except
  for localhost.
- Columns keep the SQLite types (JSON and timestamps as TEXT) so hashes, ISO timestamp
  comparisons and `json.loads` round trips are identical; JSON is cast to jsonb only in
  the cumulative-spend query.

The connection URL is never logged.
"""

from __future__ import annotations

import json
import threading
import uuid
from datetime import datetime
from typing import Any
from urllib.parse import urlsplit

from .store import StoreConnectionError, _hash, now

LOCK_ORDERS = 0x7474_0001  # pg_advisory_xact_lock keys (bigint)
LOCK_AUDIT = 0x7474_0002
LOCK_SCHEMA = 0x7474_0003
ORDER_COLUMNS = ("idempotency_key", "buyer_id", "agent_id", "mandate_id", "status",
                 "total_eur", "order_number", "data", "hold_until")

SCHEMA = """
CREATE TABLE IF NOT EXISTS offers (
  offer_id TEXT PRIMARY KEY, product_id TEXT, data TEXT,
  created_at TEXT, expires_at TEXT
);
CREATE TABLE IF NOT EXISTS orders (
  order_id TEXT PRIMARY KEY, idempotency_key TEXT,
  buyer_id TEXT, agent_id TEXT, mandate_id TEXT,
  status TEXT, total_eur DOUBLE PRECISION, order_number TEXT,
  data TEXT, created_at TEXT, updated_at TEXT, hold_until TEXT,
  UNIQUE (buyer_id, idempotency_key)
);
CREATE TABLE IF NOT EXISTS audit (
  seq BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY, ts TEXT, actor TEXT, event TEXT,
  subject TEXT, summary TEXT, data TEXT, prev_hash TEXT, hash TEXT
);
CREATE INDEX IF NOT EXISTS audit_subject ON audit(subject);
CREATE INDEX IF NOT EXISTS orders_buyer ON orders(buyer_id, mandate_id, created_at);
CREATE TABLE IF NOT EXISTS valio_reviews (
  order_id TEXT PRIMARY KEY, review TEXT, updated_at TEXT
);
"""


def with_sslmode(url: str) -> str:
    """Aiven requires TLS: add sslmode=require unless the URL sets it or is local."""
    if "sslmode=" in url or urlsplit(url).hostname in ("localhost", "127.0.0.1", "::1"):
        return url
    return url + ("&" if "?" in url else "?") + "sslmode=require"


class _PgLock:
    """Re-entrant in-process lock that, on the outermost entry, opens a transaction holding
    a transaction-level advisory lock (released on commit/rollback)."""

    def __init__(self, store: PgStore) -> None:
        self.store = store
        self._rlock = threading.RLock()
        self._depth = 0
        self._tx: Any = None

    def __enter__(self) -> _PgLock:
        self._rlock.acquire()
        self._depth += 1
        if self._depth == 1:
            try:
                self.store._ensure()
                self._tx = self.store.con.transaction()
                self._tx.__enter__()
                self.store.con.execute("SELECT pg_advisory_xact_lock(%s)", (LOCK_ORDERS,))
            except BaseException:
                self._depth -= 1
                self._rlock.release()
                raise
        return self

    def __exit__(self, *exc: Any) -> None:
        try:
            if self._depth == 1 and self._tx is not None:
                import psycopg

                tx, self._tx = self._tx, None
                # commit unless the database itself failed (SQLite-like statement semantics)
                tx.__exit__(*(exc if isinstance(exc[1], psycopg.Error) else (None,) * 3))
        finally:
            self._depth -= 1
            self._rlock.release()

    # threading.RLock compatibility
    def acquire(self) -> bool:
        self.__enter__()
        return True

    def release(self) -> None:
        self.__exit__(None, None, None)


class PgStore:
    def __init__(self, url: str, *, schema: str | None = None) -> None:
        self._url = with_sslmode(url)
        self.schema = schema
        self.con: Any = None
        self._connect()
        with self.con.transaction():
            self.con.execute("SELECT pg_advisory_xact_lock(%s)", (LOCK_ORDERS,))
            for stmt in filter(str.strip, SCHEMA.split(";")):
                self.con.execute(stmt)
        self.lock = _PgLock(self)

    def __repr__(self) -> str:  # never show the URL
        return "PgStore()"

    def close(self) -> None:
        self.con.close()

    def _connect(self) -> None:
        import psycopg
        from psycopg import sql
        from psycopg.rows import dict_row

        try:
            self.con = psycopg.connect(self._url, autocommit=True, row_factory=dict_row,
                                       connect_timeout=3)
        except psycopg.OperationalError:
            raise StoreConnectionError(
                "PostgreSQL connection failed (connect timeout: 3 seconds per host). "
                "Check the database and network. No SQLite fallback was performed; "
                "use --local or TTA_LOCAL=1 explicitly for isolated demo storage."
            ) from None
        if self.schema:
            # Serialise schema creation: concurrent CREATE SCHEMA IF NOT EXISTS from two
            # containers can otherwise fail with a unique violation in the catalogue.
            with self.con.transaction():
                self.con.execute("SELECT pg_advisory_xact_lock(%s)", (LOCK_SCHEMA,))
                self.con.execute(sql.SQL("CREATE SCHEMA IF NOT EXISTS {}").format(
                    sql.Identifier(self.schema)))
            self.con.execute(sql.SQL("SET search_path TO {}").format(
                sql.Identifier(self.schema)))

    def _in_tx(self) -> bool:
        return getattr(self, "lock", None) is not None and self.lock._depth > 0

    def _ensure(self) -> None:
        """Reopen a closed or broken connection (never inside a locked block)."""
        if (self.con.closed or self.con.broken) and not self._in_tx():
            self._connect()

    def _q(self, query: str, args: tuple | list = ()) -> list[dict[str, Any]]:
        import psycopg

        with self.lock._rlock:
            self._ensure()
            try:
                cur = self.con.execute(query, args)
            except psycopg.OperationalError:
                if self._in_tx() or not (self.con.closed or self.con.broken):
                    raise
                self._connect()  # dropped while idle: one retry on a fresh connection
                cur = self.con.execute(query, args)
            return cur.fetchall() if cur.description else []

    # -- offers ------------------------------------------------------------

    def save_offer(self, product_id: str, data: dict[str, Any], expires_at: datetime) -> str:
        offer_id = "of_" + uuid.uuid4().hex[:12]
        self._q("INSERT INTO offers VALUES (%s,%s,%s,%s,%s)",
                (offer_id, product_id, json.dumps(data), now().isoformat(),
                 expires_at.isoformat()))
        return offer_id

    def get_offer(self, offer_id: str) -> tuple[dict[str, Any], datetime] | None:
        rows = self._q("SELECT * FROM offers WHERE offer_id = %s", (offer_id,))
        if not rows:
            return None
        return json.loads(rows[0]["data"]), datetime.fromisoformat(rows[0]["expires_at"])

    # -- orders ------------------------------------------------------------

    def order_by_key(self, buyer_id: str, idempotency_key: str) -> dict[str, Any] | None:
        rows = self._q("SELECT * FROM orders WHERE buyer_id = %s AND idempotency_key = %s",
                       (buyer_id, idempotency_key))
        return self._order(rows[0] if rows else None)

    def get_order(self, order_id: str) -> dict[str, Any] | None:
        rows = self._q("SELECT * FROM orders WHERE order_id = %s", (order_id,))
        return self._order(rows[0] if rows else None)

    def list_orders(self, status: str | None = None, buyer_id: str | None = None) -> list[dict]:
        q, args = "SELECT * FROM orders WHERE 1=1", []
        if status:
            q += " AND status = %s"
            args.append(status)
        if buyer_id:
            q += " AND buyer_id = %s"
            args.append(buyer_id)
        return [self._order(r) for r in self._q(q + " ORDER BY created_at DESC", args)]

    def create_order(self, rec: dict[str, Any]) -> dict[str, Any]:
        order_id = "ord_" + uuid.uuid4().hex[:12]
        ts = now().isoformat()
        with self.lock._rlock:
            self._ensure()
        with self.lock._rlock, self.con.transaction():
            self.con.execute(
                "INSERT INTO orders VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (order_id, rec["idempotency_key"], rec["buyer_id"], rec["agent_id"],
                 rec["mandate_id"], rec["status"], rec["total_eur"], rec.get("order_number"),
                 json.dumps(rec["data"]), ts, ts, rec.get("hold_until")))
        return self.get_order(order_id)  # type: ignore[return-value]

    def update_order(self, order_id: str, **fields: Any) -> dict[str, Any]:
        unknown = set(fields) - set(ORDER_COLUMNS)
        if unknown:
            raise ValueError(f"unknown order field(s) {sorted(unknown)}")
        if "data" in fields:
            fields["data"] = json.dumps(fields["data"])
        fields["updated_at"] = now().isoformat()
        sets = ", ".join(f"{k} = %s" for k in fields)
        self._q(f"UPDATE orders SET {sets} WHERE order_id = %s", (*fields.values(), order_id))
        return self.get_order(order_id)  # type: ignore[return-value]

    def autonomous_spend_since(self, buyer_id: str, mandate_id: str, since: datetime,
                               *, wall_since: datetime | None = None) -> float:
        column = ("COALESCE(data::jsonb ->> 'received_at_wall', created_at)"
                  if wall_since else "created_at")
        rows = self._q(
            "SELECT COALESCE(SUM(total_eur), 0) AS s FROM orders WHERE buyer_id = %s AND "
            f"mandate_id = %s AND {column} >= %s AND status = 'placed' AND "
            "COALESCE(CASE WHEN jsonb_typeof(data::jsonb -> 'approvals') = 'array' "
            "THEN jsonb_array_length(data::jsonb -> 'approvals') END, 0) = 0 AND "
            "COALESCE(data::jsonb -> 'cart_confirmation', 'null'::jsonb) = 'null'::jsonb",
            (buyer_id, mandate_id, (wall_since or since).isoformat()))
        return round(float(rows[0]["s"]), 2)

    def next_order_number(self) -> str:
        n = self._q("SELECT COUNT(*) AS n FROM orders WHERE order_number IS NOT NULL")[0]["n"]
        return f"VA-{now():%Y%m%d}-{n + 1:05d}"

    @staticmethod
    def _order(r: dict[str, Any] | None) -> dict[str, Any] | None:
        if not r:
            return None
        d = dict(r)
        d["data"] = json.loads(d["data"])
        return d

    # -- Valio's reviews ------------------------------------------------------

    def save_valio_review(self, order_id: str, review: dict[str, Any]) -> None:
        self._q("INSERT INTO valio_reviews VALUES (%s,%s,%s) ON CONFLICT (order_id) DO UPDATE "
                "SET review = EXCLUDED.review, updated_at = EXCLUDED.updated_at",
                (order_id, json.dumps(review, ensure_ascii=False, default=str),
                 now().isoformat()))

    def get_valio_review(self, order_id: str) -> dict[str, Any] | None:
        rows = self._q("SELECT review FROM valio_reviews WHERE order_id = %s", (order_id,))
        return json.loads(rows[0]["review"]) if rows else None

    # -- audit -------------------------------------------------------------

    def audit(self, actor: str, event: str, subject: str, summary: str,
              data: dict[str, Any] | None = None) -> dict[str, Any]:
        payload = json.dumps(data or {}, sort_keys=True, ensure_ascii=False, default=str)
        with self.lock._rlock:
            self._ensure()
        with self.lock._rlock, self.con.transaction():
            self.con.execute("SELECT pg_advisory_xact_lock(%s)", (LOCK_AUDIT,))
            last = self.con.execute(
                "SELECT hash FROM audit ORDER BY seq DESC LIMIT 1").fetchone()
            prev = last["hash"] if last else "genesis"
            ts = now().isoformat()
            h = _hash(prev, ts, actor, event, subject, summary, payload)
            seq = self.con.execute(
                "INSERT INTO audit (ts, actor, event, subject, summary, data, prev_hash, hash) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s) RETURNING seq",
                (ts, actor, event, subject, summary, payload, prev, h)).fetchone()["seq"]
        return {"seq": seq, "ts": ts, "actor": actor, "event": event,
                "subject": subject, "summary": summary, "hash": h}

    def audit_log(self, subject: str | None = None, limit: int = 200) -> list[dict[str, Any]]:
        if subject:
            rows = self._q("SELECT * FROM audit WHERE subject = %s ORDER BY seq LIMIT %s",
                           (subject, limit))
        else:
            rows = self._q("SELECT * FROM audit ORDER BY seq DESC LIMIT %s", (limit,))[::-1]
        return [{**r, "data": json.loads(r["data"])} for r in rows]

    def verify_audit(self) -> tuple[bool, int | None]:
        prev = "genesis"
        for r in self._q("SELECT * FROM audit ORDER BY seq"):
            h = _hash(prev, r["ts"], r["actor"], r["event"], r["subject"], r["summary"], r["data"])
            if r["prev_hash"] != prev or r["hash"] != h:
                return False, r["seq"]
            prev = r["hash"]
        return True, None
