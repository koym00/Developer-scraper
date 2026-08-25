"""
Spoločné rozhranie pre všetky per-developer scrapery.

Každý scraper implementuje `fetch_all_units()`, ktorý vráti VŠETKY
dostupné jednotky, aké sa dajú z webu vytiahnuť (typicky pre jeden
alebo viac projektov naraz - podľa toho, ako developer stránku stavia).

Prečo nescrapovať len jeden konkrétny byt na požiadanie?
Pretože skoro všetci developeri publikujú buď kompletný cenník,
alebo zoznam bytov v rámci projektu - je lacnejšie (menej requestov,
menej rizika blokovania) stiahnuť celý projekt/cenník naraz a výsledok
cachovať v DB, než robiť samostatný scrape pre každý dopyt.
"""
from __future__ import annotations

import ssl
from abc import ABC, abstractmethod

import httpx
import truststore

from main.models import Developer, UnitData

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept-Language": "cs-CZ,cs;q=0.9,en;q=0.8",
}


class ScraperError(RuntimeError):
    """Vyhodí sa, keď sa nepodarí stiahnuť alebo naparsovať dáta zo stránky."""


class BaseScraper(ABC):
    developer: Developer
    base_url: str

    def __init__(self, timeout: float = 20.0):
        # verify cez truststore namiesto default certifi bundlu - v prostrediach
        # s TLS-interpretujucim proxy (napr. Zscaler v bankovom nasadeni) je
        # firemna CA v OS trust store, ale nie je v certifi (ten obsahuje len
        # verejne CA) - httpx by inak padal na SSLCertVerificationError.
        self.client = httpx.Client(
            headers=DEFAULT_HEADERS,
            timeout=timeout,
            follow_redirects=True,
            verify=truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT),
        )

    def close(self) -> None:
        self.client.close()

    def __enter__(self) -> "BaseScraper":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def _get(self, url: str) -> httpx.Response:
        resp = self.client.get(url)
        if resp.status_code != 200:
            raise ScraperError(f"GET {url} -> HTTP {resp.status_code}")
        return resp

    @abstractmethod
    def fetch_all_units(self) -> list[UnitData]:
        """Stiahne a vráti všetky nájdené bytové jednotky."""
        raise NotImplementedError

    def fetch_extra_details_for_unit(self, record) -> dict:
        """Voliteľné: lazy dotiahne polia JEDNEJ konkrétnej jednotky, ktoré
        by si pri hromadnom `fetch_all_units()` vyžadovali EXTRA request na
        KAŽDÚ jednotku (napr. rozpis miestností pri Ekospole/Central
        Group, pôdorys pri Central Group) - aby stovky/tisícky bytov
        neúmerne nespomalili hromadný `/refresh`. `record` je `UnitRecord`
        (alebo čokoľvek s rovnakými atribútmi, napr. `detail_url`).

        Default: nepodporované (prázdny slovník). Prepísané len tam, kde
        má to zmysel - volá sa až z `/unit` endpointu, keď si niekto
        konkrétny byt naozaj vyhľadá (výsledok sa uloží do DB, takže sa
        nefetchuje opakovane). Vracia slovník `{názov_poľa: hodnota}` na
        aktualizáciu - len polia, ktoré sa podarilo zistiť."""
        return {}

    def needs_extra_details(self, record) -> bool:
        """Má zmysel volať `fetch_extra_details_for_unit()` pre TENTO
        konkrétny záznam? Default: nie. Scrapery, ktoré prepisujú
        `fetch_extra_details_for_unit`, MUSIA prepísať aj toto -
        presnou kontrolou polí, ktoré ich konkrétna implementácia vie
        naplniť. Bez tejto kontroly by `/unit` skúšal lazy fetch pri
        KAŽDOM dopyte navždy dokola pre developera, ktorý dané pole
        (napr. `rooms`) nikdy neposkytuje - nemalo by sa kedy zastaviť,
        keďže "chýbajúce" pole by ostalo chýbajúce naveky (doplnené
        2026-08, nájdené pri pridávaní `usable_area_m2`/`orientation`
        pre Skanska, ktorá `rooms` nikdy nemá)."""
        return False

    def find_unit(self, project_name: str, unit_number: str) -> UnitData | None:
        """Pomocná default implementácia - stiahne všetko a vyfiltruje.
        Konkrétne scrapery to môžu prepísať efektívnejšie (napr. priamy
        dotaz na projekt namiesto celého cenníka).
        """
        project_norm = _normalize(project_name)
        unit_norm = _normalize(unit_number)
        for unit in self.fetch_all_units():
            if (
                _normalize(unit.project_name) == project_norm
                and _normalize(unit.unit_number) == unit_norm
            ):
                return unit
        return None


def _normalize(value: str) -> str:
    return "".join(ch for ch in value.lower().strip() if ch.isalnum())
