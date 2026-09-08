"""
Databázová vrstva (SQLAlchemy). Ukladáme aktuálny stav každej bytovej
jednotky + históriu cien, aby sme:
  1) nemuseli pri každom dopyte znova scrapovať celý web,
  2) mali dáta pre porovnávací (comparable) odhad ceny,
  3) vedeli sledovať vývoj ceny v čase.
"""
from __future__ import annotations

import os
from datetime import datetime

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    Integer,
    String,
    UniqueConstraint,
    create_engine,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

# CodeNow kontajner nemá zapisovateľný adresár appky (relatívna cesta
# "./byty.db" tam zlyháva) - jediný zapisovateľný priečinok je "/tmp".
# Preto sa cesta dá prepísať cez env premennú (nastaviteľnú v CodeNow UI,
# viď pravidlo 3 z architektúry projektu - "secrets/config nikdy natvrdo
# v kóde"), s fallbackom na pôvodné lokálne správanie, keď premenná
# nie je nastavená. POZOR: "/tmp" je na väčšine kontajnerových platforiem
# efemérne úložisko - dáta (cache aj história cien) sa strácajú pri
# každom reštarte/redeployi appky.
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./byty.db")

engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


class Base(DeclarativeBase):
    pass


class UnitRecord(Base):
    """Aktuálny (najnovší) stav jednej bytovej jednotky."""

    __tablename__ = "units"
    __table_args__ = (
        UniqueConstraint("developer", "project_name", "unit_number", name="uq_unit_identity"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    developer: Mapped[str] = mapped_column(String, index=True)
    project_name: Mapped[str] = mapped_column(String, index=True)
    project_url: Mapped[str | None] = mapped_column(String, nullable=True)

    unit_number: Mapped[str] = mapped_column(String, index=True)
    floor: Mapped[str | None] = mapped_column(String, nullable=True)
    disposition: Mapped[str | None] = mapped_column(String, nullable=True)

    area_m2: Mapped[float | None] = mapped_column(Float, nullable=True)
    outdoor_area_m2: Mapped[float | None] = mapped_column(Float, nullable=True)
    outdoor_area_by_type: Mapped[dict] = mapped_column(JSON, default=dict)
    rooms: Mapped[list] = mapped_column(JSON, default=list)
    # Interný príznak (nie je súčasťou verejného API) - rozlišuje "ešte sa
    # nepodarilo dotiahnuť rozpis miestností" (skús znova) od "skúsilo sa
    # to, zdroj (napr. PDF pôdorysu bez textovej vrstvy) ho ale trvalo
    # neposkytuje" (netreba skúšať znova pri každom ďalšom vyhľadaní).
    # Doplnené 2026-09 pre Skanska (viď skanska.py, needs_extra_details).
    rooms_unavailable: Mapped[bool] = mapped_column(Boolean, default=False)
    # Interný príznak - či bol plan_url už overený/opravený proti skutočnej
    # galérii na detaile bytu (viď skanska.py, fetch_extra_details_for_unit).
    # Predtým sa plan_url pri Skanske len skladal podľa vzoru
    # `{code}.svg` bez overenia - naživo sa zistilo (2026-09, nahlásil
    # používateľ), že tento vzor niekedy ukazuje na fasádu budovy
    # ("pohled jižní") namiesto skutočného pôdorysu, ktorý je vždy PRVÝ
    # obrázok v galérii (rel="specs-gallery") na stránke bytu.
    plan_url_verified: Mapped[bool] = mapped_column(Boolean, default=False)
    usable_area_m2: Mapped[float | None] = mapped_column(Float, nullable=True)
    features: Mapped[list] = mapped_column(JSON, default=list)
    orientation: Mapped[str | None] = mapped_column(String, nullable=True)
    plan_url: Mapped[str | None] = mapped_column(String, nullable=True)
    detail_url: Mapped[str | None] = mapped_column(String, nullable=True)
    locality: Mapped[str | None] = mapped_column(String, nullable=True)

    price_czk: Mapped[int | None] = mapped_column(Integer, nullable=True)
    price_note: Mapped[str | None] = mapped_column(String, nullable=True)
    price_includes_vat: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    garage_price_czk: Mapped[int | None] = mapped_column(Integer, nullable=True)

    status: Mapped[str] = mapped_column(String, default="neznamy")
    move_in_date: Mapped[str | None] = mapped_column(String, nullable=True)

    source_url: Mapped[str] = mapped_column(String)
    scraped_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class PriceHistoryRecord(Base):
    """Záznam ceny v čase - zapisuje sa pri každom scrapovaní, ak sa cena zmenila."""

    __tablename__ = "price_history"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    developer: Mapped[str] = mapped_column(String, index=True)
    project_name: Mapped[str] = mapped_column(String, index=True)
    unit_number: Mapped[str] = mapped_column(String, index=True)
    price_czk: Mapped[int | None] = mapped_column(Integer, nullable=True)
    recorded_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


def init_db() -> None:
    Base.metadata.create_all(bind=engine)


def get_session():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
