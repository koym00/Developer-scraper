"""
Scraper pre Skanska Reality (residential.skanska.cz).

OVERENÉ NAŽIVO (2026-08): stránka `/byty` je síce SPA (prázdny
`<div id="apartment-filter">`, obsah vykresľuje až JavaScript), ale JS
widget si dáta ťahá z verejného JSON API - **Playwright teda vôbec nie je
potrebný**. Endpoint bol nájdený priamo v JS bundli
(`assets/cs/js/cached.*.app.js`), kde trieda filtra počíta
`apiBaseUrl + filterSetId + "/snapshot"` (a `.../config`).
`filterSetId` je pre stránku s bytmi hardcodovaný v HTML ako
`filterSetId: 'apartments_page_cs'`.

Endpoint vracia JEDNÝM requestom kompletný zoznam všetkých bytov
(`data.apartments`, ~360 záznamov) plus `data.reference_tables` - slovníky
id -> čitateľný text pre `projects`, `localities` a `amenities` (číselné
polia na jednotlivom byte, napr. `"projects": [285]`, sú len cudzie kľúče
do tejto tabuľky). `floor_llp`, `disposition_text`, `eta` (termín
nastěhovania, napr. "Q2 2027") a `area`/`price` sú už priamo na zázname
bytu - `area` je v stotinách m² (5800 -> 58.0 m²), `price` je rovno v Kč.

Stav predaja (`state`) NIE JE "available"/"reserved" text, ale interné
hodnoty "empty"/"registered" - ich význam bol zistený z JS bundlu
(zelený/oranžový odznak v UI):
    "empty"      -> zelený odznak, tooltip "Volný"          -> AVAILABLE
    "registered" -> oranžový odznak, tooltip "V jednání"    -> RESERVED
(žiadna iná hodnota `state` sa naživo nenašla; iné hodnoty preto padajú
do UNKNOWN, aby scraper nehádal.)

**Užitná plocha a orientácia (doplnené 2026-08, nahlásil používateľ) -**
snapshot API ich nemá, ale detail KAŽDÉHO bytu (`listitem` bloky na
`residential.skanska.cz/.../<code>`) áno - "Užitná plocha" (m²) a
"Orientace" (napr. "J"). `fetch_extra_details_for_unit()` ich dotiahne
lenivo, len pre konkrétny vyhľadaný byt (viď `main.py`), nie hromadne.
"""
from __future__ import annotations

import logging

from bs4 import BeautifulSoup

from main.models import Developer, UnitData, UnitStatus
from main.scrapers.base import BaseScraper, ScraperError
from main.scrapers.parsing_utils import parse_float, strip_diacritics_lower_alnum

logger = logging.getLogger(__name__)

BASE_URL = "https://residential.skanska.cz"
FILTER_SET_ID = "apartments_page_cs"
SNAPSHOT_URL = f"{BASE_URL}/api/v1/filters/{FILTER_SET_ID}/snapshot"

# Normalizovaný label z "listitem" bloku na detaile bytu -> náš kľúč.
# Nájdené 2026-08 (nahlásil používateľ) - detail KAŽDÉHO bytu má okrem
# "Podlahová plocha" (už v bulk API ako `area`) aj "Užitná plocha" a
# "Orientace" (obe v bulk API/`reference_tables` chýbajú úplne).
_LISTITEM_LABEL_TO_KEY = {
    "uzitnaplocha": "usable_area_m2",
    "orientace": "orientation",
}

# Fallback, keď sa `projects` referencia z API nedá vyhľadať v
# `reference_tables` - viď `_is_ghost_record` nižšie.
_FALLBACK_PROJECT_NAME = "Skanska Reality"

_STATE_MAP = {
    "empty": UnitStatus.AVAILABLE,
    "registered": UnitStatus.RESERVED,
}


def _lookup(reference_tables: dict, table_name: str, ids: list) -> str | None:
    if not ids:
        return None
    table = reference_tables.get(table_name, {})
    value = table.get(str(ids[0]))
    return value.strip() if value else value


def _apartment_to_unit_data(apt: dict, developer: Developer, reference_tables: dict) -> UnitData:
    project_name = _lookup(reference_tables, "projects", apt.get("projects")) or _FALLBACK_PROJECT_NAME
    locality = _lookup(reference_tables, "localities", apt.get("localities"))

    amenities_table = reference_tables.get("amenities", {})
    features = [
        amenities_table[str(aid)].lower()
        for aid in apt.get("amenities", [])
        if str(aid) in amenities_table
    ]

    area = apt.get("area")
    area_m2 = round(area / 100, 2) if area else None

    code = apt.get("code")
    # súbor pôdorysu je vždy malými písmenami, aj keď `code` je z API veľkými
    plan_url = f"{BASE_URL}/files/{code.lower()}.svg" if code else None

    return UnitData(
        developer=developer,
        project_name=project_name,
        unit_number=code or str(apt.get("id")),
        plan_url=plan_url,
        detail_url=apt.get("detail_url"),
        locality=locality,
        floor=apt.get("floor_llp"),
        disposition=apt.get("disposition_text"),
        area_m2=area_m2,
        features=features,
        price_czk=apt.get("price") or None,
        status=_STATE_MAP.get(apt.get("state"), UnitStatus.UNKNOWN),
        move_in_date=apt.get("eta"),
        source_url=apt.get("detail_url") or SNAPSHOT_URL,
    )


def _is_ghost_record(unit: UnitData) -> bool:
    """Naživo sa našiel (2026-08, nahlásil používateľ na konkrétnom
    odkaze) ojedinelý "duch" v dátach Skanska - byt, ktorého `projects`
    referencia sa nedá vyhľadať v `reference_tables` (žiadny reálny
    projekt), a ktorý súčasne nemá ani cenu, ani plochu. Ich vlastný web
    pre taký byt vygeneruje URL s "unknown" namiesto názvu projektu a
    stránka je prázdna - je to rozbitý/osirelý záznam na strane Skanska,
    nie chyba tohto scrapera. Používateľ potvrdil, že takéto záznamy má
    appka radšej zahodiť, než ich vracať ako použiteľné dáta. Kontroluje
    sa kombinácia troch chýbajúcich polí naraz (nie len jedno), aby sa
    nezahodili byty, ktoré majú len BEŽNE chýbajúcu jednu hodnotu
    (napr. cena "na dotaz" pri inak kompletnom zázname)."""
    return unit.project_name == _FALLBACK_PROJECT_NAME and unit.area_m2 is None and unit.price_czk is None


class SkanskaScraper(BaseScraper):
    developer = Developer.SKANSKA
    base_url = BASE_URL

    def fetch_all_units(self) -> list[UnitData]:
        resp = self._get(SNAPSHOT_URL)
        try:
            payload = resp.json()
        except ValueError as exc:
            raise ScraperError(f"Skanska: odpoveď z {SNAPSHOT_URL} nie je platný JSON ({exc})") from exc

        if not payload.get("success"):
            raise ScraperError(f"Skanska: API vrátilo success=false ({SNAPSHOT_URL})")

        data = payload.get("data", {})
        apartments = data.get("apartments", [])
        reference_tables = data.get("reference_tables", {})

        if not apartments:
            raise ScraperError(
                "Skanska: API vrátilo 0 bytov - štruktúra odpovede sa pravdepodobne zmenila, "
                "over SNAPSHOT_URL a tvar dát naživo."
            )

        units = [_apartment_to_unit_data(apt, self.developer, reference_tables) for apt in apartments]

        clean_units = []
        for unit in units:
            if _is_ghost_record(unit):
                logger.warning(
                    "Skanska: preskakujem osirelý záznam bez projektu/ceny/plochy (%s, %s)",
                    unit.unit_number, unit.detail_url,
                )
                continue
            clean_units.append(unit)
        return clean_units

    def fetch_extra_details_for_unit(self, record) -> dict:
        """Lazy dotiahnutie polí, ktoré sú len na detaile KONKRÉTNEHO bytu,
        nie v hromadnom snapshot API - užitná plocha a orientácia (viď
        `_LISTITEM_LABEL_TO_KEY`). Nevolá sa z `fetch_all_units()` (356
        bytov, zbytočne by to spomalilo hromadný `/refresh` - rovnaký
        dôvod ako pri Ekospole/Central Group)."""
        detail_url = record.detail_url
        if not detail_url:
            return {}
        try:
            resp = self._get(detail_url)
        except Exception as exc:
            logger.warning("Skanska: zlyhalo stiahnutie detailu %s (%s)", detail_url, exc)
            return {}
        soup = BeautifulSoup(resp.text, "lxml")

        result: dict = {}
        for item in soup.select(".listitem"):
            cells = item.select(".listitem-cell")
            if len(cells) < 2:
                continue
            label_p, value_p = cells[0].find("p"), cells[1].find("p")
            if label_p is None or value_p is None:
                continue
            key = _LISTITEM_LABEL_TO_KEY.get(strip_diacritics_lower_alnum(label_p.get_text()))
            if key is None:
                continue
            text = value_p.get_text(strip=True)
            if key == "usable_area_m2":
                area = parse_float(text)
                if area:
                    result[key] = area
            elif key == "orientation" and text:
                result[key] = text
        return result

    def needs_extra_details(self, record) -> bool:
        return record.usable_area_m2 is None or not record.orientation


if __name__ == "__main__":
    with SkanskaScraper() as scraper:
        result = scraper.fetch_all_units()
        print(f"Nájdených {len(result)} bytov.")
        for u in result[:5]:
            print(u.model_dump())
