"""
Scraper pre Ekospol (www.ekospol.cz).

Na rozdiel od Trigemy nemá Ekospol jeden centrálny cenník - ceny/byty sú
rozdelené po jednotlivých projektových podstránkach (napr.
`/byty/prodej-bytu-praha/ekorezidence-smichov`). Postup je dvojkrokový:
  1) z listovacej stránky vytiahneme odkazy na jednotlivé projekty,
  2) pre každý projekt stiahneme jeho `/cenik` podstránku.

OVERENÉ NAŽIVO (2026-08): projektová stránka samotná obsahuje len súhrnné
tabuľky (lokalita, rozsah plochy, cena "od" podľa dispozície) - jednotlivé
byty tam NIE SÚ v <table>. Skutočný zoznam bytov je až na `/cenik`
podstránke, kde je `<table>` hlavička bez dátových riadkov (vypĺňa ju JS)
- ale rovno v `<script>` je vložené `window.flats = [...]` s kompletným
JSON zoznamom bytov (pagetitle, dispozice, podlazi, plocha, balkon, terasa,
predzahradka, orientace, cena, stav, garazove-stani, sklep, uri, ...).
Namiesto krehkého parsovania HTML tabuľky preto scraper parsuje priamo
tento JSON blok - je to štrukturované a spoľahlivejšie.
"""
from __future__ import annotations

import json
import logging
import re

from bs4 import BeautifulSoup

from main.models import Developer, UnitData
from main.scrapers.base import BaseScraper
from main.scrapers.parsing_utils import parse_float, parse_status

logger = logging.getLogger(__name__)

BASE_URL = "https://www.ekospol.cz"
PROJECT_LIST_URL = "https://www.ekospol.cz/byty/prodej-bytu-praha"
PROJECT_LINK_SELECTOR = "a[href*='/byty/prodej-bytu-praha/']"

# `window.flats = [ {...}, {...} ];` - JSON pole bytov vložené priamo v <script>
# na /cenik podstránke každého projektu.
FLATS_JSON_RE = re.compile(r"window\.flats\s*=\s*(\[.*?\])\s*;", re.DOTALL)

# Pôdorysy bytov sú na /assets/ekospol/<interny-slug>/pudorys/<cislo-bytu>.png,
# kde <interny-slug> NIE JE odvoditeľný z URL slugu projektu (napr. URL slug
# "ekocity-hostivar-a" má interný slug "eko_hostivar_a"). PÔVODNE sa vyťahoval
# z marketingového obrázka projektu (`.project-pic-col` na /cenik stránke),
# ale to je NESPOĽAHLIVÉ - naživo sa zistilo (2026-08, nahlásené používateľom),
# že Ekocity Hostivar B má tento obrázok chybne odkazujúci na priečinok
# Hostivar A (zdieľaný/nesprávny asset na strane Ekospolu), takže pôdorysy
# Hostivar B sa skladali s cudzím slugom a boli nedostupné. Jediný spoľahlivo
# správny zdroj slugu je detail KONKRÉTNEJ jednotky - stiahne sa preto len
# JEDEN detail na projekt (nie za každý byt) a slug sa vytiahne priamo z
# odkazu na pôdorys tam.
PROJECT_ASSET_SLUG_RE = re.compile(r"assets/ekospol/([a-z0-9_]+)/pudorys/")


def _to_number(value) -> float | None:
    """Polia v `window.flats` sú niekedy string ("52,6", "0,00"), niekedy
    priamo int/float (0) - zjednotí oba tvary na float, alebo None pre
    prázdny/neznámy vstup."""
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return parse_float(str(value))


def _to_price(value) -> int | None:
    """Cena 0 (int aj string "0") znamená v `window.flats` "nezverejnené/
    predané", nie skutočnú cenu 0 Kč."""
    num = _to_number(value)
    if not num:
        return None
    return int(num)


def _flat_to_unit_data(
    flat: dict,
    developer: Developer,
    default_project_name: str,
    project_url: str,
    source_url: str,
    asset_slug: str | None,
) -> UnitData:
    balcony_area = _to_number(flat.get("balkon"))
    terrace_area = _to_number(flat.get("terasa"))
    front_garden_area = _to_number(flat.get("predzahradka"))
    outdoor_components = [balcony_area, terrace_area, front_garden_area]
    present_components = [c for c in outdoor_components if c]
    outdoor_area = round(sum(present_components), 2) if present_components else None

    outdoor_area_by_type: dict[str, float] = {}
    if balcony_area:
        outdoor_area_by_type["balkon"] = balcony_area
    if terrace_area:
        outdoor_area_by_type["terasa"] = terrace_area
    if front_garden_area:
        outdoor_area_by_type["predzahradka"] = front_garden_area

    features: list[str] = []
    if _to_number(flat.get("balkon")):
        features.append("balkon")
    if _to_number(flat.get("terasa")):
        features.append("terasa")
    if _to_number(flat.get("predzahradka")):
        features.append("predzahradka")
    if _to_number(flat.get("sklep")):
        features.append("sklep")
    garage_price_czk = _to_price(flat.get("garazove-stani"))
    if garage_price_czk is not None:
        features.append("parkovani")

    price_czk = _to_price(flat.get("cena"))
    unit_number = str(flat.get("pagetitle") or flat.get("alias") or "").strip()
    plan_url = f"{BASE_URL}/assets/ekospol/{asset_slug}/pudorys/{unit_number}.png" if asset_slug and unit_number else None

    uri = flat.get("uri")
    detail_url = f"{BASE_URL}/{uri}" if uri else None

    return UnitData(
        developer=developer,
        project_name=default_project_name,
        project_url=project_url,
        unit_number=unit_number,
        plan_url=plan_url,
        detail_url=detail_url,
        locality=flat.get("lokalita") or None,
        floor=str(flat.get("podlazi")) if flat.get("podlazi") else None,
        disposition=flat.get("dispozice") or None,
        area_m2=_to_number(flat.get("plocha")),
        outdoor_area_m2=outdoor_area,
        outdoor_area_by_type=outdoor_area_by_type,
        features=features,
        orientation=flat.get("orientace") or None,
        price_czk=price_czk,
        price_includes_vat=True if price_czk is not None else None,
        garage_price_czk=garage_price_czk,
        status=parse_status(flat.get("stav")),
        source_url=source_url,
    )


class EkospolScraper(BaseScraper):
    developer = Developer.EKOSPOL
    base_url = BASE_URL

    def _discover_asset_slug(self, flats: list[dict]) -> str | None:
        """Stiahne detail JEDNEJ jednotky z projektu (nie za každý byt) a
        vytiahne z neho interný asset slug pre skladanie plan_url - jediný
        spoľahlivý zdroj (viď poznámka pri PROJECT_ASSET_SLUG_RE)."""
        detail_uri = next((f.get("uri") for f in flats if f.get("uri")), None)
        if not detail_uri:
            return None
        detail_url = f"{BASE_URL}/{detail_uri}"
        try:
            resp = self._get(detail_url)
        except Exception as exc:
            logger.warning(
                "Ekospol: zlyhalo stiahnutie detailu %s pri zisťovaní asset slugu (%s)",
                detail_url, exc,
            )
            return None
        match = PROJECT_ASSET_SLUG_RE.search(resp.text)
        return match.group(1) if match else None

    def _fetch_rooms(self, detail_url: str) -> list[dict]:
        """Stiahne rozpis miestností z detailu KONKRÉTNEJ jednotky - tabuľka
        "Místnost"/"m²" (napr. `<td class="da-Balkon">Balkon</td><td
        data-name="Balkon">4,10</td>`) je len na detaile bytu, nie v
        `window.flats`. Na rozdiel od `_discover_asset_slug()` (1 request
        na CELÝ projekt) je toto 1 request na KAŽDÝ byt - preto sa nevolá
        z `fetch_all_units()` (pri ~920 bytoch by to hromadný `/refresh`
        neúmerne spomalilo), ale lazy z `fetch_rooms_for_unit()` len pre
        byt, ktorý si niekto skutočne vyhľadá (viď `main.py`, `/unit`)."""
        try:
            resp = self._get(detail_url)
        except Exception as exc:
            logger.warning("Ekospol: zlyhalo stiahnutie detailu %s pri zisťovaní miestností (%s)", detail_url, exc)
            return []
        soup = BeautifulSoup(resp.text, "lxml")
        header = soup.find(lambda tag: tag.name == "th" and tag.get_text(strip=True) == "Místnost")
        if header is None:
            return []
        table = header.find_parent("table")
        if table is None:
            return []
        rooms: list[dict] = []
        body = table.find("tbody") or table
        for row in body.find_all("tr"):
            cells = row.find_all("td")
            if len(cells) < 3:
                continue  # hlavičkové riadky používajú <th>, nie <td>
            name = cells[1].get_text(strip=True)
            area = parse_float(cells[2].get_text(strip=True))
            if name and area is not None:
                rooms.append({"name": name, "area_m2": area})
        return rooms

    def discover_project_urls(self) -> list[str]:
        resp = self._get(PROJECT_LIST_URL)
        soup = BeautifulSoup(resp.text, "lxml")
        urls: set[str] = set()
        list_url_normalized = PROJECT_LIST_URL.rstrip("/")
        for a in soup.select(PROJECT_LINK_SELECTOR):
            href = a.get("href")
            if not href:
                continue
            if href.startswith("/"):
                href = BASE_URL + href
            url = href.split("?")[0].rstrip("/")
            if url == list_url_normalized:
                continue  # odkaz späť na listovaciu stránku, nie projekt
            urls.add(url)
        return sorted(urls)

    def fetch_all_units(self) -> list[UnitData]:
        units: list[UnitData] = []
        project_urls = self.discover_project_urls()
        logger.info("Ekospol: nájdených %d projektových odkazov", len(project_urls))

        for url in project_urls:
            project_slug = url.rstrip("/").rsplit("/", 1)[-1]
            project_name = project_slug.replace("-", " ").title()
            cenik_url = f"{url}/cenik"
            try:
                resp = self._get(cenik_url)
            except Exception as exc:  # scraper jedného projektu nesmie zhodiť celý beh
                logger.warning("Ekospol: zlyhalo stiahnutie %s (%s)", cenik_url, exc)
                continue

            if project_slug not in str(resp.url):
                # Odkaz je mŕtvy/zlúčený a web presmerovalo na INÝ projekt (napr.
                # "ekorezidence-strasnice/cenik" -> "ekocity-hostivar-c/cenik") -
                # bez tejto kontroly by sa dáta iného projektu uložili pod
                # nesprávnym menom (duplicitné byty).
                logger.warning(
                    "Ekospol: %s presmerovalo na iný projekt (%s) - preskakujem "
                    "(zjavne mŕtvy/zlúčený odkaz na listovacej stránke)",
                    cenik_url, resp.url,
                )
                continue

            match = FLATS_JSON_RE.search(resp.text)
            if not match:
                logger.warning(
                    "Ekospol: na %s sa nenašiel 'window.flats' JSON blok - "
                    "štruktúra stránky sa pravdepodobne zmenila",
                    cenik_url,
                )
                continue

            try:
                flats = json.loads(match.group(1))
            except json.JSONDecodeError as exc:
                logger.warning("Ekospol: nepodarilo sa naparsovať window.flats na %s (%s)", cenik_url, exc)
                continue

            asset_slug = self._discover_asset_slug(flats)

            units.extend(
                _flat_to_unit_data(flat, self.developer, project_name, url, cenik_url, asset_slug)
                for flat in flats
            )
        return units

    def fetch_extra_details_for_unit(self, record) -> dict:
        """Lazy dotiahnutie rozpisu miestností pre JEDEN konkrétny byt (viď
        `BaseScraper.fetch_extra_details_for_unit` - prečo nie je súčasťou
        `fetch_all_units()`)."""
        if not record.detail_url:
            return {}
        rooms = self._fetch_rooms(record.detail_url)
        return {"rooms": rooms} if rooms else {}

    def needs_extra_details(self, record) -> bool:
        return not record.rooms


if __name__ == "__main__":
    with EkospolScraper() as scraper:
        result = scraper.fetch_all_units()
        print(f"Nájdených {len(result)} bytov.")
        for u in result[:5]:
            print(u.model_dump())
