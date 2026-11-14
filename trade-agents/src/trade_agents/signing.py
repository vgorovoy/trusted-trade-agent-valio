"""Ed25519 signatures for the trust protocol (JWS compact, alg EdDSA, RFC 8037).

Documents, in the vocabulary of Google AP2 / Mastercard Verifiable Intent:

| Document                | Signed by | JWS `typ`                   | Payload                    |
|-------------------------|-----------|-----------------------------|----------------------------|
| Mandate (Intent)        | buyer     | valio.mandate.v1            | detached: the mandate JSON |
| Signed Offer            | Valio     | valio.offer.v1              | attached: offer claims     |
| Cart confirmation       | buyer     | valio.cart_confirmation.v1  | attached                   |
| Order Receipt           | Valio     | valio.receipt.v1            | attached                   |
| Agent Passport          | Valio     | valio.agent_passport.v1     | attached                   |

Payloads are canonical JSON: keys sorted, no whitespace, UTF-8. A *detached* JWS
(`header..signature`, RFC 7515 appendix F) is used for the mandate because the signed
document travels next to it; the verifier re-serialises the mandate canonically. All
other documents carry their claims inside the JWS, so an agent can decode and check them
with nothing but the issuer's JWKS (`GET /.well-known/valio-agent-keys`).

Keys: kid = RFC 7638 JWK thumbprint. Valio's private key lives in `data/keys/`, demo buyer
keys in `data/buyers/` (both gitignored); the buyer key registry is `data/buyers/<id>.json`.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

from .clock import current_time
from .config import TZ

ISSUER = "valio-aimo"
TYP_MANDATE = "valio.mandate.v1"
TYP_OFFER = "valio.offer.v1"
TYP_CART = "valio.cart_confirmation.v1"
TYP_RECEIPT = "valio.receipt.v1"
TYP_PASSPORT = "valio.agent_passport.v1"


class SignatureError(ValueError):
    pass


# -- encoding ------------------------------------------------------------------------

def b64u(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def b64u_decode(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def canonical(obj: Any) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                      default=str).encode()


# -- keys ----------------------------------------------------------------------------

def public_jwk(key: Ed25519PublicKey) -> dict[str, str]:
    raw = key.public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    core = {"crv": "Ed25519", "kty": "OKP", "x": b64u(raw)}
    kid = b64u(hashlib.sha256(canonical(core)).digest())   # RFC 7638 thumbprint
    return {**core, "kid": kid, "use": "sig", "alg": "EdDSA"}


def public_from_jwk(jwk: dict[str, Any]) -> Ed25519PublicKey:
    if jwk.get("kty") != "OKP" or jwk.get("crv") != "Ed25519":
        raise SignatureError("only OKP / Ed25519 keys are supported")
    return Ed25519PublicKey.from_public_bytes(b64u_decode(jwk["x"]))


def kid_of(key: Ed25519PrivateKey | Ed25519PublicKey) -> str:
    pub = key.public_key() if isinstance(key, Ed25519PrivateKey) else key
    return public_jwk(pub)["kid"]


def load_or_create_key(path: Path) -> Ed25519PrivateKey:
    """Load a PEM private key, or create one (file mode 0600) on first use."""
    if path.exists():
        key = serialization.load_pem_private_key(path.read_bytes(), password=None)
        if not isinstance(key, Ed25519PrivateKey):
            raise SignatureError(f"{path} is not an Ed25519 key")
        return key
    path.parent.mkdir(parents=True, exist_ok=True)
    key = Ed25519PrivateKey.generate()
    pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                            serialization.NoEncryption())
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(pem)
    return key


# -- JWS -------------------------------------------------------------------------------

def sign_jws(payload: dict[str, Any] | bytes, key: Ed25519PrivateKey, typ: str, *,
             detached: bool = False) -> str:
    body = payload if isinstance(payload, bytes) else canonical(payload)
    header = b64u(canonical({"alg": "EdDSA", "kid": kid_of(key), "typ": typ}))
    p = b64u(body)
    sig = b64u(key.sign(f"{header}.{p}".encode()))
    return f"{header}..{sig}" if detached else f"{header}.{p}.{sig}"


def verify_jws(token: str, jwks: dict[str, Any], *, typ: str | None = None,
               payload: dict[str, Any] | bytes | None = None) -> dict[str, Any]:
    """Verify a compact JWS against a JWKS and return its claims.

    For a detached JWS pass the document as `payload`; it is serialised canonically.
    Raises SignatureError on any problem.
    """
    try:
        h64, p64, s64 = token.split(".")
        header = json.loads(b64u_decode(h64))
    except (ValueError, AttributeError, TypeError) as e:
        raise SignatureError("malformed JWS") from e
    if not isinstance(header, dict):
        raise SignatureError("malformed JWS: header is not a JSON object")
    if header.get("alg") != "EdDSA":
        raise SignatureError(f"unsupported alg {header.get('alg')}")
    if typ and header.get("typ") != typ:
        raise SignatureError(f"expected typ {typ}, got {header.get('typ')}")
    if payload is not None:
        if p64:
            raise SignatureError("expected a detached JWS")
        p64 = b64u(payload if isinstance(payload, bytes) else canonical(payload))
    elif not p64:
        raise SignatureError("detached JWS needs the signed document")
    keys = [k for k in jwks.get("keys", []) if k.get("kid") == header.get("kid")]
    if not keys:
        raise SignatureError(f"unknown signing key {header.get('kid')}")
    try:
        public_from_jwk(keys[0]).verify(b64u_decode(s64), f"{h64}.{p64}".encode())
    except InvalidSignature as e:
        raise SignatureError("signature does not verify") from e
    except (ValueError, KeyError, TypeError) as e:
        raise SignatureError("malformed JWS or key") from e
    try:
        claims = json.loads(b64u_decode(p64))
    except ValueError as e:
        raise SignatureError("malformed JWS payload") from e
    if not isinstance(claims, dict):
        raise SignatureError("malformed JWS payload: not a JSON object")
    return claims


def decode_unverified(token: str) -> dict[str, Any]:
    """Claims of an attached JWS without checking the signature (for display only)."""
    return json.loads(b64u_decode(token.split(".")[1]))


# -- Valio's signer and the buyer key registry -----------------------------------------

class ValioSigner:
    """Valio's signing key, created once in `data/keys/`. Publishes its JWKS."""

    def __init__(self, keys_dir: Path) -> None:
        self.key = load_or_create_key(keys_dir / "valio-signing.pem")
        self.kid = kid_of(self.key)

    def jwks(self) -> dict[str, Any]:
        return {"keys": [public_jwk(self.key.public_key())]}

    def sign(self, claims: dict[str, Any], typ: str) -> str:
        return sign_jws({"iss": ISSUER, "iat": now_iso(), **claims}, self.key, typ)

    def verify(self, token: str, typ: str) -> dict[str, Any]:
        return verify_jws(token, self.jwks(), typ=typ)


_SAFE_ID = re.compile(r"^[A-Za-z0-9._@-]{1,128}$")


class BuyerRegistry:
    """Public keys of buyer organisations: `data/buyers/<buyer_id>.json` holds a JWKS.

    In production this is the onboarding record of a Valio customer. For the demo,
    `create_demo_keys` also keeps the buyer's private key next to it (gitignored).
    """

    def __init__(self, buyers_dir: Path) -> None:
        self.dir = buyers_dir

    def _path(self, buyer_id: str, suffix: str) -> Path:
        if not _SAFE_ID.match(buyer_id):
            raise SignatureError(f"invalid buyer id {buyer_id!r}")
        return self.dir / f"{buyer_id}{suffix}"

    def jwks(self, buyer_id: str) -> dict[str, Any] | None:
        try:
            path = self._path(buyer_id, ".json")
        except SignatureError:
            return None
        return json.loads(path.read_text()) if path.exists() else None

    def register(self, buyer_id: str, public_key: Ed25519PublicKey) -> dict[str, Any]:
        doc = self.jwks(buyer_id) or {"buyer_id": buyer_id, "keys": []}
        jwk = public_jwk(public_key)
        if all(k["kid"] != jwk["kid"] for k in doc["keys"]):
            doc["keys"].append(jwk)
        path = self._path(buyer_id, ".json")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(doc, indent=2))
        return doc

    def demo_key(self, buyer_id: str) -> Ed25519PrivateKey:
        """The demo buyer's private key (created and registered on first use).

        A buyer that is already registered (public key on file) but whose private key is not
        kept here signs on its own device: refuse instead of minting a new key for it, or the
        demo wallet could sign for any registered buyer."""
        path = self._path(buyer_id, ".key.pem")
        if not path.exists() and self.jwks(buyer_id):
            raise SignatureError(f"buyer {buyer_id} is registered without a demo private key; "
                                 "it signs with its own key")
        key = load_or_create_key(path)
        if all(k.get("kid") != kid_of(key) for k in (self.jwks(buyer_id) or {}).get("keys", [])):
            self.register(buyer_id, key.public_key())
        return key


def now_iso() -> str:
    return current_time(datetime.now(TZ)).isoformat(timespec="seconds")


# -- documents ------------------------------------------------------------------------

def offer_claims(offer: Any, offer_id: str, valid_until: str) -> dict[str, Any]:
    """What Valio commits to in a Signed Offer. `offer` is an `offers.Offer`."""
    d = offer.delivery
    return {
        "offer_id": offer_id, "valid_until": valid_until,
        "product_id": offer.product_id, "name": offer.name,
        "sales_unit": offer.sales_unit, "lot_size": offer.lot_size,
        "currency": "EUR", "unit_price": offer.unit_price,
        "unit_price_with_tax": offer.unit_price_with_tax, "vat_rate": offer.vat_rate,
        "contract_price": offer.contract_price,
        "comparison_price": offer.comparison_price, "comparison_unit": offer.comparison_unit,
        "requested_date": offer.requested_date, "delivery_status": d.status,
        "available_amount": d.available_amount, "earliest_date": d.earliest_date,
        "order_cutoff": d.order_cutoff,
        "origin_country": getattr(offer, "origin_country", None),
        "claims": offer.claims, "labels": offer.labels,
        "contains_allergens": offer.contains_allergens,
    }


# claim -> where the same value sits in a quote result or a plan line
_OFFER_FIELDS = {
    "offer_id": ("offer_id",), "product_id": ("product_id",),
    "valid_until": ("valid_until",), "unit_price": ("unit_price", "unit_price_eur"),
    "earliest_date": ("earliest_date", "delivery.earliest_date"),
    "order_cutoff": ("order_cutoff", "delivery.order_cutoff"),
    "delivery_status": ("delivery.status", "delivery"),
}


def _get(d: dict[str, Any], path: str) -> tuple[bool, Any]:
    cur: Any = d
    for part in path.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return False, None
        cur = cur[part]
    return True, cur


def verify_offer(offer: dict[str, Any], jwks: dict[str, Any]) -> dict[str, Any]:
    """Check a Signed Offer (a quote result or a plan line) against Valio's JWKS.

    Verifies the signature, then that the visible fields the agent is about to act on
    (offer_id, product, price, delivery, validity) match the signed claims. Returns the
    signed claims; raises SignatureError if anything does not match.
    """
    token = offer.get("signature")
    if not token:
        raise SignatureError("offer is not signed")
    claims = verify_jws(token, jwks, typ=TYP_OFFER)
    for claim, paths in _OFFER_FIELDS.items():
        for path in paths:
            found, value = _get(offer, path)
            if found and not isinstance(value, dict):
                if value != claims.get(claim):
                    raise SignatureError(f"offer field {path} does not match the signed {claim}")
                break
    return claims


def cart_hash(cart: dict[str, Any]) -> str:
    """Digest of what the buyer confirms: the lines and the total."""
    core = {"lines": [{k: ln.get(k) for k in ("offer_id", "product_id", "quantity",
                                              "unit_price_eur", "line_total_eur")}
                      for ln in cart["lines"]],
            "total_eur": cart["total_eur"], "currency": cart.get("currency", "EUR")}
    return "sha256:" + hashlib.sha256(canonical(core)).hexdigest()


def sign_cart_confirmation(key: Ed25519PrivateKey, order: dict[str, Any], confirmed_by: str,
                           decision: str = "confirm", note: str = "") -> str:
    """What the buyer's wallet / app signs when a human confirms or declines a cart."""
    return sign_jws({"order_id": order["order_id"], "buyer_id": order["buyer_id"],
                     "cart_hash": cart_hash(order["data"]["cart"]), "decision": decision,
                     "confirmed_by": confirmed_by, "note": note, "iat": now_iso()},
                    key, TYP_CART)


PASSPORT_MODES = ("advise", "confirm_cart", "autonomous")


PASSPORT_DEFAULT_DAYS = 7


def issue_passport(signer: ValioSigner, agent_id: str, model: str, harness: str,
                   max_mode: str, bench_score: float | None = None, *,
                   valid_days: float = PASSPORT_DEFAULT_DAYS) -> str:
    """Agent Passport: Valio vouches how autonomous a given agent build may be.

    Expires after `valid_days` (claim `exp`, an RFC 7519 NumericDate, seconds since the
    epoch): a new model or harness version has to earn its passport again. How an agent earns a max_mode
    (e.g. a bench score threshold) is out of scope here."""
    if max_mode not in PASSPORT_MODES:
        raise ValueError(f"max_mode must be one of {PASSPORT_MODES}")
    if valid_days <= 0:
        raise ValueError("valid_days must be positive")
    issued = current_time(datetime.now(TZ))
    claims: dict[str, Any] = {"agent_id": agent_id, "model": model, "harness": harness,
                              "max_mode": max_mode,
                              "issued_at": issued.isoformat(timespec="seconds"),
                              "exp": int((issued + timedelta(days=valid_days)).timestamp())}
    if bench_score is not None:
        claims["bench_score"] = bench_score
    return signer.sign(claims, TYP_PASSPORT)


def passport_problem(claims: dict[str, Any], at: datetime | None = None) -> str | None:
    """Why a verified passport cannot be used now (expired / no expiry), or None."""
    exp = claims.get("exp")
    if not isinstance(exp, int | float) or isinstance(exp, bool):
        return "passport has no expiry (exp)"
    if (at or current_time(datetime.now(TZ))).timestamp() >= exp:
        return f"passport expired at {datetime.fromtimestamp(exp, TZ).isoformat(timespec='seconds')}"
    return None


def verify_receipt(receipt: dict[str, Any], jwks: dict[str, Any]) -> dict[str, Any]:
    """Check an Order Receipt (`order.data.receipt`); returns the signed claims."""
    claims = verify_jws(receipt["jws"], jwks, typ=TYP_RECEIPT)
    if receipt.get("claims") is not None and canonical(receipt["claims"]) != canonical(claims):
        raise SignatureError("receipt claims do not match the signature")
    return claims
