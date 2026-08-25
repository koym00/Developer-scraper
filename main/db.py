"""
Databázová vrstva (SQLAlchemy). Ukladáme aktuálny stav každej bytovej
jednotky + históriu cien, aby sme:
  1) nemuseli pri každom dopyte znova scrapovať celý web,
  2) mali dáta pre porovnávací (comparable) odhad ceny,
  3) vedeli sledovať vývoj ceny v čase.
"""
from __future__ import annotations

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

DATABASE_URL = "sqlite:///./byty.db"

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
