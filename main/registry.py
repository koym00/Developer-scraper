"""Centrálny register - mapuje Developer enum na konkrétnu triedu scrapera."""
from __future__ import annotations

from main.models import Developer
from main.scrapers.base import BaseScraper
from main.scrapers.central_group import CentralGroupScraper
from main.scrapers.ekospol import EkospolScraper
from main.scrapers.finep import FinepScraper
from main.scrapers.sekyra import SekyraScraper
from main.scrapers.skanska import SkanskaScraper

SCRAPER_REGISTRY: dict[Developer, type[BaseScraper]] = {
    Developer.EKOSPOL: EkospolScraper,
    Developer.FINEP: FinepScraper,
    Developer.SEKYRA: SekyraScraper,
    Developer.SKANSKA: SkanskaScraper,
    Developer.CENTRAL_GROUP: CentralGroupScraper,
}


def get_scraper(developer: Developer) -> BaseScraper:
    scraper_cls = SCRAPER_REGISTRY[developer]
    return scraper_cls()
