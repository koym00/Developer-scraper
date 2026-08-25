"""
Scraper pre Sekyra Group (sekyragroup.cz).

OVERENÉ NAŽIVO (2026-08): na rozdiel od Ekospolu/Finepu má Sekyra
JEDEN centrálny prehľad všetkých aktuálne ponúkaných bytov naprieč
projektmi na `/units/` (odkaz naň vedie formulár "Vyhledat nemovitost" na
`/pages/byty-prodej-praha`) - obsahuje klasickú HTML `<table>` s hotovými
dátovými riadkami (žiadny JS/JSON netreba, rovnaký vzor ako u generických
scraperov v `generic_table.py`).
Stránka nemá stránkovanie - `/units/?page=2` vráti identický obsah ako
`/units/` - takže ide o kompletný aktuálny zoznam v jednom requeste.

POZNÁMKA k doméne: `https://www.sekyragroup.cz` má nesprávny/nesediaci
TLS certifikát (hostname mismatch) - treba použiť `https://sekyragroup.cz`
bez `www.` prefixu.

Hlavička "Cena (vč. DPH)" potvrdzuje, že zverejnená cena je vždy vrátane
DPH. Stĺpec "Dostupnost" obsahuje okrem "Volné" aj "Předrezervováno" -
doplnené do `_STATUS_MAP` v `parsing_utils.py` (mapuje sa na RESERVED).

Pôdorys (obrázok "Plán jednotky", príp. PDF "Karta bytu ke stažení") nie
je na `/units/` prehľade, len na detailnej stránke jednej jednotky
(`/units/<id>`, odkaz je v bunke "Číslo"). Keďže je bytov málo (rádovo
desiatky), scraper si môže dovoliť po jednom requeste na byt navyše -
na rozdiel od Ekospolu (1000+ bytov), kde by to bolo neúmerne veľa
requestov a preto sa tam pôdorys skladá bez extra requestu.

Lokalita (napr. "Praha 5, Jinonice") tiež nie je na `/units/` prehľade -
je len na spoločnej stránke so zoznamom projektov `/pages/byty-prodej-praha`
(text "Lokalita: <text>" pri každom projekte). Stiahne sa 1x CELKOVO (nie
za projekt/byt) a podľa nadpisu projektu, ktorý predchádza danému textu
lokality v dokumente, sa priradí k jednotkám s odpovedajúcim
`project_name` (porovnanie je "obsahuje", keďže táto stránka používa
marketingové názvy typu "Rohan City – Vision Karlín", ale `/units/`
tabuľka len "Vision Karlín"). Nie každý projekt má na tejto stránke
štruktúrovanú lokalitu (napr. "Nekázanka 17" ju uvádza len v opisnom
texte, nie v poli "Lokalita:") - pre tie zostáva `locality=None`.
"""
from __future__ import annotations

import logging

from bs4 import BeautifulSoup

from main.models import Developer, UnitData
from main.scrapers.base import BaseScraper, ScraperError
from main.scrapers.generic_table import extract_units_from_table, find_price_table
from main.scrapers.parsing_utils import parse_float, strip_diacritics_lower_alnum

logger = logging.getLogger(__name__)

UNITS_URL = "https://sekyragroup.cz/units/"
PROJECTS_OVERVIEW_URL = "https://sekyragroup.cz/pages/byty-prodej-praha"

# Normalizovaný text labelu v "attr-box__item" na detaile jednotky -> náš
# logický názov vonkajšieho priestoru (rovnaká sada ako `outdoor_area_by_type`
# u iných developerov). Nájdené 2026-08 (nahlásil používateľ, screenshot
# detailu bytu) - "Balkóny"/"Terasy"/"Předzahrádka"/"Lodžie" sú na detaile
# KAŽDÉHO bytu podmienene renderované len keď ich byt reálne má, s presnou
# plochou v m² - rovnaký vzor pre všetky štyri typy.
_ATTR_LABEL_TO_KEY = {
    "balkony": "balkon",
    "balkon": "balkon",
    "terasy": "terasa",
    "terasa": "terasa",
    "predzahradka": "predzahradka",
    "predzahradky": "predzahradka",
    "lodzie": "lodzie",
    "lodzia": "lodzie",
    "zahrada": "zahradka",
    "zahrady": "zahradka",
}

HEADER_ALIASES: dict[str, str] = {
    "cislo": "unit_number",
    "cislobytu": "unit_number",
    "projekt": "project",
    "dispozice": "disposition",
    "podlahovaplocha": "area",
    "plocha": "area",
    "podlazi": "floor",
    "prislusenstvi": "features",
    "plochaprislusenstvi": "outdoor_area",
    "cenavcdph": "price",
    "cena": "price",
    "dostupnost": "status",
}


def _collect_detail_urls(table) -> dict[str, str]:
    """Bunka v stĺpci "Číslo" obsahuje odkaz na detail jednotky - mimo
    dosahu `extract_units_from_table` (tá pre daný stĺpec berie buď text,
    alebo href, nie oboje naraz), preto sa tabuľka prejde ešte raz zvlášť."""
    urls: dict[str, str] = {}
    body = table.find("tbody") or table
    for row in body.find_all("tr"):
        cells = row.find_all("td")
        if not cells:
            continue
        link = cells[0].find("a", href=True)
        unit_number = cells[0].get_text(strip=True)
        if link and unit_number:
            urls[unit_number] = link["href"]
    return urls


class SekyraScraper(BaseScraper):
    developer = Developer.SEKYRA
    base_url = "https://sekyragroup.cz"

    def _discover_project_localities(self) -> list[tuple[str, str]]:
        """Vráti zoznam (nadpis projektu, text lokality) z `/pages/byty-
        prodej-praha` - jeden request celkovo. Nie každý projekt tam má
        štruktúrovanú lokalitu (viď docstring modulu)."""
        try:
            resp = self._get(PROJECTS_OVERVIEW_URL)
        except Exception as exc:
            logger.warning("Sekyra: zlyhalo stiahnutie %s pri zisťovaní lokalít (%s)", PROJECTS_OVERVIEW_URL, exc)
            return []
        soup = BeautifulSoup(resp.text, "lxml")
        pairs: list[tuple[str, str]] = []
        for text_node in soup.find_all(string=lambda s: s and "Lokalita:" in s):
            strong = text_node.parent.find("strong")
            if strong is None:
                continue
            heading = text_node.find_previous(["h1", "h2", "h3", "h4", "h5"])
            if heading is None:
                continue
            pairs.append((heading.get_text(strip=True), strong.get_text(strip=True)))
        return pairs

    def _locality_for_project(self, project_name: str, locality_pairs: list[tuple[str, str]]) -> str | None:
        for heading, locality in locality_pairs:
            if project_name.lower() in heading.lower():
                return locality
        return None

    def _fetch_detail_extras(self, detail_url: str) -> tuple[str | None, dict[str, float]]:
        """Stiahne detail jednotky (potrebný aj tak pre `plan_url`) a popri
        pôdoryse z neho naparsuje aj plochu vonkajších priestorov PER TYP -
        viď `_ATTR_LABEL_TO_KEY`. Žiadny extra request navyše, len sa z už
        aj tak sťahovanej stránky vyťaží viac."""
        try:
            resp = self._get(detail_url)
        except Exception as exc:
            logger.warning("Sekyra: zlyhalo stiahnutie detailu %s (%s)", detail_url, exc)
            return None, {}
        soup = BeautifulSoup(resp.text, "lxml")

        img = soup.find("img", alt="Plán jednotky")
        src = img.get("src") if img else None
        plan_url = (src if src.startswith("http") else f"{self.base_url}{src}") if src else None

        outdoor_area_by_type: dict[str, float] = {}
        for item in soup.select(".attr-box__item"):
            label_el, value_el = item.find("span"), item.find("b")
            if label_el is None or value_el is None:
                continue
            key = _ATTR_LABEL_TO_KEY.get(strip_diacritics_lower_alnum(label_el.get_text()))
            if not key:
                continue
            area = parse_float(value_el.get_text())
            if area:
                outdoor_area_by_type[key] = area

        return plan_url, outdoor_area_by_type

    def fetch_all_units(self) -> list[UnitData]:
        resp = self._get(UNITS_URL)
        soup = BeautifulSoup(resp.text, "lxml")

        table = find_price_table(soup, HEADER_ALIASES)
        if table is None:
            raise ScraperError(
                "Sekyra: na stránke sa nenašla tabuľka s bytmi - "
                "štruktúra stránky sa pravdepodobne zmenila, treba upraviť HEADER_ALIASES."
            )

        units = extract_units_from_table(
            table,
            HEADER_ALIASES,
            developer=self.developer,
            source_url=UNITS_URL,
            default_project_name="Sekyra Group",
        )
        if not units:
            raise ScraperError(
                "Sekyra: tabuľka bola nájdená, ale nepodarilo sa naparsovať žiadny riadok - "
                "skontroluj HEADER_ALIASES voči skutočným hlavičkám stĺpcov."
            )

        # Hlavička cenového stĺpca je "Cena (vč. DPH)" - cena je vždy s DPH.
        for unit in units:
            if unit.price_czk is not None:
                unit.price_includes_vat = True

        locality_pairs = self._discover_project_localities()
        for unit in units:
            unit.locality = self._locality_for_project(unit.project_name, locality_pairs)

        detail_urls = _collect_detail_urls(table)
        for unit in units:
            detail_url = detail_urls.get(unit.unit_number)
            if not detail_url:
                continue
            if detail_url.startswith("/"):
                detail_url = f"{self.base_url}{detail_url}"
            unit.detail_url = detail_url
            unit.plan_url, unit.outdoor_area_by_type = self._fetch_detail_extras(detail_url)

        return units


if __name__ == "__main__":
    with SekyraScraper() as scraper:
        result = scraper.fetch_all_units()
        print(f"Nájdených {len(result)} bytov.")
        for u in result[:5]:
            print(u.model_dump())
