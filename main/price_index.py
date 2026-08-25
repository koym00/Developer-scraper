"""
Orientačný cenový index (Kč/m²) podľa okresu/mestskej časti.

Toto je DOPLNKOVÝ odhad k `pricing.py` (ktorý porovnáva byty v tom istom
projekte) - namiesto porovnania s podobnými bytmi porovnáva podlahovú
plochu jednotky s priemernou cenou za m² v danej lokalite.

**Dôležité:** hodnoty v `data/price_index_praha.json` sú zatiaľ len
PLACEHOLDER (viď poznámka priamo v súbore) - úloha bola zámerne
navrhnutá tak, aby fungovala end-to-end už teraz a reálny zdroj dát
(ČSÚ, cenová mapa a pod.) sa dal doplniť neskôr **bez zásahu do kódu**,
len prepísaním JSON súboru so zachovaním rovnakého tvaru (mapa
"lokalita" -> Kč/m²).

Developeri uvádzajú lokalitu v rôznych tvaroch ("Praha 5", "Praha 5 -
Smíchov", len "Smíchov"...) - `get_price_per_m2()` preto skúša viacero
odvodených kľúčov, nie len presnú zhodu.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from main.scrapers.parsing_utils import strip_diacritics_lower_alnum

_DATA_PATH = Path(__file__).resolve().parent / "data" / "price_index_praha.json"

_DISTRICT_RE = re.compile(r"praha\s*-?\s*(\d{1,2})", re.IGNORECASE)


def _load_index() -> dict[str, float]:
    if not _DATA_PATH.exists():
        return {}
    with open(_DATA_PATH, encoding="utf-8") as f:
        raw = json.load(f)
    return {
        strip_diacritics_lower_alnum(key): value
        for key, value in raw.items()
        if not key.startswith("_")  # "_POZNAMKA" a pod. - nie skutočný dátový záznam
    }


_PRICE_INDEX = _load_index()


def _candidate_keys(locality: str) -> list[str]:
    """Odvodí z textu lokality viacero možných kľúčov do indexu, keďže
    developeri ho uvádzajú v rôznych tvaroch - skúša sa celý text, časť za
    poslednou pomlčkou (mestská časť) a vzor "Praha N" (číslo obvodu)."""
    candidates = [locality]
    for dash in ("-", "–", "—"):
        if dash in locality:
            candidates.append(locality.rsplit(dash, 1)[-1].strip())
    match = _DISTRICT_RE.search(locality)
    if match:
        candidates.append(f"Praha {match.group(1)}")
    return candidates


def get_price_per_m2(locality: str | None) -> tuple[float, str] | None:
    """Vráti `(Kč/m², kľúč indexu ktorý sa použil)` pre danú lokalitu,
    alebo `None`, ak lokalita chýba alebo sa pre ňu nenašla zhoda."""
    if not locality:
        return None
    for candidate in _candidate_keys(locality):
        key = strip_diacritics_lower_alnum(candidate)
        if key in _PRICE_INDEX:
            return _PRICE_INDEX[key], candidate
    return None
