"""Settings loaded from the environment (and `trade-agents/.env`)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(PROJECT_ROOT / ".env")

TZ = ZoneInfo("Europe/Helsinki")


def local_mode() -> bool:
    """Local storage is an explicit choice; frozen demo time also isolates state."""
    return os.getenv("TTA_LOCAL") == "1" or bool(os.getenv("TTA_DEMO_CLOCK", "").strip())


@dataclass(frozen=True)
class Settings:
    valio_base_url: str = field(
        default_factory=lambda: os.getenv("VALIO_BASE_URL", "https://junction26.valioaimo.fi")
    )
    valio_api_key: str = field(default_factory=lambda: os.getenv("VALIO_API_KEY", ""))
    # In the mock, customer does not change prices; in production it identifies the buyer org.
    valio_customer: str = field(default_factory=lambda: os.getenv("VALIO_CUSTOMER", "junction-demo"))
    data_dir: Path = field(
        default_factory=lambda: Path(os.getenv("TTA_DATA_DIR", PROJECT_ROOT / "data"))
    )
    # How long a quoted price stays locked for an order (minutes).
    offer_ttl_minutes: int = field(default_factory=lambda: int(os.getenv("TTA_OFFER_TTL_MIN", "60")))
    # How long a price lock is held while an order waits for human approval (hours).
    approval_hold_hours: int = field(
        default_factory=lambda: int(os.getenv("TTA_APPROVAL_HOLD_H", "24"))
    )
    # Cache for live price/availability answers (seconds); the API has undisclosed rate limits.
    api_cache_seconds: int = field(default_factory=lambda: int(os.getenv("TTA_API_CACHE_S", "120")))
    # Valio asks to query live prices/availability only when needed, never for all products:
    # at most this many products per live call (narrow with the static catalogue first).
    max_live_products: int = field(
        default_factory=lambda: int(os.getenv("TTA_MAX_LIVE_PRODUCTS", "40"))
    )

    def __post_init__(self) -> None:
        # Invalid frozen clocks fail at startup, before creating any storage or keys.
        from .clock import demo_time

        demo_time()

    @property
    def catalog_db(self) -> Path:
        return self.data_dir / "catalog.sqlite"

    @property
    def state_dir(self) -> Path:
        return self.data_dir / "local-demo" if local_mode() else self.data_dir

    @property
    def state_db(self) -> Path:
        return self.state_dir / "state.sqlite"

    @property
    def keys_dir(self) -> Path:
        """Valio's signing key (private, gitignored)."""
        return self.state_dir / "keys"

    @property
    def buyers_dir(self) -> Path:
        """Buyer key registry (public JWKS per buyer) and demo buyer keys (gitignored)."""
        return self.state_dir / "buyers"

    @property
    def raw_dir(self) -> Path:
        return self.data_dir / "raw"


def get_settings() -> Settings:
    return Settings()
