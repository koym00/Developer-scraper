"""
Zdieľaná logika pre developerov, ktorí publikujú byty ako obyčajnú HTML
<table> (aktuálne Sekyra - na rozdiel od Central Group / Skanska, ktorí
renderujú zoznam bytov cez JavaScript, alebo Ekospolu/Finepu, ktorí
používajú vlastný JSON/karty).

Namiesto CSS selektorov/tried (ktoré sa medzi weblami líšia a menia sa
pri redesignoch) mapujeme stĺpce PODĽA TEXTU HLAVIČKY - stačí pre nový
web doplniť HEADER_ALIASES so správnymi českými/slovenskými výrazmi.
"""
from __future__ import annotations

from bs4 import BeautifulSoup

from main.models import Developer, UnitData, UnitStatus
from main.scrapers.parsing_utils import (
    normalize_header,
    parse_features,
    parse_float,
    parse_price_czk,
    parse_status,
)


def _resolve_alias(header: str, header_aliases: dict[str, str]) -> str | None:
    """Namapuje normalizovaný text hlavičky na logický názov stĺpca.

    Skutočné hlavičky na weboch často obsahujú extra slová oproti alias
    kľúčom (napr. "Podlahová plocha bytu" vs. alias "podlahovaplocha", alebo
    "Cena bytu vč. DPH (Kč)" vs. alias "cena") - preto sa okrem presnej zhody
    skúša aj obojstranné "obsahuje" porovnanie. Pri viacerých zhodách vyhráva
    najdlhší (najšpecifickejší) alias kľúč, aby napr. "cena" vyhrala nad
    kratším "byt" v hlavičke, ktorá obsahuje oboje ("Cena bytu...")."""
    if header in header_aliases:
        return header_aliases[header]
    matches = [(key, val) for key, val in header_aliases.items() if key in header]
    if not matches:
        return None
    return max(matches, key=lambda kv: len(kv[0]))[1]


def _cell_text(cell) -> str:
    """Vráti text bunky tabuľky.

    Niektoré weby duplikujú obsah bunky pre mobilné
    zobrazenie pomocou Bootstrap utility tried (napr. "d-none d-lg-table-cell"
    pre desktop verziu vs. "d-block d-lg-none" pre mobilnú kartu so všetkými
    poľami znova ako text) - BeautifulSoup nevidí CSS `display:none`, takže
    obyčajný `get_text()` by vrátil oba texty zlepené dokopy. Ak bunka
    obsahuje takýto "desktop" variant, použije sa len ten."""
    desktop_variant = cell.select_one(".d-lg-table-cell")
    target = desktop_variant if desktop_variant is not None else cell
    return target.get_text(strip=True).replace("\xa0", " ")


def find_price_table(soup: BeautifulSoup, header_aliases: dict[str, str], min_score: int = 3):
    """Nájde <table>, ktorej hlavička obsahuje aspoň `min_score` rozpoznaných stĺpcov."""
    best_table, best_score = None, 0
    for table in soup.find_all("table"):
        header_row = table.find("tr")
        if not header_row:
            continue
        headers = [normalize_header(c.get_text()) for c in header_row.find_all(["th", "td"])]
        score = sum(1 for h in headers if _resolve_alias(h, header_aliases))
        if score > best_score:
            best_table, best_score = table, score
    return best_table if best_score >= min_score else None


def build_column_map(table, header_aliases: dict[str, str]) -> dict[int, str]:
    header_row = table.find("tr")
    headers = [normalize_header(c.get_text()) for c in header_row.find_all(["th", "td"])]
    col_map: dict[int, str] = {}
    for i, h in enumerate(headers):
        resolved = _resolve_alias(h, header_aliases)
        if resolved:
            col_map[i] = resolved
    return col_map


def extract_units_from_table(
    table,
    header_aliases: dict[str, str],
    developer: Developer,
    source_url: str,
    default_project_name: str,
) -> list[UnitData]:
    col_map = build_column_map(table, header_aliases)
    body = table.find("tbody") or table
    units: list[UnitData] = []

    for row in body.find_all("tr"):
        cells = row.find_all("td")
        if not cells:
            continue
        values: dict[str, str | None] = {}
        for i, cell in enumerate(cells):
            if i not in col_map:
                continue
            key = col_map[i]
            if key == "plan_url":
                link = cell.find("a", href=True)
                values[key] = link["href"] if link else None
            else:
                values[key] = _cell_text(cell) or None
                if key == "unit_number" and cell.get("data-href"):
                    # niektoré weby majú odkaz na detail
                    # jednotky ako "data-href" atribút priamo na bunke s
                    # číslom bytu, nie ako samostatný stĺpec s <a href>.
                    values["detail_url"] = cell["data-href"]
        if not values.get("unit_number"):
            continue

        units.append(
            UnitData(
                developer=developer,
                project_name=values.get("project") or default_project_name,
                unit_number=values["unit_number"],
                floor=values.get("floor"),
                disposition=values.get("disposition"),
                area_m2=parse_float(values.get("area")),
                outdoor_area_m2=parse_float(values.get("outdoor_area")),
                features=parse_features(values.get("features")),
                price_czk=parse_price_czk(values.get("price")),
                price_note=values.get("price"),
                status=parse_status(values.get("status")) or UnitStatus.UNKNOWN,
                move_in_date=values.get("move_in"),
                plan_url=values.get("plan_url"),
                detail_url=values.get("detail_url"),
                locality=values.get("locality"),
                source_url=source_url,
            )
        )
    return units
