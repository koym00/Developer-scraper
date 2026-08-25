"""Ukladanie výsledkov scraperov do DB a čítanie späť (cache + história cien)."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from main.db import PriceHistoryRecord, UnitRecord
from main.models import Developer, UnitData


def upsert_units(session: Session, units: list[UnitData]) -> int:
    """Uloží/aktualizuje jednotky. Vráti počet spracovaných záznamov.
    Ak sa cena zmenila oproti poslednému stavu, zapíše sa aj do price_history.
    """
    count = 0
    for unit in units:
        existing = session.scalar(
            select(UnitRecord).where(
                UnitRecord.developer == unit.developer.value,
                UnitRecord.project_name == unit.project_name,
                UnitRecord.unit_number == unit.unit_number,
            )
        )

        price_changed = existing is None or existing.price_czk != unit.price_czk

        if existing is None:
            existing = UnitRecord(
                developer=unit.developer.value,
                project_name=unit.project_name,
                unit_number=unit.unit_number,
            )
            session.add(existing)

        existing.project_url = unit.project_url
        existing.floor = unit.floor
        existing.disposition = unit.disposition
        existing.area_m2 = unit.area_m2
        existing.outdoor_area_m2 = unit.outdoor_area_m2
        existing.outdoor_area_by_type = unit.outdoor_area_by_type
        existing.rooms = unit.rooms
        existing.usable_area_m2 = unit.usable_area_m2
        existing.features = unit.features
        existing.orientation = unit.orientation
        existing.plan_url = unit.plan_url
        existing.detail_url = unit.detail_url
        existing.locality = unit.locality
        existing.price_czk = unit.price_czk
        existing.price_note = unit.price_note
        existing.price_includes_vat = unit.price_includes_vat
        existing.garage_price_czk = unit.garage_price_czk
        existing.status = unit.status.value
        existing.move_in_date = unit.move_in_date
        existing.source_url = unit.source_url
        existing.scraped_at = unit.scraped_at

        if price_changed:
            session.add(
                PriceHistoryRecord(
                    developer=unit.developer.value,
                    project_name=unit.project_name,
                    unit_number=unit.unit_number,
                    price_czk=unit.price_czk,
                    recorded_at=datetime.utcnow(),
                )
            )

        count += 1

    session.commit()
    return count


def get_unit(
    session: Session, developer: Developer, project_name: str, unit_number: str
) -> UnitRecord | None:
    return session.scalar(
        select(UnitRecord).where(
            UnitRecord.developer == developer.value,
            UnitRecord.project_name == project_name,
            UnitRecord.unit_number == unit_number,
        )
    )


def get_comparables(
    session: Session, developer: Developer, project_name: str, exclude_unit_number: str
) -> list[UnitRecord]:
    return list(
        session.scalars(
            select(UnitRecord).where(
                UnitRecord.developer == developer.value,
                UnitRecord.project_name == project_name,
                UnitRecord.unit_number != exclude_unit_number,
            )
        )
    )


def list_units(
    session: Session, developer: Developer | None = None, project_name: str | None = None
) -> list[UnitRecord]:
    stmt = select(UnitRecord)
    if developer:
        stmt = stmt.where(UnitRecord.developer == developer.value)
    if project_name:
        stmt = stmt.where(UnitRecord.project_name == project_name)
    return list(session.scalars(stmt))
