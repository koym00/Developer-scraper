"""
Flask blueprint - vstupný bod backend logiky (CodeNow architektúra).

Endpointy (rovnaké ako pôvodná FastAPI verzia, viď PROJECT_BRIEF.md):
  GET  /                              - FE (main/templates/index.html)
  GET  /developers                    - zoznam developerov pre FE select
  POST /refresh/<developer>           - spustí scraper, uloží do DB
  GET  /unit                          - detail jednotky + cenové odhady
  GET  /unit/pdf                      - to isté ako /unit, ako PDF na stiahnutie
  GET  /units                         - zoznam cachovaných jednotiek

Na rozdiel od FastAPI nemá Flask automatickú validáciu podľa typov
(Depends/Query) - každý endpoint si ručne vytiahne/skontroluje query
parametre aj DB session (viď `_parse_developer`, `_require_param`,
`_get_session`).
"""
from __future__ import annotations

import logging
from urllib.parse import quote

from flask import Blueprint, Response, jsonify, render_template, request
from sqlalchemy.orm import Session

from main.db import SessionLocal, init_db
from main.models import Developer, PriceEstimate, UnitData
from main.pdf_export import render_unit_pdf
from main.price_index import get_price_per_m2
from main.pricing import estimate_price, estimate_price_by_locality_index
from main.registry import get_scraper
from main.repository import get_comparables, get_unit, list_units, upsert_units

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

bp_main = Blueprint("main", __name__, template_folder="templates", static_folder="static")

# Flask nemá FastAPI-štýl `@app.on_event("startup")` - inicializácia DB sa
# preto spustí hneď pri importe tohto modulu (run.py ho importuje pred
# `app.run()`/gunicorn pred prvým requestom).
init_db()


class ApiError(Exception):
    """Jednotná chyba API - ekvivalent FastAPI `HTTPException`, aby FE
    (`fetch` + `body.detail`) fungoval bezo zmeny oproti pôvodnej appke."""

    def __init__(self, status_code: int, detail: str):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


@bp_main.errorhandler(ApiError)
def _handle_api_error(err: ApiError):
    return jsonify({"detail": err.detail}), err.status_code


def _get_session() -> Session:
    return SessionLocal()


def _parse_developer(value: str | None) -> Developer:
    if not value:
        raise ApiError(422, "Chybí povinný parametr 'developer'.")
    try:
        return Developer(value)
    except ValueError:
        allowed = ", ".join(d.value for d in Developer)
        raise ApiError(422, f"Neplatný developer '{value}'. Povolené hodnoty: {allowed}.")


def _require_param(name: str) -> str:
    value = (request.args.get(name) or "").strip()
    if not value:
        raise ApiError(422, f"Chybí povinný parametr '{name}'.")
    return value


def _parse_bool(value: str | None, default: bool) -> bool:
    if value is None:
        return default
    return value.strip().lower() in ("1", "true", "yes", "ano")


@bp_main.route("/")
def index():
    return render_template("index.html")


@bp_main.route("/developers")
def list_developers():
    return jsonify([d.value for d in Developer])


def _refresh_developer(developer: Developer, session: Session) -> dict:
    scraper = get_scraper(developer)
    try:
        units: list[UnitData] = scraper.fetch_all_units()
    except Exception as exc:  # necháme endpoint vrátiť čitateľnú chybu namiesto pádu procesu
        raise ApiError(502, f"Scraping selhal: {exc}") from exc
    finally:
        scraper.close()

    count = upsert_units(session, units)
    return {"developer": developer.value, "units_upserted": count}


@bp_main.route("/refresh/<developer>", methods=["POST"])
def refresh_developer(developer: str):
    dev = _parse_developer(developer)
    session = _get_session()
    try:
        return jsonify(_refresh_developer(dev, session))
    finally:
        session.close()


def _load_unit_with_price(
    developer: Developer,
    project_name: str,
    unit_number: str,
    auto_refresh: bool,
    session: Session,
) -> dict:
    """Spoločná logika pre `GET /unit` aj `GET /unit/pdf` - nájde byt (s
    prípadným auto-refreshom a lazy dotiahnutím extra detailov) a spočíta
    oba cenové odhady. Vyhadzuje `ApiError`, ak sa byt nenašiel."""
    record = get_unit(session, developer, project_name, unit_number)

    if record is None and auto_refresh:
        _refresh_developer(developer, session)  # naplní cache
        record = get_unit(session, developer, project_name, unit_number)

    if record is None:
        raise ApiError(
            404,
            "Byt nebyl nalezen. Zkontroluj přesný název projektu a číslo bytu, "
            "případně zkus POST /refresh/{developer} a ověř dostupné projekty přes GET /units.",
        )

    # Polia, ktoré by pri hromadnom /refresh vyžadovali extra request na
    # KAŽDÚ jednotku (napr. rozpis miestností, pri Central Group aj
    # plan_url), sa dotiahnu až tu - lenivo, len pre TENTO konkrétny byt -
    # aby /refresh zostal rýchly aj pri stovkách/tisícoch bytov (viď
    # BaseScraper.fetch_extra_details_for_unit/needs_extra_details).
    scraper = get_scraper(developer)
    try:
        if scraper.needs_extra_details(record):
            try:
                extra_details = scraper.fetch_extra_details_for_unit(record)
                if extra_details:
                    for field, value in extra_details.items():
                        setattr(record, field, value)
                    session.commit()
            except Exception as exc:
                logger.warning(
                    "Nepodarilo sa lazy dotiahnuť extra detaily pre %s/%s/%s (%s)",
                    developer, project_name, unit_number, exc,
                )
    finally:
        scraper.close()

    comparables = get_comparables(session, developer, project_name, unit_number)
    estimate: PriceEstimate = estimate_price(record, comparables)

    index_estimate: PriceEstimate | None = None
    index_match = get_price_per_m2(record.locality)
    if index_match:
        price_per_m2, locality_label = index_match
        index_estimate = estimate_price_by_locality_index(record, price_per_m2, locality_label)

    return {
        "unit": {
            "developer": record.developer,
            "project_name": record.project_name,
            "unit_number": record.unit_number,
            "floor": record.floor,
            "disposition": record.disposition,
            "area_m2": record.area_m2,
            "outdoor_area_m2": record.outdoor_area_m2,
            "outdoor_area_by_type": record.outdoor_area_by_type,
            "rooms": record.rooms,
            "usable_area_m2": record.usable_area_m2,
            "features": record.features,
            "plan_url": record.plan_url,
            "detail_url": record.detail_url,
            "locality": record.locality,
            "status": record.status,
            "move_in_date": record.move_in_date,
            "published_price_czk": record.price_czk,
            "price_note": record.price_note,
            "garage_price_czk": record.garage_price_czk,
            "source_url": record.source_url,
            # Flask/stdlib json nesérializuje datetime rovnako predvídateľne
            # ako FastAPI (ISO 8601) - explicitný .isoformat() zaručí rovnaký
            # tvar, na aký je FE zvyknuté.
            "scraped_at": record.scraped_at.isoformat() if record.scraped_at else None,
        },
        "price_estimate": estimate.model_dump(),
        "index_price_estimate": index_estimate.model_dump() if index_estimate else None,
    }


def _unit_query_params() -> tuple[Developer, str, str, bool]:
    developer = _parse_developer(request.args.get("developer"))
    project_name = _require_param("project_name")
    unit_number = _require_param("unit_number")
    auto_refresh = _parse_bool(request.args.get("auto_refresh"), True)
    return developer, project_name, unit_number, auto_refresh


@bp_main.route("/unit")
def get_unit_with_price():
    developer, project_name, unit_number, auto_refresh = _unit_query_params()
    session = _get_session()
    try:
        return jsonify(_load_unit_with_price(developer, project_name, unit_number, auto_refresh, session))
    finally:
        session.close()


@bp_main.route("/unit/pdf")
def get_unit_pdf():
    developer, project_name, unit_number, auto_refresh = _unit_query_params()
    session = _get_session()
    try:
        data = _load_unit_with_price(developer, project_name, unit_number, auto_refresh, session)
    finally:
        session.close()

    pdf_bytes = render_unit_pdf(data["unit"], data["price_estimate"], data["index_price_estimate"])
    filename = f"byt_{data['unit']['unit_number']}_{data['unit']['project_name']}.pdf".replace(" ", "_")
    # Diakritika v názve projektu nie je platná v obyčajnom `filename=`
    # (RFC 6266 nepovoľuje percent-encoding v quoted-string) - preto ASCII
    # fallback pre staršie/menej tolerantné klienty + `filename*=UTF-8''...`
    # s reálnou diakritikou pre moderné prehliadače.
    ascii_fallback = filename.encode("ascii", "ignore").decode("ascii") or "byt.pdf"
    headers = {"Content-Disposition": f'attachment; filename="{ascii_fallback}"; filename*=UTF-8\'\'{quote(filename)}'}
    return Response(pdf_bytes, mimetype="application/pdf", headers=headers)


@bp_main.route("/units")
def list_all_units():
    developer_param = request.args.get("developer")
    developer = _parse_developer(developer_param) if developer_param else None
    project_name = request.args.get("project_name")
    session = _get_session()
    try:
        records = list_units(session, developer, project_name)
        return jsonify([
            {
                "developer": r.developer,
                "project_name": r.project_name,
                "unit_number": r.unit_number,
                "disposition": r.disposition,
                "area_m2": r.area_m2,
                "price_czk": r.price_czk,
                "status": r.status,
                "locality": r.locality,
            }
            for r in records
        ])
    finally:
        session.close()
