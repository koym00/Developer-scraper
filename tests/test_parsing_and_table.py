"""
Offline testy pre parsovaciu logiku (parsing_utils.py, generic_table.py),
ktoré nepotrebujú sieť ani databázu - dajú sa spustiť kdekoľvek.

Spustenie: python3 tests/test_parsing_and_table.py
(Zámerne bez pytestu - v tomto vývojovom prostredí nebol k dispozícii;
lokálne pokojne spusti cez `pytest tests/` ak ho máš nainštalovaný.)
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bs4 import BeautifulSoup

from main.models import Developer, UnitStatus
from main.scrapers.generic_table import extract_units_from_table, find_price_table
from main.scrapers.parsing_utils import (
    normalize_header,
    parse_features,
    parse_float,
    parse_price_czk,
    parse_status,
)

# Syntetická HTML tabuľka pokrývajúca všetky stĺpce, ktoré generic_table.py
# vie namapovať (locality/project/unit_number/floor/disposition/area/
# features/price/move_in/status/plan_url) - nezávislá od konkrétneho webu.
SAMPLE_HTML = """
<html><body>
<table>
  <thead>
    <tr>
      <th>Lokalita</th><th>Projekt</th><th>Číslo bytu</th><th>Podlaží</th>
      <th>Dispozice</th><th>Plocha</th><th>Příslušenství</th><th>Cena</th>
      <th>Nastěhování</th><th>Stav</th><th>PDF</th>
    </tr>
  </thead>
  <tbody>
    <tr>
      <td>Smíchov</td><td>Rezidence Smíchov</td><td>161</td><td>6</td>
      <td>4+kk</td><td>132,5 m²</td><td>T S</td><td>na dotaz</td>
      <td>ihned</td><td>volný</td><td><a href="https://example.test/karta/161.pdf">PDF</a></td>
    </tr>
    <tr>
      <td>Smíchov</td><td>Rezidence Smíchov</td><td>2021</td><td>2</td>
      <td>2+kk</td><td>53,6 m²</td><td>B</td><td>10 960 000 Kč</td>
      <td>Q4/2026</td><td>volný</td><td></td>
    </tr>
    <tr>
      <td>Kamýk</td><td>Rezidence Kamýk</td><td>1021</td><td>1</td>
      <td></td><td>54,6 m²</td><td></td><td>9 390 000 Kč</td>
      <td>Q2/2027</td><td>rezervovaný</td><td></td>
    </tr>
  </tbody>
</table>
</body></html>
"""

HEADER_ALIASES = {
    "lokalita": "locality",
    "projekt": "project",
    "cislobytu": "unit_number",
    "podlazi": "floor",
    "dispozice": "disposition",
    "plocha": "area",
    "prislusenstvi": "features",
    "cena": "price",
    "nastehovani": "move_in",
    "stav": "status",
    "pdf": "plan_url",
}


def check(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)
    print(f"OK: {message}")


def test_normalize_header():
    check(normalize_header("Číslo bytu") == "cislobytu", "normalize_header odstráni diakritiku a medzery")


def test_parse_float():
    check(parse_float("132,5 m²") == 132.5, "parse_float parsuje čárku ako desatinnú")
    check(parse_float(None) is None, "parse_float(None) -> None")


def test_parse_price_czk():
    check(parse_price_czk("10 960 000 Kč") == 10960000, "parse_price_czk odstráni medzery a Kč")
    check(parse_price_czk("na dotaz") is None, "parse_price_czk('na dotaz') -> None")


def test_parse_status():
    check(parse_status("volný") == UnitStatus.AVAILABLE, "parse_status('volný') -> AVAILABLE")
    check(parse_status("rezervovaný") == UnitStatus.RESERVED, "parse_status('rezervovaný') -> RESERVED")


def test_parse_features():
    check(parse_features("T S") == ["terasa", "sklep"], "parse_features('T S') -> [terasa, sklep]")


def test_full_table_extraction():
    soup = BeautifulSoup(SAMPLE_HTML, "lxml")
    table = find_price_table(soup, HEADER_ALIASES)
    check(table is not None, "find_price_table nájde tabuľku podľa hlavičiek")

    units = extract_units_from_table(
        table,
        HEADER_ALIASES,
        developer=Developer.SEKYRA,
        source_url="https://example.test/cenik",
        default_project_name="Fallback",
    )
    check(len(units) == 3, f"extract_units_from_table vráti 3 byty (dostal {len(units)})")

    u = next(u for u in units if u.unit_number == "161")
    check(u.project_name == "Rezidence Smíchov", "byt 161 patrí do Rezidence Smíchov")
    check(u.price_czk is None, "byt 161 má cenu 'na dotaz' -> price_czk je None")
    check(u.area_m2 == 132.5, "byt 161 má výmeru 132.5")
    check(u.features == ["terasa", "sklep"], "byt 161 má features [terasa, sklep]")
    check(
        u.plan_url == "https://example.test/karta/161.pdf",
        "byt 161 má plan_url z href v stĺpci PDF",
    )

    u2 = next(u for u in units if u.unit_number == "2021")
    check(u2.price_czk == 10960000, "byt 2021 má cenu 10 960 000")
    check(u2.status == UnitStatus.AVAILABLE, "byt 2021 je volný")
    check(u2.plan_url is None, "byt 2021 nemá odkaz v stĺpci PDF -> plan_url je None")

    u3 = next(u for u in units if u.unit_number == "1021")
    check(u3.status == UnitStatus.RESERVED, "byt 1021 je rezervovaný")
    check(u3.disposition is None, "byt 1021 má prázdnu bunku Dispozice -> disposition je None (nie '')")


if __name__ == "__main__":
    test_normalize_header()
    test_parse_float()
    test_parse_price_czk()
    test_parse_status()
    test_parse_features()
    test_full_table_extraction()
    print("\nVšetky testy prešli.")
