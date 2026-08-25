"""
Spoločný dátový model pre bytovú jednotku, ktorý zjednocuje výstup
zo všetkých scraperov (bez ohľadu na to, ako developer dáta prezentuje).

Toto je jadro celej aplikácie - všetky scrapery musia vracať dáta
v tomto tvare, aby fungoval spoločný pricing engine a API.
"""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class Developer(str, Enum):
    """Top 5 developerov z rozšíreného zadania (2026-08) - tí, ktorí majú
    u RB možnosť vkladania 2 plomb. Trigema (pôvodný prototypový developer,
    mimo tohto zoznamu) bola z appky úplne odstránená na žiadosť
    používateľa - žiadny iný developer nad tento zoznam sa nepridáva."""

    EKOSPOL = "ekospol"
    FINEP = "finep"
    SEKYRA = "sekyra"
    SKANSKA = "skanska"
    CENTRAL_GROUP = "central_group"


class UnitStatus(str, Enum):
    AVAILABLE = "volny"
    RESERVED = "rezervovany"
    SOLD = "predany"
    UNKNOWN = "neznamy"


class UnitData(BaseModel):
    """Jedna bytová jednotka tak, ako ju vráti scraper (pred uložením do DB)."""

    developer: Developer
    project_name: str
    project_url: Optional[str] = None

    unit_number: str
    floor: Optional[str] = None
    disposition: Optional[str] = None  # napr. "2+kk"

    area_m2: Optional[float] = None
    outdoor_area_m2: Optional[float] = None  # balkón/terasa/záhrada spolu

    # Plocha KAŽDÉHO vonkajšieho priestoru ZVLÁŠŤ (na rozdiel od
    # outdoor_area_m2, ktorý je ich súčet) - kľúč je názov vlastnosti tak,
    # ako je aj vo `features` (napr. "balkon", "terasa", "predzahradka",
    # "zahradka"), hodnota je plocha v m². Vypĺňa sa len tam, kde zdrojové
    # dáta rozlišujú plochu per-príslušenstvo, nie len spoločný súčet -
    # zatiaľ Ekospol (priamo číselné polia balkon/terasa/predzahradka) a
    # Finep (plocha je pri každom segmente v texte príslušenstva). Ak
    # developer uvádza daný priestor len ako flag bez plochy, v slovníku
    # nebude a FE zobrazí aspoň "Áno" (viď hasFeature).
    outdoor_area_by_type: dict[str, float] = Field(default_factory=dict)

    # Rozpis podlahovej plochy po jednotlivých miestnostiach, tak ako ho
    # uvádza developer (napr. [{"name": "Ložnice", "area_m2": 13.8}, ...]) -
    # v poradí, v akom sa nachádza v zdroji. Doplnené 2026-08 na žiadosť
    # používateľa. Vypĺňa sa len tam, kde je táto informácia spoľahlivo a
    # štruktúrovane dostupná (nie krehkým parsovaním obrázka/SVG):
    # Central Group (`rooms` pole v `/api/apartment/{catalogNumber}`),
    # Ekospol (HTML tabuľka "Místnost" na detaile bytu). Finep/Sekyra tento
    # rozpis nikde nezverejňujú (statický obrázok bez textu) a Skanska ho
    # má len v SVG pôdoryse s nekonzistentnou štruktúrou naprieč projektmi
    # (viď PROJECT_BRIEF.md) - zámerne sa preto neskúša.
    rooms: list[dict] = Field(default_factory=list)

    # Užitná plocha (na rozdiel od `area_m2` = podlahová plocha vrátane
    # priečok/nosných stien) - doplnené 2026-08 na žiadosť používateľa,
    # PRIAMO zverejnená hodnota od developera, nie odhad. Zatiaľ len
    # Central Group (`innerFloorArea` v `/api/apartment/{catalogNumber}`,
    # lazy) a Skanska ("Užitná plocha" v `listitem` na detaile bytu,
    # lazy). Ostatní developeri toto pole nikde nezverejňujú - FE si tam
    # pomáha buď súčtom `rooms` (ak sú k dispozícii), alebo odhadom
    # `area_m2 * 0.95` označeným hviezdičkou (viď `index.html`).
    usable_area_m2: Optional[float] = None

    features: list[str] = Field(default_factory=list)  # napr. ["balkon", "sklep"]
    orientation: Optional[str] = None

    # Okres/mestská časť tak, ako ju uvádza developer (formát sa líši -
    # "Praha 5", "Praha 5 - Smíchov", "Smíchov"...) - vstup pre orientačný
    # cenový index podľa lokality (app/price_index.py). Nie každý developer
    # ju zverejňuje bez dodatočného requestu (Finep/Sekyra zatiaľ None).
    locality: Optional[str] = None

    # Obrázok/pôdorys jednotky, ak ho developer zverejňuje (formát sa líši
    # naprieč developermi - PDF karta bytu, PNG/SVG pôdorys a pod.).
    plan_url: Optional[str] = None

    # Odkaz na stránku TEJTO KONKRÉTNEJ jednotky u developera (na rozdiel
    # od source_url, ktorý je často spoločný pre celý cenník/projekt).
    detail_url: Optional[str] = None

    # Ak developer cenu nezverejňuje ("na dotaz"), price_czk je None
    # a price_note obsahuje pôvodný text.
    price_czk: Optional[int] = None
    price_note: Optional[str] = None
    price_includes_vat: Optional[bool] = None

    # Cena garáže/parkovacieho státia SAMOSTATNE od ceny bytu (developeri ju
    # typicky do price_czk nezahŕňajú - napr. Finep to explicitne píše:
    # "Cena garážového stání není zahrnuta v celkové ceně bytu"). Zatiaľ ju
    # ako skutočné číslo poskytuje len Ekospol - pri ostatných developeroch,
    # ktorí majú parkovanie len ako áno/nie flag vo `features`, zostáva None.
    garage_price_czk: Optional[int] = None

    status: UnitStatus = UnitStatus.UNKNOWN
    move_in_date: Optional[str] = None

    source_url: str
    scraped_at: datetime = Field(default_factory=datetime.utcnow)

    @property
    def price_per_m2(self) -> Optional[float]:
        if self.price_czk and self.area_m2:
            return round(self.price_czk / self.area_m2, 2)
        return None


class PriceEstimateMethod(str, Enum):
    PUBLISHED = "published"  # cena je priamo zverejnená developerom
    COMPARABLE_AVG = "comparable_avg"  # odhad z porovnateľných bytov v tom istom projekte
    COMPARABLE_MARKET = "comparable_market"  # odhad z cenového indexu podľa okresu/lokality (price_index.py)
    UNAVAILABLE = "unavailable"  # nedá sa odhadnúť (chýbajú porovnateľné dáta)


class PriceEstimate(BaseModel):
    unit_number: str
    project_name: str
    developer: Developer

    estimated_price_czk: Optional[int] = None
    price_per_m2_used: Optional[float] = None
    method: PriceEstimateMethod
    comparables_count: int = 0
    confidence: str = "low"  # low / medium / high
    notes: list[str] = Field(default_factory=list)


class UnitQuery(BaseModel):
    """Vstup od používateľa - identifikácia konkrétnej bytovej jednotky."""

    developer: Developer
    project_name: str
    unit_number: str
