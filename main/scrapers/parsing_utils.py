"""Pomocné parsovacie funkcie zdieľané naprieč jednotlivými scrapermi.

Developerské weby píšu čísla a stavy nekonzistentne (čárky vs. bodky,
"Kč" vs. nič, "volný" vs. "k dispozici" ...), takže tieto funkcie sú
zámerne benevolentné a radšej vrátia None, než aby spadli na výnimke -
scraper jednej chybnej bunky nesmie zhodiť celý beh pre stovky bytov.
"""
from __future__ import annotations

import re
import unicodedata

from main.models import UnitStatus

_NUMBER_RE = re.compile(r"[\d\s.,]+")


def strip_diacritics_lower_alnum(text: str) -> str:
    """"Volný" -> "volny", "Číslo bytu" -> "cislobytu". Zdieľaná normalizácia
    použitá pri mapovaní hlavičiek stĺpcov aj pri parsovaní textových hodnôt
    (stavov a pod.), aby diakritika/veľkosť písmen neboli zdrojom chýb."""
    no_diacritics = "".join(
        ch for ch in unicodedata.normalize("NFKD", text) if not unicodedata.combining(ch)
    )
    return "".join(ch for ch in no_diacritics.lower() if ch.isalnum())


def normalize_header(text: str | None) -> str:
    if not text:
        return ""
    return strip_diacritics_lower_alnum(text)


def parse_float(text: str | None) -> float | None:
    if not text:
        return None
    match = _NUMBER_RE.search(text)
    if not match:
        return None
    raw = match.group(0).strip()
    # "132,5 m²" -> 132.5 ; "1 234,5" -> 1234.5 (čárka = desatinná, medzera = tisícová)
    raw = raw.replace(" ", "").replace(",", ".")
    try:
        return float(raw)
    except ValueError:
        return None


def parse_price_czk(text: str | None) -> int | None:
    """Vráti cenu v Kč ako int, alebo None ak nie je zverejnená ("na dotaz" a pod.)."""
    if not text:
        return None
    lowered = text.lower()
    if any(
        phrase in lowered
        for phrase in ("na dotaz", "na dotaz", "informace v rk", "dotaz", "neuvedena", "-")
    ) and not any(ch.isdigit() for ch in text):
        return None
    digits = re.sub(r"[^\d]", "", text)
    if not digits:
        return None
    try:
        return int(digits)
    except ValueError:
        return None


_STATUS_MAP = {
    "volny": UnitStatus.AVAILABLE,
    "volna": UnitStatus.AVAILABLE,
    "volne": UnitStatus.AVAILABLE,
    "kdispozici": UnitStatus.AVAILABLE,
    "rezervovany": UnitStatus.RESERVED,
    "rezervovana": UnitStatus.RESERVED,
    "rezervace": UnitStatus.RESERVED,
    "predrezervovano": UnitStatus.RESERVED,
    "prodany": UnitStatus.SOLD,
    "prodana": UnitStatus.SOLD,
    "prodano": UnitStatus.SOLD,
    "obsazeny": UnitStatus.SOLD,
}


def parse_status(text: str | None) -> UnitStatus:
    if not text:
        return UnitStatus.UNKNOWN
    key = strip_diacritics_lower_alnum(text.strip())
    return _STATUS_MAP.get(key, UnitStatus.UNKNOWN)


_FEATURE_MAP = {
    "t": "terasa",
    "b": "balkon",
    "l": "lodzia",
    "s": "sklep",
    "z": "zahradka",
    "p": "parkovani",
}


def parse_features(text: str | None) -> list[str]:
    """Developeri často kódujú príslušenstvo skratkami
    oddelenými medzerou, napr. "T B S" -> terasa, balkón, sklep."""
    if not text:
        return []
    tokens = re.split(r"[\s,/]+", text.strip())
    features = []
    for tok in tokens:
        key = tok.lower()
        if key in _FEATURE_MAP:
            features.append(_FEATURE_MAP[key])
        elif tok.strip():
            features.append(tok.strip().lower())
    return features
