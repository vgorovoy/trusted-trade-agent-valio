"""Static product catalogue: ingest the Valio Aimo data files into SQLite and search it.

Search uses an FTS5 *trigram* index over fi/en/sv names, brand and key ingredients.
Trigrams matter for Finnish compounds: "kevytmaitojuoma" must match the query "maito".
Structured filters (allergens, claims, labels) come from GS1/Synkka classifications,
so "lactose-free" is checked against `FREE_FROM_LACTOSE`, not guessed from the name.
"""

from __future__ import annotations

import csv
import json
import re
import sqlite3
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# GS1 AllergenTypeCode values seen in the data. Unknown codes are kept raw.
ALLERGEN_NAMES = {
    "AC": "crustaceans", "AE": "eggs", "AF": "fish", "AM": "milk", "AN": "tree nuts",
    "AP": "peanuts", "AS": "sesame", "AU": "sulphites", "AW": "gluten cereals", "AY": "soy",
    "BC": "celery", "BM": "mustard", "GB": "barley", "GK": "kamut", "GO": "oats",
    "GS": "spelt", "ML": "lactose", "NL": "lupin", "NR": "rye", "UM": "molluscs",
    "UW": "wheat", "SA": "almond", "SC": "cashew", "SH": "hazelnut", "SM": "macadamia",
    "SP": "pecan", "SR": "brazil nut", "ST": "pistachio", "SW": "walnut", "X99": "other",
}
ALLERGEN_CODES = {v: k for k, v in ALLERGEN_NAMES.items()}

SCHEMA = """
CREATE TABLE IF NOT EXISTS products (
  id TEXT PRIMARY KEY,
  name_fi TEXT, name_en TEXT, name_sv TEXT,
  brand TEXT, vendor TEXT, category TEXT,
  sales_unit TEXT, base_unit TEXT, base_per_sales REAL, lot_size REAL,
  temperature INTEGER, country TEXT, gtin TEXT,
  name_size REAL, name_size_unit TEXT,      -- size parsed from the name, per base unit (L or KG)
  contains TEXT, may_contain TEXT, free_from TEXT,   -- JSON arrays of allergen codes
  claims TEXT, labels TEXT,                          -- JSON arrays
  key_ingredients TEXT,
  reliability REAL, co2_per_kg REAL,
  raw TEXT
);
CREATE TABLE IF NOT EXISTS substitutions (
  product TEXT, substitute TEXT, PRIMARY KEY (product, substitute)
);
CREATE VIRTUAL TABLE IF NOT EXISTS products_fts USING fts5(
  id UNINDEXED, names, brand, ingredients, tokenize = 'trigram remove_diacritics 1'
);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
"""

_SIZE = re.compile(r"(?<![\dx])(\d+(?:[.,]\d+)?)\s*(ml|cl|dl|l|g|kg)\b", re.I)
_MULTI = re.compile(r"\d+\s*x\s*\d|\b\d+\s*x\s|\bx\s*\d+\b|\d+\s*kpl", re.I)
_TO_BASE = {"ml": (0.001, "L"), "cl": (0.01, "L"), "dl": (0.1, "L"), "l": (1.0, "L"),
            "g": (0.001, "KG"), "kg": (1.0, "KG")}


def parse_name_size(name: str) -> tuple[float, str] | None:
    """Size of one base unit from a product name, e.g. 'maito 1 l' -> (1.0, 'L').

    Returns None for multipacks ('6 x 500g', '60x90g') and names with no or several sizes:
    those are ambiguous and the price API's comparison price is used instead.
    """
    if not name or _MULTI.search(name):
        return None
    hits = _SIZE.findall(name)
    if len(hits) != 1:
        return None
    value, unit = hits[0]
    factor, base = _TO_BASE[unit.lower()]
    return round(float(value.replace(",", ".")) * factor, 6), base


def _name(names: list[dict[str, str]], lang: str) -> str:
    return next((n["value"] for n in names if n.get("language") == lang), "")


def _classes(sd: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    return {c["name"]: c.get("values", []) for c in sd.get("classifications", [])}


def _product_row(p: dict[str, Any], reliability: dict[str, float], co2: dict[str, float]) -> dict:
    sd = p.get("synkkaData", {})
    names = sd.get("names", [])
    cls = _classes(sd)
    allergens = cls.get("allergen", [])
    contains = sorted({a["id"] for a in allergens if a.get("unit") == "CONTAINS"})
    may = sorted({a["id"] for a in allergens if a.get("unit") == "MAY_CONTAIN"})
    free = sorted({a["id"] for a in cls.get("nonAllergen", []) if a.get("unit") == "FREE_FROM"})
    claims = sorted({c["id"] for c in cls.get("nutritionalClaim", [])})
    labels = sorted({c["id"] for c in cls.get("packagingMarkedLabel", [])
                     if not c["id"].endswith("Temperature")})
    units = {u["unitId"]: u.get("sizeInBaseUnits") for u in p.get("units", [])}
    name_fi = _name(names, "fi")
    size = parse_name_size(name_fi) or parse_name_size(_name(names, "en"))
    key_ingr = " ".join(_name(sd.get("keyIngredients", []), lang) for lang in ("fi", "en"))
    return {
        "id": p["id"],
        "name_fi": name_fi,
        "name_en": _name(names, "en"),
        "name_sv": _name(names, "sv"),
        "brand": sd.get("brand") or "",
        "vendor": p.get("vendorName") or "",
        "category": p.get("category"),
        "sales_unit": p.get("salesUnit"),
        "base_unit": p.get("baseUnit"),
        "base_per_sales": units.get(p.get("salesUnit")) or 1.0,
        "lot_size": p.get("allowedLotSize") or 1.0,
        "temperature": p.get("temperatureCondition"),
        "country": p.get("countryOfOrigin"),
        "gtin": p.get("salesUnitGtin"),
        "name_size": size[0] if size else None,
        "name_size_unit": size[1] if size else None,
        "contains": json.dumps(contains),
        "may_contain": json.dumps(may),
        "free_from": json.dumps(free),
        "claims": json.dumps(claims),
        "labels": json.dumps(labels),
        "key_ingredients": key_ingr.strip(),
        "reliability": reliability.get(p["id"]),
        "co2_per_kg": co2.get(p["id"]),
        "raw": json.dumps(p, ensure_ascii=False),
    }


def _read_csv_map(path: Path | None, column: str) -> dict[str, float]:
    if not path or not path.exists():
        return {}
    with path.open(newline="", encoding="utf-8") as f:
        return {r["productId"]: float(r[column]) for r in csv.DictReader(f) if r.get(column)}


def build_catalog(
    db_path: Path,
    products_json: Path,
    reliability_csv: Path | None = None,
    carbon_csv: Path | None = None,
) -> int:
    """(Re)build the catalogue database. Returns the number of products loaded."""
    products = json.loads(products_json.read_text(encoding="utf-8"))
    reliability = _read_csv_map(reliability_csv, "deliveryReliability")
    co2 = _read_csv_map(carbon_csv, "AVG kg CO2-eq/kg")
    db_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = db_path.with_suffix(".building")
    tmp.unlink(missing_ok=True)
    con = sqlite3.connect(tmp)
    con.executescript(SCHEMA)
    rows = [_product_row(p, reliability, co2) for p in products if not p.get("deleted")]
    cols = list(rows[0])
    con.executemany(
        f"INSERT INTO products ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
        [tuple(r[c] for c in cols) for r in rows],
    )
    con.executemany(
        "INSERT OR IGNORE INTO substitutions VALUES (?, ?)",
        [(s["product"], s["substituteProduct"])
         for p in products for s in (p.get("substitutions") or [])],
    )
    con.executemany(
        "INSERT INTO products_fts (id, names, brand, ingredients) VALUES (?, ?, ?, ?)",
        [(r["id"], " | ".join(filter(None, (r["name_fi"], r["name_en"], r["name_sv"]))),
          r["brand"], r["key_ingredients"]) for r in rows],
    )
    con.execute("INSERT INTO meta VALUES ('products', ?)", (str(len(rows)),))
    con.commit()
    con.close()
    tmp.replace(db_path)
    return len(rows)


@dataclass
class Product:
    id: str
    name: str
    name_en: str
    brand: str
    vendor: str
    category: str | None
    sales_unit: str
    base_unit: str
    base_per_sales: float
    lot_size: float
    name_size: float | None
    name_size_unit: str | None
    contains: list[str]
    may_contain: list[str]
    free_from: list[str]
    claims: list[str]
    labels: list[str]
    country: str | None
    reliability: float | None
    co2_per_kg: float | None
    substitutes: list[str] = field(default_factory=list)

    def summary(self) -> dict[str, Any]:
        """Compact view for agents: what is needed to choose, nothing more."""
        return {
            "product_id": self.id,
            "name": self.name,
            "name_en": self.name_en,
            "brand": self.brand,
            "sales_unit": self.sales_unit,
            "content_hint": (
                {"per_sales_unit": round(self.name_size * self.base_per_sales, 4),
                 "unit": self.name_size_unit}
                if self.name_size else None
            ),
            "allergens_contains": [ALLERGEN_NAMES.get(c, c) for c in self.contains],
            "allergens_may_contain": [ALLERGEN_NAMES.get(c, c) for c in self.may_contain],
            "claims": self.claims,
            "labels": self.labels,
            "country": self.country,
            "delivery_reliability": self.reliability,
            "co2_kg_per_kg_category_avg": self.co2_per_kg,
            "official_substitutes": self.substitutes,
        }


def _to_product(r: sqlite3.Row, substitutes: list[str]) -> Product:
    return Product(
        id=r["id"], name=r["name_fi"] or r["name_en"], name_en=r["name_en"], brand=r["brand"],
        vendor=r["vendor"], category=r["category"], sales_unit=r["sales_unit"],
        base_unit=r["base_unit"], base_per_sales=r["base_per_sales"], lot_size=r["lot_size"],
        name_size=r["name_size"], name_size_unit=r["name_size_unit"],
        contains=json.loads(r["contains"]), may_contain=json.loads(r["may_contain"]),
        free_from=json.loads(r["free_from"]), claims=json.loads(r["claims"]),
        labels=json.loads(r["labels"]), country=r["country"], reliability=r["reliability"],
        co2_per_kg=r["co2_per_kg"], substitutes=substitutes,
    )



# Further GS1 AllergenTypeCodes that occur in the Valio data without a name in our table;
# they are accepted as codes and shown raw.
ALLERGEN_EXTRA_CODES = {"ABD", "ADQ", "BF", "BP", "CM", "NC", "NE", "NK", "NM", "NP", "NW",
                        "PN", "PO", "SO"}
KNOWN_ALLERGEN_CODES = set(ALLERGEN_NAMES) | ALLERGEN_EXTRA_CODES

GLUTEN_CODES = frozenset({"AW", "UW", "NR", "GB", "GS", "GK"})   # oats (GO) not included
TREE_NUT_CODES = frozenset({"AN", "SA", "SC", "SH", "SM", "SP", "SR", "ST", "SW"})
# A code excludes its whole family: products are tagged with the specific code (hazelnut SH)
# or the umbrella one (tree nuts AN), so both must be caught.
ALLERGEN_FAMILIES: dict[str, frozenset[str]] = {
    "AM": frozenset({"AM", "ML"}),
    "AW": GLUTEN_CODES,
    "UW": frozenset({"UW", "AW"}),
    "AN": TREE_NUT_CODES,
}
LACTOSE = "ML"
LACTOSE_FREE_CLAIMS = frozenset({"FREE_FROM_LACTOSE", "LOW_LACTOSE"})

# Everyday names (English / Finnish) an agent or a buyer may use for a GS1 allergen code.
ALLERGEN_SYNONYMS = {
    "milk": "AM", "dairy": "AM", "maito": "AM", "lactose": "ML", "laktoosi": "ML",
    "gluten": "AW", "gluteeni": "AW", "cereals containing gluten": "AW",
    "nuts": "AN", "nut": "AN", "tree nut": "AN", "pähkinä": "AN", "pähkinät": "AN",
    "peanut": "AP", "maapähkinä": "AP", "egg": "AE", "kananmuna": "AE", "muna": "AE",
    "soya": "AY", "soija": "AY", "sulphite": "AU", "sulfites": "AU", "sulfite": "AU",
    "shellfish": "AC", "crustacean": "AC", "mollusc": "UM", "mollusk": "UM",
    "mollusks": "UM", "kala": "AF", "seesami": "AS", "selleri": "BC", "sinappi": "BM",
    "lupine": "NL", "oat": "GO", "kaura": "GO", "vehnä": "UW", "ruis": "NR", "ohra": "GB",
}
# Names that stand for several umbrella codes at once.
ALLERGEN_MULTI = {"nuts and peanuts": ("AN", "AP"), "all nuts": ("AN", "AP"),
                  "pähkinät ja maapähkinä": ("AN", "AP")}


class UnknownAllergen(ValueError):
    """An allergen name that maps to no GS1 code: excluding it would silently exclude nothing."""


def _lookup(x: str) -> tuple[str, ...]:
    raw = x.strip()
    low = raw.lower()
    if raw.upper() in KNOWN_ALLERGEN_CODES:
        return (raw.upper(),)
    for table in (ALLERGEN_CODES, ALLERGEN_SYNONYMS):
        if low in table:
            return (table[low],)
    if low in ALLERGEN_MULTI:
        return ALLERGEN_MULTI[low]
    known = sorted(set(ALLERGEN_CODES) | set(ALLERGEN_SYNONYMS) | set(ALLERGEN_MULTI))
    raise UnknownAllergen(f"unknown allergen {x!r}: use a GS1 code (e.g. AM, ML, AW) or one of "
                          f"{', '.join(known)}")


def allergen_code(x: str) -> str:
    """Primary GS1 AllergenTypeCode for a code or a name ("milk" -> AM, "lactose" -> ML).

    Raises UnknownAllergen for names and codes it does not know. Filtering must use
    `AllergenFilter`, which also covers the code's family (gluten -> wheat, rye, barley...).
    """
    return _lookup(x)[0]


_allergen_code = allergen_code   # backward-compatible alias


@dataclass(frozen=True)
class AllergenFilter:
    """The one allergen predicate used by search, plan, substitutes and the mandate check.

    - each requested name maps to a set of codes (gluten -> AW, UW, NR, GB, GS, GK;
      nuts -> AN and the specific tree nuts; milk -> AM, ML; wheat -> UW, AW);
    - lactose (ML) also hits milk (AM) unless the product claims FREE_FROM_LACTOSE or
      LOW_LACTOSE, since most milk products are tagged AM only;
    - "may contain" is checked with the same sets when the caller asks for it.
    Oats (GO) are not part of gluten; exclude "oats" explicitly if needed.
    """

    codes: frozenset[str] = frozenset()
    lactose: bool = False

    @classmethod
    def parse(cls, names: Iterable[str] = ()) -> AllergenFilter:
        codes: set[str] = set()
        for name in names:
            for code in _lookup(name):
                codes |= ALLERGEN_FAMILIES.get(code, {code})
        return cls(frozenset(codes), LACTOSE in codes)

    def __bool__(self) -> bool:
        return bool(self.codes)

    def hits(self, codes: Iterable[str], claims: Iterable[str] = ()) -> set[str]:
        tagged = set(codes)
        hit = set(self.codes) & tagged
        if self.lactose and "AM" in tagged and not LACTOSE_FREE_CLAIMS & set(claims):
            hit.add("AM")
        return hit

    def reason(self, contains: Iterable[str], may_contain: Iterable[str],
               claims: Iterable[str] = (), exclude_may_contain: bool = False) -> str | None:
        """Why a product is excluded ('contains milk'), or None if it passes."""
        claims = list(claims)
        if hit := self.hits(contains, claims):
            return f"contains {allergen_names(hit)}"
        if exclude_may_contain and (hit := self.hits(may_contain, claims)):
            return f"may contain {allergen_names(hit)}"
        return None


def allergen_names(codes: Iterable[str]) -> str:
    return ", ".join(sorted(ALLERGEN_NAMES.get(c, c) for c in codes))

class Catalog:
    def __init__(self, db_path: Path) -> None:
        if not db_path.exists():
            raise FileNotFoundError(f"{db_path} missing: run `tta ingest` first")
        self.con = sqlite3.connect(db_path, check_same_thread=False)
        self.con.row_factory = sqlite3.Row

    def get(self, product_id: str) -> Product | None:
        r = self.con.execute("SELECT * FROM products WHERE id = ?", (product_id,)).fetchone()
        return _to_product(r, self.substitutes_of(product_id)) if r else None

    def get_many(self, ids: Iterable[str]) -> dict[str, Product]:
        return {i: p for i in ids if (p := self.get(i))}

    def raw(self, product_id: str) -> dict[str, Any] | None:
        r = self.con.execute("SELECT raw FROM products WHERE id = ?", (product_id,)).fetchone()
        return json.loads(r["raw"]) if r else None

    def substitutes_of(self, product_id: str) -> list[str]:
        rows = self.con.execute(
            "SELECT substitute FROM substitutions WHERE product = ?", (product_id,)
        ).fetchall()
        return [r[0] for r in rows]

    def search(
        self,
        query: str,
        *,
        require_claims: Iterable[str] = (),
        exclude_allergens: Iterable[str] = (),
        exclude_may_contain: bool = False,
        require_labels: Iterable[str] = (),
        limit: int = 20,
    ) -> list[Product]:
        """Full-text search plus hard structured filters.

        Every query term (3+ chars) must appear somewhere in the indexed text.
        Ranking is BM25; agents should still judge relevance (e.g. cocoa drink vs. milk).
        """
        terms = [t for t in re.findall(r"\w+", query.lower()) if len(t) >= 3]
        if not terms:
            return []
        fts = " AND ".join(f'"{t}"' for t in terms)
        rows = self.con.execute(
            """SELECT p.* FROM products_fts f JOIN products p ON p.id = f.id
               WHERE products_fts MATCH ? ORDER BY bm25(products_fts, 0, 10, 3, 1) LIMIT 500""",
            (fts,),
        ).fetchall()
        claims = set(require_claims)
        labels = set(require_labels)
        banned = AllergenFilter.parse(exclude_allergens)
        out: list[Product] = []
        for r in rows:
            row_claims = json.loads(r["claims"])
            if claims and not claims <= set(row_claims):
                continue
            if labels and not labels <= set(json.loads(r["labels"])):
                continue
            if banned and banned.reason(json.loads(r["contains"]), json.loads(r["may_contain"]),
                                        row_claims, exclude_may_contain):
                continue
            out.append(_to_product(r, self.substitutes_of(r["id"])))
            if len(out) >= limit:
                break
        return out
