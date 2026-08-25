"""
Výpočet (odhad) ceny bytovej jednotky.

Logika:
  1) Ak developer cenu priamo publikuje -> použije sa priamo (metóda
     PUBLISHED), žiadny odhad netreba.
  2) Ak nie ("na dotaz") -> porovnávacia (comparable) metóda: zoberú sa
     ostatné byty v TOM ISTOM PROJEKTE so zverejnenou cenou, spočíta sa
     medián ceny za m², a z neho sa dopočíta cena pre daný byt s
     jednoduchými úpravami za poschodie a vonkajšie priestory (balkón/
     terasa/záhrada sa počítajú len ako zlomok hodnoty vnútornej plochy -
     bežná trhová prax).

Toto je zámerne jednoduchý, transparentný model (nie čierna skrinka) -
ľahko sa dá nahradiť/rozšíriť o regresiu, keď bude k dispozícii viac dát
naprieč projektmi a developermi.
"""
from __future__ import annotations

import statistics
from typing import TYPE_CHECKING, Iterable

from main.models import PriceEstimate, PriceEstimateMethod

if TYPE_CHECKING:
    # Len pre typové nápovedy - pricing.py je zámerne nezávislý od SQLAlchemy,
    # aby sa dal testovať/používať aj bez DB vrstvy (stačí objekt s rovnakými
    # atribútmi ako UnitRecord: price_czk, area_m2, floor, project_name, ...).
    from main.db import UnitRecord

# Vonkajšie priestory (balkón/terasa/záhrada) sa oceňujú zlomkom ceny za m²
# obytnej plochy - na žiadosť používateľa 2026-08 nastavené na polovicu
# (predtým 0.3, teraz 0.5) ceny za m² bytu.
OUTDOOR_AREA_VALUE_FACTOR = 0.5

# Približná úprava ceny za každé poschodie nad najnižším porovnateľným
# bytom (jednoduchá heuristika, nie regresia) - v realite je táto hodnota
# projekt-špecifická (výhľad, hluk z ulice a pod.), preto je konzervatívna.
PRICE_PER_FLOOR_ADJUSTMENT = 0.005  # +0.5 % za poschodie

MIN_COMPARABLES_FOR_MEDIUM_CONFIDENCE = 3
MIN_COMPARABLES_FOR_HIGH_CONFIDENCE = 8


def _effective_area_m2(unit) -> float:
    """Vnútorná plocha + vonkajšia plocha ocenená faktorom
    OUTDOOR_AREA_VALUE_FACTOR. Používa sa ako spoločný menovateľ pri
    odvodzovaní ceny/m² z porovnateľných bytov - inak by cena/m² odvodená
    z CELKOVEJ (zverejnenej) ceny porovnávaného bytu delenej len jeho
    VNÚTORNOU plochou už v sebe niesla hodnotu JEHO balkóna, a keby sa
    táto (nafúknutá) sadzba použila spolu s ĎALŠÍM pripočítaním balkóna
    cieľového bytu, hodnota balkóna by sa počítala dvakrát."""
    outdoor = unit.outdoor_area_m2 or 0
    return unit.area_m2 + outdoor * OUTDOOR_AREA_VALUE_FACTOR


def _floor_to_int(floor: str | None) -> int | None:
    if not floor:
        return None
    digits = "".join(ch for ch in floor if ch.isdigit() or ch == "-")
    try:
        return int(digits)
    except ValueError:
        return None


def estimate_price(unit: UnitRecord, comparables: Iterable[UnitRecord]) -> PriceEstimate:
    notes: list[str] = []

    if unit.price_czk:
        return PriceEstimate(
            unit_number=unit.unit_number,
            project_name=unit.project_name,
            developer=unit.developer,
            estimated_price_czk=unit.price_czk,
            price_per_m2_used=(
                round(unit.price_czk / unit.area_m2, 2) if unit.area_m2 else None
            ),
            method=PriceEstimateMethod.PUBLISHED,
            comparables_count=0,
            confidence="high",
            notes=["Cena je přímo zveřejněná developerem."],
        )

    priced_comparables = [
        c for c in comparables if c.price_czk and c.area_m2 and c.unit_number != unit.unit_number
    ]

    if not priced_comparables or not unit.area_m2:
        return PriceEstimate(
            unit_number=unit.unit_number,
            project_name=unit.project_name,
            developer=unit.developer,
            estimated_price_czk=None,
            price_per_m2_used=None,
            method=PriceEstimateMethod.UNAVAILABLE,
            comparables_count=len(priced_comparables),
            confidence="low",
            notes=[
                "Chybí porovnatelné byty se zveřejněnou cenou ve stejném projektu, "
                "nebo chybí výměra hledaného bytu - odhad není možný."
            ],
        )

    # Cena/m² sa odvodzuje z efektívnej plochy (nie len area_m2) - viď
    # docstring _effective_area_m2, prečo by inak hrozilo dvojité
    # počítanie hodnoty balkóna.
    price_per_m2_values = [c.price_czk / _effective_area_m2(c) for c in priced_comparables]
    base_price_per_m2 = statistics.median(price_per_m2_values)
    notes.append(
        f"Medián ceny za m² z {len(priced_comparables)} porovnatelných bytů "
        f"v projektu '{unit.project_name}': {round(base_price_per_m2):,} Kč/m²".replace(",", " ")
    )

    # úprava za poschodie oproti mediánu poschodí porovnávaných bytov
    unit_floor = _floor_to_int(unit.floor)
    comparable_floors = [f for f in (_floor_to_int(c.floor) for c in priced_comparables) if f is not None]
    if unit_floor is not None and comparable_floors:
        floor_diff = unit_floor - statistics.median(comparable_floors)
        adjustment = 1 + (floor_diff * PRICE_PER_FLOOR_ADJUSTMENT)
        base_price_per_m2 *= adjustment
        if floor_diff:
            notes.append(f"Upraveno o {round((adjustment - 1) * 100, 1)} % za rozdíl v podlaží.")

    estimated_price = base_price_per_m2 * unit.area_m2

    if unit.outdoor_area_m2:
        estimated_price += base_price_per_m2 * unit.outdoor_area_m2 * OUTDOOR_AREA_VALUE_FACTOR
        notes.append(
            f"Připočteno {unit.outdoor_area_m2} m² venkovní plochy "
            f"s faktorem {OUTDOOR_AREA_VALUE_FACTOR}."
        )

    if len(priced_comparables) >= MIN_COMPARABLES_FOR_HIGH_CONFIDENCE:
        confidence = "high"
    elif len(priced_comparables) >= MIN_COMPARABLES_FOR_MEDIUM_CONFIDENCE:
        confidence = "medium"
    else:
        confidence = "low"
        notes.append("Málo porovnatelných bytů (méně než 3) - odhad je jen orientační.")

    return PriceEstimate(
        unit_number=unit.unit_number,
        project_name=unit.project_name,
        developer=unit.developer,
        estimated_price_czk=round(estimated_price),
        price_per_m2_used=round(base_price_per_m2, 2),
        method=PriceEstimateMethod.COMPARABLE_AVG,
        comparables_count=len(priced_comparables),
        confidence=confidence,
        notes=notes,
    )


def estimate_price_by_locality_index(
    unit: UnitRecord, price_per_m2: float, locality_label: str
) -> PriceEstimate:
    """Doplnkový odhad k `estimate_price()` - namiesto porovnania s inými
    bytmi v projekte násobí podlahovú plochu jednotky orientačným cenovým
    indexom danej lokality (`price_index.py`). Nezávisí od toho, či
    developer cenu zverejňuje - má zmysel zobraziť ho popri `estimate_price`
    ako druhý, nezávislý údaj (porovnanie "developer vs. trh").

    Vonkajšie priestory (balkón/terasa/predzahrádka - `outdoor_area_m2`) sa
    pripočítavajú rovnako ako v `estimate_price()`: za `OUTDOOR_AREA_VALUE_FACTOR`
    (30 %) hodnoty vnútornej plochy, aby obe metódy odhadu boli konzistentné -
    predtým táto metóda vonkajšie priestory úplne ignorovala."""
    if not unit.area_m2:
        return PriceEstimate(
            unit_number=unit.unit_number,
            project_name=unit.project_name,
            developer=unit.developer,
            method=PriceEstimateMethod.UNAVAILABLE,
            confidence="low",
            notes=["Chybí podlahová plocha jednotky - odhad podle indexu není možný."],
        )

    estimated_price = price_per_m2 * unit.area_m2
    notes = [
        f"Odhad podle orientačního cenového indexu pro lokalitu '{locality_label}' "
        f"({round(price_per_m2):,} Kč/m² × {unit.area_m2} m²).".replace(",", " "),
    ]

    if unit.outdoor_area_m2:
        estimated_price += price_per_m2 * unit.outdoor_area_m2 * OUTDOOR_AREA_VALUE_FACTOR
        notes.append(
            f"Připočteno {unit.outdoor_area_m2} m² venkovní plochy "
            f"s faktorem {OUTDOOR_AREA_VALUE_FACTOR}."
        )

    notes.append(
        "Hodnoty indexu jsou zatím ilustrativní placeholder (app/data/price_index_praha.json), "
        "nikoli oficiální cenový index - nahraď reálným zdrojem před produkčním použitím."
    )

    return PriceEstimate(
        unit_number=unit.unit_number,
        project_name=unit.project_name,
        developer=unit.developer,
        estimated_price_czk=round(estimated_price),
        price_per_m2_used=price_per_m2,
        method=PriceEstimateMethod.COMPARABLE_MARKET,
        comparables_count=0,
        confidence="low",
        notes=notes,
    )
