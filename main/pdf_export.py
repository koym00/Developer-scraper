"""
Export detailu bytovej jednotky do PDF (CodeNow verzia - bez Playwrightu).

Pôvodná appka generovala PDF cez headless Chromium (Playwright) - PaaS
prostredia ako CodeNow typicky nepodporujú spúšťanie prehliadačových
binárok (chýbajúce systémové závislosti, sandbox obmedzenia), preto táto
verzia používa `reportlab` (čisto Python, žiadny externý proces).

Vizuálne je výsledok jednoduchší než pôvodná HTML/Chromium verzia
(reportlab nemá plný CSS box model - žiadny flexbox), ale obsahovo
rovnocenný: rovnaké fakty, rozpis miestností, oba cenové odhady.

Diakritika: vstavané reportlab fonty (Helvetica a pod.) používajú
WinAnsiEncoding, ktoré NEPODPORUJE viaceré české/slovenské znaky (č, ř, š,
ž, ě, ď, ť, ň, ů) - preto je v `main/fonts/` priložený DejaVu Sans
(slobodný font, licencia dovoľuje redistribúciu - rovnaký font okrem iného
bundluje aj matplotlib), zaregistrovaný nižšie a použitý vo všetkých
štýloch namiesto default Helvetiky.

Fonty sú v repozitári uložené ako Base64 text (`.ttf.b64`), nie ako
binárne `.ttf` súbory - pri kopírovaní do Bitbucketu cez webové UI
("vytvor súbor + vlož text") sa binárny súbor vložiť nedá, Base64 text
áno. Pri štarte appky sa preto `.ttf.b64` dekóduje do dočasného súboru a
až ten sa zaregistruje v reportlab (viď `_register_font_from_b64`).

Pôdorys: vie sa vykresliť len rastrový obrázok (PNG/JPEG/GIF/WEBP) stiahnutý
cez httpx. SVG pôdorys (napr. Skanska) reportlab/Pillow bez ďalšej
knižnice (napr. `svglib`) priamo nevykreslí - v takom prípade sa namiesto
obrázka vypíše len klikateľný odkaz, rovnako ako pri PDF-pôdorysoch v
pôvodnej verzii.
"""
from __future__ import annotations

import base64
import html
import io
import ssl
import tempfile
import unicodedata
from datetime import datetime
from pathlib import Path

import httpx
import truststore
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import Image, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

_FONTS_DIR = Path(__file__).resolve().parent / "fonts"


def _register_font_from_b64(font_name: str, b64_filename: str) -> None:
    """Dekóduje `main/fonts/<b64_filename>` (Base64 text) do dočasného
    `.ttf` súboru a zaregistruje ho v reportlab pod `font_name`. Fonty sa
    v repozitári držia ako text (nie binárka), aby sa dali skopírovať aj
    cez webové UI, ktoré neumožňuje nahratie binárneho súboru (viď
    docstring modulu)."""
    raw_bytes = base64.b64decode((_FONTS_DIR / b64_filename).read_bytes())
    tmp = tempfile.NamedTemporaryFile(suffix=".ttf", delete=False)
    tmp.write(raw_bytes)
    tmp.close()
    pdfmetrics.registerFont(TTFont(font_name, tmp.name))


_register_font_from_b64("DejaVuSans", "DejaVuSans.ttf.b64")
_register_font_from_b64("DejaVuSans-Bold", "DejaVuSans-Bold.ttf.b64")

_TEXT = colors.HexColor("#1c2126")
_MUTED = colors.HexColor("#5b6570")
_BORDER = colors.HexColor("#dde1e6")
_ACCENT = colors.HexColor("#1a5fb4")

_STATUS_LABELS = {
    "volny": "Volný",
    "rezervovany": "Rezervovaný",
    "predany": "Prodaný",
    "neznamy": "Neznámý",
}
_STATUS_COLORS = {
    "volny": ("#e3f4e9", "#1f7a3d"),
    "rezervovany": ("#fdf0d5", "#a15c00"),
    "predany": ("#f7dedc", "#b3261e"),
    "neznamy": ("#eceef1", "#5b6570"),
}
_METHOD_LABELS = {
    "published": "Přímo zveřejněná cena",
    "comparable_avg": "Odhad z porovnatelných bytů v projektu",
    "comparable_market": "Odhad z cenového indexu lokality",
    "unavailable": "Odhad není možný",
}

_IMAGE_EXTENSIONS = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp")


# --- rovnaké čisto-textové pomocné funkcie ako v pôvodnej Playwright verzii
# (framework-nezávislé - vracajú plain/escapnutý text, logika sa nemení) ---

def _normalize_feature(text: str) -> str:
    no_diacritics = "".join(ch for ch in unicodedata.normalize("NFD", text) if not unicodedata.combining(ch))
    return no_diacritics.lower()


def _has_feature(features: list[str], name: str) -> bool:
    target = _normalize_feature(name)
    return any(_normalize_feature(f) == target for f in (features or []))


def _has_any_feature(features: list[str], names: list[str]) -> bool:
    return any(_has_feature(features, name) for name in names)


def _format_area(value: float | None) -> str:
    return f"{value} m²" if value is not None else "—"


def _format_czk(value: int | float | None) -> str | None:
    if value is None:
        return None
    return f"{round(value):,}".replace(",", " ") + " Kč"


def _outdoor_type_display(unit: dict, keys: list[str]) -> str:
    by_type = unit.get("outdoor_area_by_type") or {}
    for key in keys:
        if by_type.get(key) is not None:
            return f"{by_type[key]} m²"
    return "Ano (plocha neuvedena)" if _has_any_feature(unit.get("features"), keys) else "Ne"


def _garage_display(unit: dict) -> str:
    if unit.get("garage_price_czk") is not None:
        return _format_czk(unit["garage_price_czk"])
    return "Ano (cena neuvedena)" if _has_feature(unit.get("features"), "parkovani") else "Ne"


_OUTDOOR_ROOM_KEYWORDS = ("balkon", "terasa", "predzahradka", "zahrada", "zahradka", "lodzie")


def _is_outdoor_room_name(name: str) -> bool:
    normalized = _normalize_feature(name or "")
    return any(kw in normalized for kw in _OUTDOOR_ROOM_KEYWORDS)


def _usable_area_info(unit: dict) -> tuple[str, bool]:
    """Užitná plocha - v poradí priority: priamo od developera
    (`usable_area_m2`), inak súčet plôch VNÚTORNÝCH miestností z rozpisu
    (bez terasy/balkóna a pod.), inak odhad ako podlahová plocha − 5 %
    označený hviezdičkou - rovnaká logika ako `index.html`, `usableAreaInfo()`."""
    usable_area_m2 = unit.get("usable_area_m2")
    if usable_area_m2 is not None:
        return f"{usable_area_m2} m²", False
    rooms = unit.get("rooms") or []
    indoor_rooms = [r for r in rooms if not _is_outdoor_room_name(r.get("name", ""))]
    if indoor_rooms:
        total = round(sum(r.get("area_m2") or 0 for r in indoor_rooms), 1)
        return f"{total} m²", False
    area_m2 = unit.get("area_m2")
    if area_m2 is not None:
        return f"{round(area_m2 * 0.95, 1)} m² *", True
    return "—", False


def _e(value) -> str:
    """XML-escape (&, <, > a pod.) - reportlab `Paragraph` parsuje malú
    podmnožinu XML značiek (napr. `<a href>`, `<b>`), takže text z
    externých developerských webov treba escapovať rovnako opatrne ako pri
    pôvodnej HTML/Playwright verzii."""
    return html.escape(str(value)) if value is not None else ""


def _fact_rows(unit: dict) -> list[tuple[str, str]]:
    outdoor_total = unit.get("outdoor_area_m2")
    price_note = unit.get("price_note")
    published = unit.get("published_price_czk")
    return [
        ("Projekt", _e(unit.get("project_name"))),
        ("Podlaží", _e(unit.get("floor")) or "—"),
        ("Dispozice", _e(unit.get("disposition")) or "—"),
        ("Podlahová plocha", _format_area(unit.get("area_m2"))),
        ("Užitná plocha", _e(_usable_area_info(unit)[0])),
        ("Venkovní prostory celkem", _format_area(outdoor_total) if outdoor_total is not None else "—"),
        ("Balkon", _e(_outdoor_type_display(unit, ["balkon"]))),
        ("Terasa", _e(_outdoor_type_display(unit, ["terasa"]))),
        ("Předzahrádka", _e(_outdoor_type_display(unit, ["predzahradka"]))),
        ("Zahrada", _e(_outdoor_type_display(unit, ["zahrada", "zahradka"]))),
        ("Lodžie", _e(_outdoor_type_display(unit, ["lodzie"]))),
        ("Příslušenství", _e(", ".join(unit.get("features") or [])) or "—"),
        ("Garáž/parkovací stání", _e(_garage_display(unit))),
        ("Úložný prostor (sklep/komora)", "Ano" if _has_any_feature(unit.get("features"), ["sklep", "komora"]) else "Ne"),
        ("Lokalita", _e(unit.get("locality")) or "—"),
        ("Nastěhování", _e(unit.get("move_in_date")) or "—"),
        ("Zveřejněná cena", _e(_format_czk(published) if published is not None else (price_note or "na dotaz"))),
    ]


# --- štýly (všetky cez DejaVu Sans kvôli diakritike, viď docstring modulu) ---

_STYLE_LABEL = ParagraphStyle("label", fontName="DejaVuSans", fontSize=9, textColor=_MUTED, leading=12)
_STYLE_VALUE = ParagraphStyle("value", fontName="DejaVuSans-Bold", fontSize=9.5, textColor=_TEXT, leading=12)
_STYLE_H1 = ParagraphStyle("h1", fontName="DejaVuSans-Bold", fontSize=17, textColor=_TEXT, leading=20)
_STYLE_SUB = ParagraphStyle("sub", fontName="DejaVuSans", fontSize=10, textColor=_MUTED, leading=13)
_STYLE_BADGE = ParagraphStyle("badge", fontName="DejaVuSans-Bold", fontSize=10, leading=16, alignment=1)
_STYLE_META = ParagraphStyle("meta", fontName="DejaVuSans", fontSize=8, textColor=_MUTED, leading=11)
_STYLE_LINK = ParagraphStyle("link", fontName="DejaVuSans", fontSize=9.5, textColor=_ACCENT, leading=13)
_STYLE_H2 = ParagraphStyle("h2", fontName="DejaVuSans-Bold", fontSize=9.5, textColor=_MUTED, leading=13)
_STYLE_HINT = ParagraphStyle("hint", fontName="DejaVuSans", fontSize=8.5, textColor=_MUTED, leading=12)
_STYLE_ROOM = ParagraphStyle("room", fontName="DejaVuSans", fontSize=9.5, textColor=_TEXT, leading=13)
_STYLE_AMOUNT = ParagraphStyle("amount", fontName="DejaVuSans-Bold", fontSize=17, textColor=_TEXT, leading=20)
_STYLE_NOTE = ParagraphStyle("note", fontName="DejaVuSans", fontSize=8.5, textColor=_MUTED, leading=12)


def _fetch_image_bytes(url: str) -> bytes | None:
    # verify cez truststore - rovnaký dôvod ako v `main/scrapers/base.py`
    # (TLS-interpretujúci proxy v bankovom prostredí, napr. Zscaler).
    try:
        with httpx.Client(
            verify=truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT), timeout=10.0, follow_redirects=True
        ) as client:
            resp = client.get(url)
            if resp.status_code == 200 and resp.content:
                return resp.content
    except Exception:
        return None
    return None


def _plan_flowables(plan_url: str | None) -> list:
    if not plan_url:
        return [Paragraph("Půdorys/obrázek není k dispozici.", _STYLE_HINT)]
    is_image = plan_url.lower().split("?")[0].endswith(_IMAGE_EXTENSIONS)
    if not is_image:
        # napr. SVG (Skanska) alebo PDF pôdorys - nevieme vykresliť bez
        # ďalšej knižnice, ponúkneme aspoň klikateľný odkaz.
        return [Paragraph(
            f'Půdorys je k dispozici: <a href="{_e(plan_url)}" color="#1a5fb4">{_e(plan_url)}</a>', _STYLE_HINT
        )]
    data = _fetch_image_bytes(plan_url)
    if not data:
        return [Paragraph(
            f'Obrázek se nepodařilo stáhnout. Odkaz: <a href="{_e(plan_url)}" color="#1a5fb4">{_e(plan_url)}</a>',
            _STYLE_HINT,
        )]
    try:
        reader = ImageReader(io.BytesIO(data))
        iw, ih = reader.getSize()
        max_w, max_h = 80 * mm, 80 * mm
        scale = min(max_w / iw, max_h / ih, 1.0)
        return [Image(io.BytesIO(data), width=iw * scale, height=ih * scale)]
    except Exception:
        return [Paragraph(
            f'Obrázek se nepodařilo zobrazit. Odkaz: <a href="{_e(plan_url)}" color="#1a5fb4">{_e(plan_url)}</a>',
            _STYLE_HINT,
        )]


def _facts_table(unit: dict) -> Table:
    rows = [[Paragraph(label, _STYLE_LABEL), Paragraph(str(value), _STYLE_VALUE)] for label, value in _fact_rows(unit)]
    table = Table(rows, colWidths=[45 * mm, None])
    table.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("LINEBELOW", (0, 0), (-1, -2), 0.4, _BORDER),
    ]))
    return table


def _rooms_flowables(rooms: list[dict]) -> list:
    if not rooms:
        return []
    flow = [Spacer(1, 4 * mm), Paragraph("ROZLOŽENÍ MÍSTNOSTÍ", _STYLE_H2), Spacer(1, 2 * mm)]
    rows = [
        [Paragraph(_e(r.get("name")), _STYLE_ROOM), Paragraph(f'{_e(r.get("area_m2"))} m²', _STYLE_ROOM)]
        for r in rooms
    ]
    table = Table(rows, colWidths=[None, 30 * mm])
    table.setStyle(TableStyle([
        ("ALIGN", (1, 0), (1, -1), "RIGHT"),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("LINEBELOW", (0, 0), (-1, -2), 0.3, _BORDER),
    ]))
    flow.append(table)
    return flow


def _estimate_flowables(title: str, estimate: dict | None) -> list:
    if not estimate:
        return []
    flow = [Spacer(1, 4 * mm), Paragraph(title.upper(), _STYLE_H2), Spacer(1, 2 * mm)]
    if estimate.get("method") == "unavailable":
        note = (estimate.get("notes") or ["Odhad není k dispozici."])[0]
        flow.append(Paragraph(_e(note), _STYLE_HINT))
        return flow
    amount = _format_czk(estimate.get("estimated_price_czk")) or "—"
    per_m2 = estimate.get("price_per_m2_used")
    per_m2_text = f"{round(per_m2):,}".replace(",", " ") + " Kč/m²" if per_m2 is not None else ""
    method_label = _METHOD_LABELS.get(estimate.get("method"), estimate.get("method"))
    hint = f"{_e(method_label)}{' · ' + _e(per_m2_text) if per_m2_text else ''} · důvěra: {_e(estimate.get('confidence'))}"
    flow.append(Paragraph(_e(amount), _STYLE_AMOUNT))
    flow.append(Paragraph(hint, _STYLE_HINT))
    for note in estimate.get("notes") or []:
        flow.append(Paragraph(f"• {_e(note)}", _STYLE_NOTE))
    return flow


def _build_story(unit: dict, price_estimate: dict, index_price_estimate: dict | None) -> list:
    status = unit.get("status")
    status_label = _STATUS_LABELS.get(status, status)
    badge_bg, badge_fg = _STATUS_COLORS.get(status, _STATUS_COLORS["neznamy"])

    header_left = [
        Paragraph(f'Byt {_e(unit.get("unit_number"))} — {_e(unit.get("project_name"))}', _STYLE_H1),
        Paragraph(_e(unit.get("developer")), _STYLE_SUB),
    ]
    badge_style = ParagraphStyle(
        "badge_dyn", parent=_STYLE_BADGE, textColor=colors.HexColor(badge_fg), backColor=colors.HexColor(badge_bg)
    )
    header_table = Table([[header_left, Paragraph(_e(status_label), badge_style)]], colWidths=[None, 35 * mm])
    header_table.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("ALIGN", (1, 0), (1, 0), "CENTER"),
        ("LINEBELOW", (0, 0), (-1, 0), 1.2, _ACCENT),
        ("BOTTOMPADDING", (0, 0), (-1, 0), 8),
    ]))

    story: list = [header_table, Spacer(1, 3 * mm)]

    generated_at = datetime.now().strftime("%d.%m.%Y %H:%M")
    story.append(Paragraph(f"Vygenerováno {_e(generated_at)}", _STYLE_META))

    detail_url = unit.get("detail_url")
    if detail_url:
        story.append(Paragraph(
            f'<a href="{_e(detail_url)}" color="#1a5fb4">Zobrazit na stránce developera ↗</a>', _STYLE_LINK
        ))
    story.append(Spacer(1, 4 * mm))

    # facts + pôdorys vedľa seba (dvojstĺpcový layout cez vonkajšiu tabuľku
    # - reportlab nemá flexbox, ale vnorené flowables v bunkách tabuľky
    # dajú rovnaký vizuálny efekt).
    facts_col: list = [_facts_table(unit)]
    if _usable_area_info(unit)[1]:
        facts_col.append(Spacer(1, 2 * mm))
        facts_col.append(Paragraph(
            "* Užitná plocha je odhad (podlahová plocha − 5 %), protože rozpis místností není k dispozici.",
            _STYLE_HINT,
        ))
    layout_table = Table([[facts_col, _plan_flowables(unit.get("plan_url"))]], colWidths=[100 * mm, None])
    layout_table.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP")]))
    story.append(layout_table)

    story.extend(_rooms_flowables(unit.get("rooms") or []))
    story.extend(_estimate_flowables("Odhad ceny (porovnání v rámci projektu)", price_estimate))
    story.extend(_estimate_flowables("Odhad podle cenového indexu lokality", index_price_estimate))

    story.append(Spacer(1, 6 * mm))
    story.append(Paragraph(
        f'Zdroj: {_e(unit.get("source_url"))} · Data stažena {_e(str(unit.get("scraped_at")))}', _STYLE_META
    ))
    return story


def render_unit_pdf(unit: dict, price_estimate: dict, index_price_estimate: dict | None) -> bytes:
    """Vyrenderuje detail bytovej jednotky do PDF a vráti surové bajty.
    `unit`/`price_estimate`/`index_price_estimate` majú rovnaký tvar ako
    JSON odpoveď `GET /unit` (viď `main/routes.py`, `_load_unit_with_price`)."""
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        topMargin=18 * mm,
        bottomMargin=18 * mm,
        leftMargin=16 * mm,
        rightMargin=16 * mm,
        title=f'Byt {unit.get("unit_number")} - {unit.get("project_name")}',
    )
    doc.build(_build_story(unit, price_estimate, index_price_estimate))
    return buffer.getvalue()
