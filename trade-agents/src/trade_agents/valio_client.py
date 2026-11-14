"""Thin client for the Valio Aimo mock API (pricing, availability, file downloads).

Notes from the API docs that shape this client:
- Every /api call needs the `X-API-Key` header.
- /api/price and /api/availability take 1-1000 product codes per request.
- Rate limits are undisclosed, and the docs ask not to poll the whole catalogue:
  narrow candidates down from static product data first. Hence the short TTL cache.
"""

from __future__ import annotations

import time
from collections.abc import Iterable
from datetime import UTC, date
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any, Literal

import httpx

from .config import Settings, get_settings

MAX_BATCH = 1000
RATE_LIMIT_COOLDOWN = 30.0
DownloadKind = Literal["products", "delivery-reliability", "carbon-footprint"]


class ValioApiError(RuntimeError):
    pass


class ValioClient:
    def __init__(
        self,
        settings: Settings | None = None,
        *,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        if not self.settings.valio_api_key:
            raise ValioApiError("VALIO_API_KEY is not set (see trade-agents/.env.example)")
        self._http = httpx.Client(
            base_url=self.settings.valio_base_url,
            headers={"X-API-Key": self.settings.valio_api_key},
            timeout=30,
            follow_redirects=True,
            transport=transport,
        )
        self._cooldown_until = 0.0
        self._cache: dict[tuple[str, str, str, str], tuple[float, dict[str, Any]]] = {}

    # -- live data ---------------------------------------------------------

    def ping(self) -> bool:
        r = self._request("GET", "/api/ping")
        try:
            return r.status_code == 200 and r.json().get("status") == "ok"
        finally:
            r.close()

    def availability(
        self, product_ids: Iterable[str], requested_date: date, customer: str | None = None,
        *, force_refresh: bool = False,
    ) -> dict[str, dict[str, Any]]:
        """Raw availability; committing a basket must bypass cached stock."""
        return self._query("availability", product_ids, requested_date, customer,
                           force_refresh=force_refresh)

    def prices(
        self, product_ids: Iterable[str], requested_date: date, customer: str | None = None
    ) -> dict[str, dict[str, Any]]:
        """Raw price record per product id (price is per sales unit)."""
        return self._query("price", product_ids, requested_date, customer)

    def _query(
        self,
        endpoint: str,
        product_ids: Iterable[str],
        requested_date: date,
        customer: str | None,
        *, force_refresh: bool = False,
    ) -> dict[str, dict[str, Any]]:
        customer = customer or self.settings.valio_customer
        day = requested_date.isoformat()
        ids = list(dict.fromkeys(str(p) for p in product_ids))
        now = time.monotonic()
        out: dict[str, dict[str, Any]] = {}
        missing: list[str] = []
        for pid in ids:
            if force_refresh:
                # Evict first: an empty fresh response must not resurrect old stock.
                self._cache.pop((endpoint, customer, day, pid), None)
            hit = self._cache.get((endpoint, customer, day, pid))
            if hit and now - hit[0] < self.settings.api_cache_seconds:
                out[pid] = hit[1]
            else:
                missing.append(pid)
        for i in range(0, len(missing), MAX_BATCH):
            chunk = missing[i : i + MAX_BATCH]
            body = {"customer": customer, "requestedDate": day, "products": chunk}
            data = self._post(f"/api/{endpoint}", body)
            stamp = time.monotonic()
            for rec in data.get("products", []):
                pid = str(rec["productId"])
                out[pid] = rec
                self._cache[(endpoint, customer, day, pid)] = (stamp, rec)
        return out

    @staticmethod
    def _retry_after(value: str | None) -> float:
        """HTTP Retry-After supports delta-seconds or an HTTP-date, using real time."""
        if not value:
            return 0.0
        if value.strip().isdigit():
            return float(value.strip())
        try:
            at = parsedate_to_datetime(value)
            if at.tzinfo is None:
                at = at.replace(tzinfo=UTC)
            return max(0.0, at.timestamp() - time.time())
        except (ValueError, TypeError, OverflowError):
            return 0.0

    def _request(self, method: str, path: str, *, stream: bool = False,
                 **kwargs: Any) -> httpx.Response:
        """Bounded retries; a 429 cooldown applies to subsequent calls on this client too."""
        for attempt in range(4):
            while (remaining := self._cooldown_until - time.monotonic()) > 0:
                time.sleep(remaining)
            delay = 2.0 ** attempt
            try:
                request = self._http.build_request(method, path, **kwargs)
                r = self._http.send(request, stream=stream)
            except httpx.TransportError as e:
                if attempt == 3:
                    raise ValioApiError(f"{path}: {e}") from e
            else:
                if r.status_code == 200:
                    return r
                retry_after = self._retry_after(r.headers.get("retry-after"))
                status = r.status_code
                r.close()
                if status == 429:
                    self._cooldown_until = max(self._cooldown_until, time.monotonic()
                                               + max(RATE_LIMIT_COOLDOWN, retry_after))
                if status not in (429, 500, 502, 503, 504) or attempt == 3:
                    raise ValioApiError(f"{path}: HTTP {status}")
                if status == 429:
                    continue  # wait at the top, also after a final 429 on the next call
                delay = max(delay, retry_after)
            time.sleep(delay)
        raise ValioApiError(f"{path}: retries exhausted")  # pragma: no cover

    def _post(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        r = self._request("POST", path, json=body)
        try:
            return r.json()
        finally:
            r.close()

    # -- static files ------------------------------------------------------

    def download(self, kind: DownloadKind, dest: Path) -> Path:
        """Download one of the static data files (follows the API's redirect)."""
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_suffix(dest.suffix + ".part")
        r = self._request("GET", f"/api/{kind}/download", stream=True)
        try:
            with tmp.open("wb") as f:
                for chunk in r.iter_bytes(1 << 20):
                    f.write(chunk)
        finally:
            r.close()
        tmp.replace(dest)
        return dest

    def close(self) -> None:
        self._http.close()
