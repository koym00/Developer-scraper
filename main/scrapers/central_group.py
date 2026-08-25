"""
Scraper pre Central Group (www.central-group.cz).

OVERENÉ NAŽIVO (2026-08): stránka je celá "webclient" SPA (Vue), bez JS
sa nezobrazí vôbec nič. Endpointy interného REST API sa našli **bez
pripojeného prehliadača** - stiahnutím hlavných JS bundlov
(`<script src>` z HTML, napr. `/wms3/js/app.<hash>.js`) a hľadaním
`/api/` v ich obsahu. Kľúčové zistenia:

  1. `GET /api/system/time-version` vráti čisté číslo (`timeId`) - CMS
     time-verziu obsahu, ktorú treba poslať pri každom ďalšom volaní.
  2. `GET /api/location` vráti zoznam všetkých rezidenčných projektov
     ("lokalít") s `id`, `name`, `city`, `cityPart`.
  3. `GET /api/apartment/search` vráti zoznam bytov pre danú lokalitu.
     Vyžaduje query parametre `langId`, `timeId`, `sort`, `sortDirections`,
     `limit`, `offset` - inak vráti 400 (chýbajúci/neplatný enum) alebo
     (pri langId≠1) 500/prázdny výsledok. **DÔLEŽITÉ:** volanie BEZ
     `locationIds` filtra (t.j. "všetky lokality naraz") spoľahlivo padá
     na 500 (server-side bug/timeout) - funguje to len keď sa pošle
     `locationIds` pre KONKRÉTNU jednu lokalitu, takže scraper musí
     iterovať `/api/location` a volať `/api/apartment/search` pre každú
     zvlášť (rovnaký vzor ako Ekospol/Finep - "zoznam projektov -> dáta
     per projekt", len tu ide o JSON API namiesto HTML).

Odpoveď pre jednu lokalitu je bohatá - obsahuje priamo `totalFloorArea`
(m²), `totalPriceWithVAT` (Kč, **vždy vrátane DPH** podľa názvu poľa),
`layoutLabel` (dispozícia), `floorAbbr` (napr. "NP2"), a boolovské
príznaky vlastností (`hasTerrace`, `hasBalcony`, `hasFrontGarden`,
`hasParkingPlace`, `hasStorageFacility`). Stav bytu sa odvodzuje z
`sold`/`isUnderOffer` (v `/api/apartment/search` sa naživo nikdy
nenašiel `sold: true` - zdá sa, že predané byty sa do výsledkov vôbec
nezahŕňajú, podobne ako pri Finepe).

Pole `orientation` je zoznam číselných kódov (napr. `[1,3]`) bez
sprievodnej lookup tabuľky v žiadnom z volaných endpointov - bez
overeného mapovania na svetové strany sa zámerne NEPREKLADÁ (radšej
`None` než hádanie a riziko zavádzajúceho údaju).

**Pôdorys jednotky (`plan_url`) - doplnené 2026-08 vďaka používateľovi,**
ktorý poslal skutočný request z DevTools (Network → vyhľadanie GUID-u v
tele odpovedí → "Copy as cURL"). Bez tohto by sa to z kódu stránky
nedalo spoľahlivo odvodiť - vlastný CMS "resource" systém stránky
(direktíva `v-wms-resource:image` v JS bundli) skladá URL dynamicky cez
niekoľko vrstiev (page → block → component), ktoré sa cez minifikovaný
kód nedali rozmotať. Skutočný request:

    GET /api/resource/image/24885/ground-plan
        ?timeId=<timeId>&langId=1&boIdMapping[13]=<catalogNumber>

`24885` je prekvapivo **globálna konštanta** (overené na 2 rôznych
projektoch/bytoch - funguje rovnako všade), nie ID viazané na
projekt/blok/poschodie. `boIdMapping[13]` (13 = enum "Apartment" v JS)
stačí samotné - netreba aj `[10]`/`[11]`/`[12]` (lokalita/blok/poschodie),
hoci reálny request z prehliadača ich posielal navyše. Odpoveď je
zoznam variantov obrázka v rôznych veľkostiach (`[[{"path":...,
"size":"400"}, ..., {"size":"2400"}]]`) - berie sa najväčší. Finálna URL
sa skladá ako `/Uloziste/<path>` (potvrdené priamo v JS: funkcia, ktorá
pred relatívnu cestu prilepí `//www.central-group.cz/Uloziste/`).

Vyžaduje **1 extra request na jednotku** (netreba pre búdorys jednotky
prehliadač, len `catalogNumber`, ktorý už máme z `/api/apartment/
search`). **Doplnené 2026-08 (na žiadosť používateľa, po sťažnosti na
pomalý `/refresh`):** pôvodne sa toto (aj rozpis miestností nižšie)
volalo pre KAŽDÝ byt priamo v `fetch_all_units()` - pri ~540 bytoch to
znamenalo ~1000+ extra requestov a hromadný refresh trval rádovo
desiatky minút. Teraz sa oboje sťahuje LAZY cez
`fetch_extra_details_for_unit()`, volané až z `/unit` endpointu pre
KONKRÉTNY vyhľadaný byt (výsledok sa uloží do DB) - `fetch_all_units()`
stiahne len to, čo `/api/apartment/search` vráti priamo (bez extra
requestov na jednotku), takže je späť rýchly.
"""
from __future__ import annotations

import logging
import re

import httpx

from main.models import Developer, UnitData, UnitStatus
from main.scrapers.base import BaseScraper, ScraperError

BASE_URL = "https://www.central-group.cz"
TIME_VERSION_URL = f"{BASE_URL}/api/system/time-version"
LOCATION_LIST_URL = f"{BASE_URL}/api/location"
APARTMENT_SEARCH_URL = f"{BASE_URL}/api/apartment/search"
APARTMENT_DETAIL_URL = f"{BASE_URL}/api/apartment"

# catalogNumber sa dá spätne vytiahnuť z detail_url - viď dve URL formy
# v _apartment_to_unit_data() (bežná / "premium" jednotka).
_CATALOG_NUMBER_RE = re.compile(r"/byt-detail/([^/?]+)|[?&]idByt=([^&]+)")


def _extract_catalog_number(detail_url: str | None) -> str | None:
    if not detail_url:
        return None
    match = _CATALOG_NUMBER_RE.search(detail_url)
    if not match:
        return None
    return match.group(1) or match.group(2)

# Globálna konštanta CMS "resource" bloku pre pôdorys - viď docstring vyššie.
GROUND_PLAN_BLOCK_ID = 24885
GROUND_PLAN_RESOURCE_URL = f"{BASE_URL}/api/resource/image/{GROUND_PLAN_BLOCK_ID}/ground-plan"
APARTMENT_BOID_KEY = 13  # enum Me.Apartment v JS bundli
STORAGE_BASE_URL = f"{BASE_URL}/Uloziste"

LANG_ID_CS = 1
PAGE_LIMIT = 500

logger = logging.getLogger(__name__)

_FEATURE_FLAG_MAP = {
    "hasTerrace": "terasa",
    "hasBalcony": "balkon",
    "hasFrontGarden": "predzahradka",
    "hasStorageFacility": "sklep",
    "hasParkingPlace": "parkovani",
    "hasWinterGarden": "zimni_zahrada",
}


def _apartment_to_unit_data(
    apt: dict, developer: Developer, project_name: str, source_url: str
) -> UnitData:
    features = [label for flag, label in _FEATURE_FLAG_MAP.items() if apt.get(flag)]

    if apt.get("sold"):
        status = UnitStatus.SOLD
    elif apt.get("isUnderOffer"):
        status = UnitStatus.RESERVED
    else:
        status = UnitStatus.AVAILABLE

    completion_date = apt.get("completionDate")
    move_in_date = completion_date[:10] if completion_date else None

    price_czk = apt.get("totalPriceWithVAT") or None

    city, city_part = apt.get("locationCity"), apt.get("locationCityPart")
    locality = f"{city} - {city_part}" if city and city_part else city or city_part or None

    # Developer sám na svojej stránke zobrazuje byt ako "<blok> <číslo>"
    # (napr. "H 338"), nie ako interný katalógový kód "189-08-338" - viď
    # JS bundle: `${housingBlockName} ${number} / ${layoutLabel}`.
    block, number = apt.get("housingBlockName"), apt.get("number")
    unit_number = f"{block} {number}" if block and number is not None else apt.get("catalogNumber") or str(number)

    catalog_number = apt.get("catalogNumber")
    if catalog_number:
        detail_url = (
            f"{BASE_URL}/byt-detail-premium.aspx?idByt={catalog_number}"
            if apt.get("isPremium")
            else f"{BASE_URL}/byt-detail/{catalog_number}"
        )
    else:
        detail_url = None

    return UnitData(
        developer=developer,
        project_name=project_name,
        unit_number=unit_number,
        detail_url=detail_url,
        locality=locality,
        floor=apt.get("floorAbbr"),
        disposition=apt.get("layoutLabel") or None,
        area_m2=apt.get("totalFloorArea"),
        outdoor_area_m2=apt.get("amenitiesArea") or None,
        features=features,
        price_czk=price_czk,
        price_includes_vat=True if price_czk is not None else None,
        status=status,
        move_in_date=move_in_date,
        source_url=source_url,
    )


class CentralGroupScraper(BaseScraper):
    developer = Developer.CENTRAL_GROUP
    base_url = BASE_URL

    def _fetch_time_id(self) -> int:
        resp = self._get(f"{TIME_VERSION_URL}?langId={LANG_ID_CS}")
        try:
            return int(resp.text)
        except ValueError as exc:
            raise ScraperError(f"Central Group: neočakávaná odpoveď z {TIME_VERSION_URL}: {resp.text!r}") from exc

    def _fetch_locations(self, time_id: int) -> list[dict]:
        resp = self.client.get(LOCATION_LIST_URL, params={"langId": LANG_ID_CS, "timeId": time_id})
        if resp.status_code != 200:
            raise ScraperError(f"Central Group: GET {LOCATION_LIST_URL} -> HTTP {resp.status_code}")
        return resp.json()

    def _fetch_apartments_for_location(self, time_id: int, location_id: int) -> list[dict]:
        apartments: list[dict] = []
        offset = 0
        while True:
            params = {
                "langId": LANG_ID_CS,
                "timeId": time_id,
                "sort": "TotalPrice",
                "sortDirections": "Up",
                "limit": PAGE_LIMIT,
                "offset": offset,
                "locationIds": location_id,
            }
            resp = self.client.get(APARTMENT_SEARCH_URL, params=params)
            if resp.status_code != 200:
                logger.warning(
                    "Central Group: GET %s (locationIds=%s, offset=%s) -> HTTP %s",
                    APARTMENT_SEARCH_URL, location_id, offset, resp.status_code,
                )
                break
            page = resp.json()
            apartments.extend(page)
            if len(page) < PAGE_LIMIT:
                break
            offset += PAGE_LIMIT
        return apartments

    def _fetch_plan_url(self, catalog_number: str, time_id: int) -> str | None:
        """Stiahne URL pôdorysu pre jeden byt - viď docstring modulu."""
        params = {
            "timeId": time_id,
            "langId": LANG_ID_CS,
            f"boIdMapping[{APARTMENT_BOID_KEY}]": catalog_number,
        }
        try:
            resp = self.client.get(GROUND_PLAN_RESOURCE_URL, params=params)
        except httpx.HTTPError as exc:
            logger.warning("Central Group: zlyhalo stiahnutie pôdorysu pre %s (%s)", catalog_number, exc)
            return None
        if resp.status_code != 200:
            return None
        try:
            variant_groups = resp.json()
        except ValueError:
            return None
        variants = variant_groups[0] if variant_groups else []
        if not variants:
            return None
        best = max(variants, key=lambda v: v.get("width", 0) * v.get("height", 0))
        path = best.get("path")
        return f"{STORAGE_BASE_URL}/{path}" if path else None

    def _fetch_apartment_detail(self, catalog_number: str, time_id: int) -> dict | None:
        """Stiahne bohatší endpoint `/api/apartment/{catalogNumber}` (ten
        istý, z ktorého sa pôvodne pri hľadaní plan_url zistilo len
        `hasTechnicalGroundPlan`) - má aj `rooms` (rozpis miestností) A
        `innerFloorArea` (užitná plocha - priamo zverejnená developerom,
        na rozdiel od `totalFloorArea`/`area_m2`, ktorá je podlahová
        plocha vrátane priečok). Volá sa lazy z
        `fetch_extra_details_for_unit()`, nie z `fetch_all_units()`."""
        params = {"timeId": time_id, "langId": LANG_ID_CS}
        try:
            resp = self.client.get(f"{APARTMENT_DETAIL_URL}/{catalog_number}", params=params)
        except httpx.HTTPError as exc:
            logger.warning("Central Group: zlyhalo stiahnutie detailu bytu pre %s (%s)", catalog_number, exc)
            return None
        if resp.status_code != 200:
            return None
        try:
            return resp.json()
        except ValueError:
            return None

    def fetch_all_units(self) -> list[UnitData]:
        time_id = self._fetch_time_id()
        locations = self._fetch_locations(time_id)
        logger.info("Central Group: nájdených %d lokalít (projektov)", len(locations))

        units: list[UnitData] = []
        for location in locations:
            location_id = location["id"]
            project_name = location.get("name") or location.get("fullName") or str(location_id)
            source_url = f"{APARTMENT_SEARCH_URL}?locationIds={location_id}"

            try:
                apartments = self._fetch_apartments_for_location(time_id, location_id)
            except Exception as exc:  # scraper jednej lokality nesmie zhodiť celý beh
                logger.warning("Central Group: zlyhalo stiahnutie bytov pre lokalitu %s (%s)", project_name, exc)
                continue

            units.extend(
                _apartment_to_unit_data(apt, self.developer, project_name, source_url) for apt in apartments
            )

        if not units:
            raise ScraperError(
                "Central Group: API nevrátilo žiadne byty - over TIME_VERSION_URL/LOCATION_LIST_URL/"
                "APARTMENT_SEARCH_URL naživo, štruktúra API sa mohla zmeniť."
            )
        return units

    def fetch_extra_details_for_unit(self, record) -> dict:
        """Lazy dotiahnutie pôdorysu, rozpisu miestností aj užitnej plochy
        pre JEDEN konkrétny byt (viď `BaseScraper.
        fetch_extra_details_for_unit` - prečo nie sú súčasťou
        `fetch_all_units()`). Pôdorys má vlastný request (iný endpoint),
        rozpis miestností a užitná plocha zdieľajú JEDEN request na
        `_fetch_apartment_detail()`."""
        catalog_number = _extract_catalog_number(record.detail_url)
        if not catalog_number:
            return {}
        time_id = self._fetch_time_id()
        result: dict = {}
        try:
            plan_url = self._fetch_plan_url(catalog_number, time_id)
            if plan_url:
                result["plan_url"] = plan_url
        except Exception as exc:
            logger.warning("Central Group: zlyhalo stiahnutie pôdorysu pre %s (%s)", catalog_number, exc)
        try:
            detail = self._fetch_apartment_detail(catalog_number, time_id)
        except Exception as exc:
            logger.warning("Central Group: zlyhalo stiahnutie detailu bytu pre %s (%s)", catalog_number, exc)
            detail = None
        if detail:
            rooms = [
                {"name": r["name"], "area_m2": r["area"]}
                for r in (detail.get("rooms") or [])
                if r.get("name") and r.get("area") is not None
            ]
            if rooms:
                result["rooms"] = rooms
            inner_area = detail.get("innerFloorArea")
            if inner_area:
                result["usable_area_m2"] = inner_area
        return result

    def needs_extra_details(self, record) -> bool:
        return not record.rooms or not record.plan_url


if __name__ == "__main__":
    with CentralGroupScraper() as scraper:
        result = scraper.fetch_all_units()
        print(f"Nájdených {len(result)} bytov.")
        for u in result[:5]:
            print(u.model_dump())
