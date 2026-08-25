"""
Scraper pre Finep (www.finep.cz).

Finep má samostatnú cenníkovú podstránku pre každý projekt vo formáte
`/cs/<projekt>/cenik`. Podobne ako pri Ekospole ide o dvojkrokový postup:
zoznam projektov -> pre každý projekt jeho `/cenik` stránka.

OVERENÉ NAŽIVO (2026-08): `/cenik` stránka NEMÁ jednotky v `<table>` ani v
JS-vloženom JSON-e (na rozdiel od Ekospolu) - byty sú vykreslené priamo v
HTML ako "karty" (`div.tile[data-item-id]`), stránkované cez `?page=N`
query parameter. Štruktúra jednej karty (zistená z reálneho HTML):

    div.tile
      a.tile-link[href]                       - URL detailu bytu
      div.g-9 > div.grid                       - dva vnorené `.g-6 > .grid`:
        [0]: <strong>Byt 305/C3</strong> <strong>1+kk</strong> <strong>24,4 m²</strong>
        [1]: "Britská čtvrť XVIII"  "3. NP"  "S" (budova, podlažie, orientácia)
        div.accessories                        - "balkon (5,9 m²), garáž ..."
        ul.tags                                - marketingové štítky
                                                  ("Ve výstavbě"/"Novinka"),
                                                  NIE stav predaja
      div.price-info strong                    - aktuálna cena (prvá <strong>
                                                  v cenovom bloku; prípadná
                                                  preškrtnutá pôvodná cena
                                                  nemá <strong>, takže sa
                                                  nezamieňa)

`/cenik` stránka zjavne obsahuje len byty aktuálne na predaj (žiadny tag
"Prodáno"/"Rezervováno" sa nenašiel v žiadnej z overených kariet), preto sa
stav defaultne nastavuje na AVAILABLE, pokiaľ štítok neobsahuje rozpoznaný
stav (`parse_status`).
"""
from __future__ import annotations

import logging
import re

from bs4 import BeautifulSoup

from main.models import Developer, UnitData, UnitStatus
from main.scrapers.base import BaseScraper
from main.scrapers.parsing_utils import parse_float, parse_price_czk, parse_status, strip_diacritics_lower_alnum

logger = logging.getLogger(__name__)

BASE_URL = "https://www.finep.cz"
PROJECT_LIST_URL = "https://www.finep.cz/cs/byty"
PROJECT_LINK_SELECTOR = "a[href*='/cs/byty-']"

MAX_PAGES_SAFETY_LIMIT = 30

# Pôdorys bytu na https://www.finep.cz/files/images/item/plan/1/<data-item-id>.png
# - dá sa poskladať priamo z `data-item-id` atribútu na `div.tile` (ten istý
# atribút, čo je vidieť v HTML kariet), žiadny extra request netreba.
PLAN_IMAGE_BASE_URL = "https://www.finep.cz/files/images/item/plan/1"

# normalizovaný text prvého slova v segmente "accessories" -> logický názov
_ACCESSORY_MAP = {
    "balkon": "balkon",
    "terasa": "terasa",
    "predzahradka": "predzahradka",
    "zahrada": "zahradka",
    "lodzie": "lodzie",
    "garaz": "parkovani",
    "komora": "komora",
    "sklep": "sklep",
}
# ktoré z vyššie uvedených kategórií sa počítajú do outdoor_area_m2 - lodžia
# doplnená 2026-08 (predtým sa jej plocha vôbec neparsovala ani nezapočítavala
# do súčtu, hoci ju má cca 45 % bytov u Finepu - viď Coverage Blueprint).
_OUTDOOR_AREA_KEYS = {"balkon", "terasa", "predzahradka", "zahrada", "lodzie"}

_UNIT_LABEL_PREFIX_RE = re.compile(r"^.*?byt\.?\s+", re.IGNORECASE)
_ACCESSORY_AREA_RE = re.compile(r"\(([\d,.\s]+)\s*m", re.IGNORECASE)


def _parse_accessories(text: str | None) -> tuple[list[str], float | None, dict[str, float]]:
    """Parsuje text ako 'balkon (5,9 m²), garáž Cena garážového stání ...'
    na zoznam features + súčet plochy vonkajších priestorov (balkón/terasa/
    predzahrádka/záhrada) + plochu KAŽDÉHO z nich ZVLÁŠŤ (kľúč je rovnaký
    normalizovaný názov ako vo `features`, napr. "zahrada" -> "zahradka").
    Segmenty ako "garáž"/"komora" nesú za sebou aj tooltip text (bez
    čiarky pred ním), preto sa z každého segmentu berie len prvé slovo ako
    názov vlastnosti."""
    if not text:
        return [], None, {}
    features: list[str] = []
    outdoor_total = 0.0
    has_outdoor = False
    outdoor_area_by_type: dict[str, float] = {}
    # Rozdelenie na "," ako oddeľovač vlastností - NIE na desatinnú čiarku
    # v čísle plochy (napr. "5,9 m²"), preto sa nerozdelí čiarka, po ktorej
    # priamo nasleduje číslica.
    for raw_segment in re.split(r",(?!\d)", text):
        segment = raw_segment.strip()
        if not segment:
            continue
        first_word = segment.split()[0]
        key = strip_diacritics_lower_alnum(first_word)
        mapped_key = _ACCESSORY_MAP.get(key, key)
        features.append(mapped_key)

        if key in _OUTDOOR_AREA_KEYS:
            area_match = _ACCESSORY_AREA_RE.search(segment)
            area = parse_float(area_match.group(1)) if area_match else None
            if area:
                outdoor_total += area
                has_outdoor = True
                outdoor_area_by_type[mapped_key] = outdoor_area_by_type.get(mapped_key, 0.0) + area
    return features, (round(outdoor_total, 2) if has_outdoor else None), outdoor_area_by_type


def _parse_status(tags_text: str | None) -> UnitStatus:
    if tags_text:
        status = parse_status(tags_text)
        if status != UnitStatus.UNKNOWN:
            return status
    # /cenik zjavne listuje len byty aktuálne na predaj (žiadne "Prodáno"/
    # "Rezervováno" štítky sa naživo nenašli) - marketingové štítky ako
    # "Ve výstavbě"/"Novinka" nie sú rozpoznaný stav, preto default AVAILABLE.
    return UnitStatus.AVAILABLE


def _tile_to_unit_data(
    tile,
    developer: Developer,
    default_project_name: str,
    project_url: str,
    source_url: str,
    locality: str | None,
) -> UnitData | None:
    info_grids = tile.select(".g-9 .g-6 > .grid")
    if len(info_grids) < 2:
        return None
    unit_info, location_info = info_grids[0], info_grids[1]

    strongs = unit_info.find_all("strong")
    if len(strongs) < 3:
        return None
    unit_number = _UNIT_LABEL_PREFIX_RE.sub("", strongs[0].get_text(" ", strip=True)).strip()
    disposition = strongs[1].get_text(" ", strip=True)
    area_m2 = parse_float(strongs[2].get_text(" ", strip=True))

    location_parts = [
        c.get_text(" ", strip=True)
        for c in location_info.find_all(recursive=False)
        if "cleaner" not in (c.get("class") or [])
    ]
    building_name = location_parts[0] if len(location_parts) > 0 else None
    floor = location_parts[1] if len(location_parts) > 1 else None
    orientation = location_parts[2] if len(location_parts) > 2 else None

    accessories_el = tile.find(class_="accessories")
    features, outdoor_area_m2, outdoor_area_by_type = _parse_accessories(
        accessories_el.get_text(" ", strip=True) if accessories_el else None
    )

    tags_el = tile.find(class_="tags")
    status = _parse_status(tags_el.get_text(" ", strip=True) if tags_el else None)

    price_el = tile.select_one(".price-info strong")
    price_czk = parse_price_czk(price_el.get_text(" ", strip=True)) if price_el else None

    item_id = tile.get("data-item-id")
    plan_url = f"{PLAN_IMAGE_BASE_URL}/{item_id}.png" if item_id else None

    tile_link = tile.find("a", class_="tile-link", href=True)
    detail_url = tile_link["href"] if tile_link else None
    if detail_url and detail_url.startswith("/"):
        detail_url = f"{BASE_URL}{detail_url}"

    return UnitData(
        developer=developer,
        project_name=building_name or default_project_name,
        project_url=project_url,
        unit_number=unit_number,
        plan_url=plan_url,
        detail_url=detail_url,
        locality=locality,
        floor=floor,
        disposition=disposition,
        area_m2=area_m2,
        outdoor_area_m2=outdoor_area_m2,
        outdoor_area_by_type=outdoor_area_by_type,
        features=features,
        orientation=orientation,
        price_czk=price_czk,
        price_includes_vat=True if price_czk is not None else None,
        status=status,
        source_url=source_url,
    )


class FinepScraper(BaseScraper):
    developer = Developer.FINEP
    base_url = BASE_URL

    def discover_project_urls(self) -> list[str]:
        resp = self._get(PROJECT_LIST_URL)
        soup = BeautifulSoup(resp.text, "lxml")
        urls: set[str] = set()
        for a in soup.select(PROJECT_LINK_SELECTOR):
            href = a.get("href")
            if not href:
                continue
            if href.startswith("/"):
                href = BASE_URL + href
            urls.add(href.split("?")[0].rstrip("/"))
        return sorted(urls)

    def _discover_locality(self, project_url: str) -> str | None:
        """Lokalita (mestský obvod) je len na vlastnej stránke projektu, nie
        na /cenik ani na karte bytu - 1 request navyše na projekt (nie na byt)."""
        try:
            resp = self._get(project_url)
        except Exception as exc:
            logger.warning("Finep: zlyhalo stiahnutie %s pri zisťovaní lokality (%s)", project_url, exc)
            return None
        link = BeautifulSoup(resp.text, "lxml").select_one("a[href*='developerske-projekty-praha-']")
        if link is None:
            return None
        return link.get_text(strip=True) or None

    def _fetch_cenik_page(self, cenik_url: str, page: int) -> BeautifulSoup | None:
        url = cenik_url if page == 1 else f"{cenik_url}?page={page}"
        try:
            resp = self._get(url)
        except Exception as exc:
            logger.warning("Finep: zlyhalo stiahnutie %s (%s)", url, exc)
            return None
        return BeautifulSoup(resp.text, "lxml")

    def fetch_all_units(self) -> list[UnitData]:
        units: list[UnitData] = []
        project_urls = self.discover_project_urls()
        logger.info("Finep: nájdených %d projektových odkazov", len(project_urls))

        for project_url in project_urls:
            cenik_url = f"{project_url}/cenik"
            default_project_name = project_url.rstrip("/").rsplit("/", 1)[-1].replace("byty-", "").replace("-", " ").title()
            locality = self._discover_locality(project_url)

            soup = self._fetch_cenik_page(cenik_url, page=1)
            if soup is None:
                continue

            item_list = soup.select_one("[id^='item-list-']")
            if item_list is None:
                logger.warning(
                    "Finep: na %s sa nenašiel zoznam bytov (item-list) - "
                    "štruktúra stránky sa pravdepodobne zmenila",
                    cenik_url,
                )
                continue

            tiles = item_list.select(".tile")
            for tile in tiles:
                unit = _tile_to_unit_data(tile, self.developer, default_project_name, project_url, cenik_url, locality)
                if unit is not None:
                    units.append(unit)

            last_page = 1
            for a in soup.select(".pagination a[href]"):
                m = re.search(r"[?&]page=(\d+)", a["href"])
                if m:
                    last_page = max(last_page, int(m.group(1)))
            last_page = min(last_page, MAX_PAGES_SAFETY_LIMIT)

            for page in range(2, last_page + 1):
                page_soup = self._fetch_cenik_page(cenik_url, page)
                if page_soup is None:
                    continue
                page_item_list = page_soup.select_one("[id^='item-list-']")
                if page_item_list is None:
                    continue
                for tile in page_item_list.select(".tile"):
                    unit = _tile_to_unit_data(tile, self.developer, default_project_name, project_url, cenik_url, locality)
                    if unit is not None:
                        units.append(unit)

        return units


if __name__ == "__main__":
    with FinepScraper() as scraper:
        result = scraper.fetch_all_units()
        print(f"Nájdených {len(result)} bytov.")
        for u in result[:5]:
            print(u.model_dump())
