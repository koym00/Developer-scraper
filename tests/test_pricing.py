"""
Offline test pre pricing.py - odhad ceny na základe porovnateľných bytov.
Spustenie: python3 tests/test_pricing.py

Pricing.py je zámerne nezávislý od SQLAlchemy (pozri TYPE_CHECKING import
v app/pricing.py), takže tu použijeme jednoduchý objekt s rovnakými
atribútmi ako UnitRecord namiesto skutočnej DB.
"""
from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from main.models import PriceEstimateMethod
from main.pricing import estimate_price, estimate_price_by_locality_index


@dataclass
class FakeUnit:
    developer: str
    project_name: str
    unit_number: str
    price_czk: int | None
    area_m2: float | None
    floor: str | None = None
    outdoor_area_m2: float | None = None
    features: list = field(default_factory=list)


def check(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)
    print(f"OK: {message}")


def test_published_price_used_directly():
    unit = FakeUnit("ekospol", "Ekocity Hostivar A", "161", price_czk=10_000_000, area_m2=100.0)
    estimate = estimate_price(unit, comparables=[])
    check(estimate.method == PriceEstimateMethod.PUBLISHED, "zverejnená cena -> metóda PUBLISHED")
    check(estimate.estimated_price_czk == 10_000_000, "zverejnená cena sa vráti bezo zmeny")


def test_comparable_estimate_basic():
    target = FakeUnit("ekospol", "Ekocity Hostivar A", "161", price_czk=None, area_m2=100.0, floor="5")
    comparables = [
        FakeUnit("ekospol", "Ekocity Hostivar A", "1", price_czk=8_000_000, area_m2=80.0, floor="5"),
        FakeUnit("ekospol", "Ekocity Hostivar A", "2", price_czk=9_000_000, area_m2=90.0, floor="5"),
        FakeUnit("ekospol", "Ekocity Hostivar A", "3", price_czk=10_000_000, area_m2=100.0, floor="5"),
    ]
    estimate = estimate_price(target, comparables)
    check(estimate.method == PriceEstimateMethod.COMPARABLE_AVG, "chýbajúca cena -> COMPARABLE_AVG")
    # medián ceny/m2 z [100000, 100000, 100000] = 100000 -> 100000*100 = 10_000_000 (rovnaké poschodie)
    check(estimate.estimated_price_czk == 10_000_000, f"odhad pri rovnakom poschodí = 10M (dostal {estimate.estimated_price_czk})")
    check(estimate.comparables_count == 3, "použili sa 3 porovnateľné byty")


def test_comparable_estimate_floor_adjustment_and_outdoor_area():
    # porovnávané byty na poschodí 1, cieľový byt na poschodí 11 -> cena hore
    target = FakeUnit(
        "ekospol", "Ekocity Hostivar A", "161", price_czk=None, area_m2=100.0, floor="11", outdoor_area_m2=10.0
    )
    comparables = [
        FakeUnit("ekospol", "Ekocity Hostivar A", "1", price_czk=10_000_000, area_m2=100.0, floor="1"),
    ]
    estimate = estimate_price(target, comparables)
    # base = 100_000 Kc/m2, floor_diff = 10 -> adjustment = 1 + 10*0.005 = 1.05 -> 105_000/m2
    # 105_000*100 (byt) + 105_000*10*0.5 (vonkajsia plocha) = 10_500_000 + 525_000 = 11_025_000
    check(
        estimate.estimated_price_czk == 11_025_000,
        f"odhad s úpravou za poschodie a vonkajšiu plochu (dostal {estimate.estimated_price_czk})",
    )


def test_unavailable_when_no_comparables():
    target = FakeUnit("ekospol", "Ekocity Hostivar A", "161", price_czk=None, area_m2=100.0)
    estimate = estimate_price(target, comparables=[])
    check(estimate.method == PriceEstimateMethod.UNAVAILABLE, "bez porovnateľných bytov -> UNAVAILABLE")
    check(estimate.estimated_price_czk is None, "bez porovnateľných bytov -> žiadna cena")


def test_comparable_estimate_does_not_double_count_comparables_own_balcony():
    # porovnávaný byt MÁ balkón zahrnutý vo svojej zverejnenej cene
    # (10_500_000 = 100_000 Kč/m² efektívnej plochy [100 + 10*0.5] bytu) -
    # cieľový byt balkón NEMÁ, takže jeho odhad sa nesmie "nakaziť"
    # hodnotou balkóna porovnávaného bytu (regresný test na opravu
    # dvojitého počítania - predtým by tu vyšlo 10_500_000 namiesto 10M).
    target = FakeUnit("ekospol", "Ekocity Hostivar A", "161", price_czk=None, area_m2=100.0, floor="5", outdoor_area_m2=None)
    comparables = [
        FakeUnit("ekospol", "Ekocity Hostivar A", "1", price_czk=10_500_000, area_m2=100.0, floor="5", outdoor_area_m2=10.0),
    ]
    estimate = estimate_price(target, comparables)
    check(
        estimate.estimated_price_czk == 10_000_000,
        f"odhad bez vlastného balkóna sa nesmie nafúknuť balkónom porovnávaného bytu (dostal {estimate.estimated_price_czk})",
    )


def test_locality_index_includes_outdoor_area():
    # rovnaký vzorec ako pri comparable_avg: vnútorná plocha za plnú cenu/m²,
    # vonkajšia plocha za OUTDOOR_AREA_VALUE_FACTOR (0.5) z ceny/m²
    unit = FakeUnit("ekospol", "Ekocity Hostivar A", "161", price_czk=None, area_m2=100.0, outdoor_area_m2=10.0)
    estimate = estimate_price_by_locality_index(unit, price_per_m2=100_000.0, locality_label="Smíchov")
    # 100_000*100 (byt) + 100_000*10*0.5 (balkón/terasa) = 10_000_000 + 500_000 = 10_500_000
    check(
        estimate.estimated_price_czk == 10_500_000,
        f"index odhad musí zarátať aj vonkajšiu plochu (dostal {estimate.estimated_price_czk})",
    )
    check(
        any("venkovní plochy" in note for note in estimate.notes),
        "poznámky musia vysvetliť pripočítanie vonkajšej plochy",
    )


def test_locality_index_without_outdoor_area():
    unit = FakeUnit("ekospol", "Ekocity Hostivar A", "161", price_czk=None, area_m2=100.0, outdoor_area_m2=None)
    estimate = estimate_price_by_locality_index(unit, price_per_m2=100_000.0, locality_label="Smíchov")
    check(
        estimate.estimated_price_czk == 10_000_000,
        f"bez vonkajšej plochy sa počíta len z area_m2 (dostal {estimate.estimated_price_czk})",
    )


if __name__ == "__main__":
    test_published_price_used_directly()
    test_comparable_estimate_basic()
    test_comparable_estimate_floor_adjustment_and_outdoor_area()
    test_unavailable_when_no_comparables()
    test_comparable_estimate_does_not_double_count_comparables_own_balcony()
    test_locality_index_includes_outdoor_area()
    test_locality_index_without_outdoor_area()
    print("\nVšetky testy prešli.")
