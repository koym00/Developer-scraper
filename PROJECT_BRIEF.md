# Byty Scraper — kontext projektu (prečítaj pred pokračovaním v práci)

Tento dokument je určený AI asistentovi (napr. Claude v VS Code), ktorý
má pokračovať v tomto projekte bez predchádzajúcej histórie konverzácie.
Vysvetľuje, čo má aplikácia robiť, prečo je postavená tak, ako je, čo je
hotové, čo nie, a aké sú najbližšie priority.

## 1. Zadanie / cieľ aplikácie

Úloha: vytvoriť aplikáciu, ktorá zisťuje informácie o konkrétnych
bytových jednotkách z webov developerských spoločností. Vstup je
identifikácia konkrétneho bytu (developer + projekt + číslo bytu),
výstup sú jeho parametre (dispozícia, výmera, poschodie, stav, dátum
nastěhovania...) a **cena** — buď priamo zverejnená developerom, alebo
ak nie je zverejnená ("na dotaz"), **odhadnutá** na základe porovnateľných
bytov v tom istom projekte.

Prvá fáza: sprevádzkovať to pre **5 najväčších českých rezidenčných
developerov**. Pôvodný prieskum (na základe objemu predaných/ponúkaných
bytov v zdrojoch E15/Patria, ARTN, Hypoindex) obsahoval aj iného
developera, ktorý sa neskôr nahradil presnejším kritériom nižšie.

**Zmena (2026-08, spresnené zadanie od používateľa):** aktuálny Top 5
zoznam je iný kritérium - developeri, ktorí majú u RB (banka) možnosť
vkladania 2 "plomb" (bližší kontext v internom Word dokumente
používateľa, appke nedostupnom). Aktuálny zoznam:

1. **Ekospol**
2. **Finep**
3. **Sekyra Group** (sekyragroup.cz)
4. **Skanska Reality** (residential.skanska.cz)
5. **Central Group**

**Presne týchto 5 - žiadny iný developer.** Pôvodný prototypový
developer z fázy prieskumu bol z appky na výslovnú žiadosť používateľa
(2026-08) **úplne odstránený** - nielen z FE výberu (to platilo už
skôr), ale kompletne z kódu (`Developer` enum, scraper, registry,
testy, dokumentácia). Ak sa v budúcnosti zdá, že niečo chýba alebo
nesedí kvôli tomu, že šiesty developer kedysi existoval - nepridávaj ho
naspäť bez toho, aby si sa opýtal používateľa.

## 2. Prečo je architektúra taká, aká je

Rozhodnutia padli na základe explicitnej voľby používateľa (viď nižšie)
a prieskumu štruktúry jednotlivých webov (spravený cez WebFetch, keďže
vývojové prostredie, v ktorom projekt vznikol, **nemalo priamy prístup
na internet** mimo pár nástrojov — pozri sekciu 5, "Známe obmedzenia").

Používateľ si vybral:
- **Python** ako stack (FastAPI + SQLAlchemy + BeautifulSoup/httpx + Playwright ako fallback pre JS weby).
- **Poradie budovania od najjednoduchších webov**: Ekospol → Finep → Skanska → Central Group.
- **Rovno API + databáza** (nie len jednorazový skript) — teda architektúra
  s cachovaním v DB a FastAPI endpointmi hneď od začiatku.

Zistenia o jednotlivých weboch (z prieskumu, pred písaním kódu):

| Developer | Technológia webu | Dôsledok pre scraper |
|---|---|---|
| Ekospol | Prevažne statické HTML, ceny v tabuľkách na podstránkach jednotlivých projektov | Treba najprv nájsť zoznam projektov, potom parsovať každý zvlášť |
| Finep | Statické stránky, cenník na `/cs/<projekt>/cenik`, plus JS vyhľadávač na `/cs/vyhledavani` | Podobne dvojkrokové, ale menej isté, či `/cenik` má vždy tabuľku |
| Skanska Reality | SPA — zoznam bytov (`/byty`) sa renderuje cez JavaScript, bez JS prázdna stránka | Potrebný headless browser (Playwright) alebo nájdenie interného API |
| Central Group | Celá stránka je "webclient" SPA — bez JS nefunguje vôbec nič | Najnáročnejšie — treba nájsť interné REST/GraphQL API cez DevTools, inak Playwright |

Preto namiesto scrapovania podľa presných CSS tried (krehké, rozbije sa
pri redesignu) sa statické scrapery spoliehajú na **mapovanie stĺpcov
tabuľky podľa textu hlavičky** (`app/scrapers/generic_table.py` +
`app/scrapers/parsing_utils.py`) — odolnejšie, ale treba overiť, že
`HEADER_ALIASES` v každom module sedí na skutočné texty hlavičiek na
danom webe.

## 3. Architektúra a súbory

```
app/
  models.py       - spoločný dátový model (Pydantic): UnitData, PriceEstimate,
                    Developer/UnitStatus/PriceEstimateMethod enumy
  db.py           - SQLAlchemy modely: UnitRecord (aktuálny stav bytu),
                    PriceHistoryRecord (história cien), SQLite (byty.db)
  repository.py   - upsert_units/get_unit/get_comparables/list_units -
                    prepojenie medzi scrapermi a DB
  pricing.py      - výpočet/odhad ceny; ZÁMERNE nezávislý od SQLAlchemy
                    (import UnitRecord len pod `TYPE_CHECKING`), takže sa
                    dá testovať a použiť aj bez DB vrstvy
  registry.py     - SCRAPER_REGISTRY: mapovanie Developer enum -> trieda scrapera
  main.py         - FastAPI: POST /refresh/{developer}, GET /unit, GET /units
  scrapers/
    base.py             - BaseScraper (abstraktná trieda), httpx klient, ScraperError
    parsing_utils.py    - parse_float/parse_price_czk/parse_status/parse_features,
                          normalize_header/strip_diacritics_lower_alnum
                          (robustné voči diakritike, čárkam vs. bodkám, "Kč" a pod.)
    generic_table.py    - find_price_table/build_column_map/extract_units_from_table
                          - zdieľaná logika pre weby s HTML <table>
    playwright_base.py  - PlaywrightTableScraper - headless browser fallback pre JS weby
    ekospol.py            - HOTOVÝ scraper (dvojkrokový: zoznam projektov -> tabuľka)
    finep.py              - HOTOVÝ scraper (dvojkrokový: projekty -> /cenik -> tabuľka)
    skanska.py            - KOSTRA (Playwright), chýba wait_selector a overenie API
    central_group.py      - KOSTRA, zámerne len vyhadzuje ScraperError s návodom
tests/
  test_parsing_and_table.py  - offline testy (syntetické HTML, žiadna sieť) - PREŠLI
  test_pricing.py            - offline testy pricing engine (fake objekty) - PREŠLI
README.md          - používateľská dokumentácia (setup, spustenie, právne poznámky)
PROJECT_BRIEF.md   - tento súbor
```

### Dátový tok

1. `POST /refresh/{developer}` zavolá príslušný scraper (`registry.py`),
   ten stiahne a naparsuje dáta do zoznamu `UnitData` (Pydantic).
2. `repository.upsert_units()` ich uloží/aktualizuje v SQLite (`UnitRecord`),
   a ak sa zmenila cena, zapíše záznam do `PriceHistoryRecord`.
3. `GET /unit?developer=...&project_name=...&unit_number=...` najprv
   skúsi nájsť byt v DB; ak tam nie je a `auto_refresh=true` (default),
   spustí refresh pre daného developera a skúsi znova.
4. Keď je byt nájdený, `pricing.estimate_price()` spočíta/vráti cenu:
   - ak `price_czk` je vyplnené -> vráti sa priamo (`method: published`),
   - inak sa zoberú `get_comparables()` (ostatné byty v tom istom projekte
     so zverejnenou cenou), spočíta sa medián ceny/m², upraví sa o
     poschodie (+0.5 % za poschodie oproti mediánu porovnávaných) a
     pripočíta sa hodnota vonkajších priestorov (balkón/terasa/záhrada)
     ako 50 % z ceny/m² (`OUTDOOR_AREA_VALUE_FACTOR`, pôvodne 30 %,
     zmenené na žiadosť používateľa 2026-08) (`method: comparable_avg`,
     s `confidence` a `notes` vysvetľujúcimi výpočet).

## 4. Stav implementácie — čo je hotové vs. čo treba doriešiť

**Hotové a otestované (offline, bez potreby siete):**
- Dátový model, DB vrstva, repository, pricing engine, FastAPI kostra.
- Parsovacia logika (`parsing_utils.py`, `generic_table.py`) — overená
  syntetickými testami vrátane edge-casov ("na dotaz" cena, diakritika v
  slove "volný"/"rezervovaný", skratky príslušenstva T/B/L/S/Z).

**Hotové a overené proti živým stránkam (2026-08):**
- **`generic_table.py` (zdieľaná logika pre tabuľkové weby)** — pri
  prvom nasadení na živý web sa opravili dva bugy, ktoré sú relevantné
  dodnes (aktuálne ho používa Sekyra): (a) `HEADER_ALIASES` vyžadovali
  presnú zhodu, ale skutočné hlavičky majú extra slová ("Podlahová plocha
  bytu" vs. alias "plocha") — zmenené na "obsahuje" zhodu s víťazstvom
  najdlhšieho kľúča; (b) bunka s číslom bytu môže obsahovať skrytú
  duplicitnú "mobilnú kartu" (Bootstrap `d-none`/`d-block` triedy) so
  všetkými poľami ako text — BeautifulSoup vidí aj skrytý text, takže sa
  všetko zlepilo dokopy. Pridaná `_cell_text()` helper funkcia, ktorá
  preferuje `.d-lg-table-cell` (desktop) variant.
- **Ekospol** — funguje, vracia ~920 bytov (`app/scrapers/ekospol.py`
  prepísaný). Kľúčové zistenie: `<table>` na `/cenik` stránke je len
  prázdna kostra vypĺňaná JavaScriptom — skutočné dáta sú vložené priamo v
  `<script>` ako `window.flats = [...]` (čistý JSON s poľami pagetitle,
  dispozice, podlazi, plocha, balkon/terasa/predzahradka, orientace, cena,
  stav, sklep, garazove-stani). Scraper teraz parsuje tento JSON regexom +
  `json.loads()` namiesto (nefunkčného) parsovania `<table>`.
  **Bug nájdený a opravený 2026-08 (cez FE + reálny web vedľa seba):**
  `discover_project_urls()` nájde aj mŕtve/zlúčené odkazy (napr.
  `ekorezidence-strasnice`), ktoré web presmeruje (302) na `/cenik` INÉHO
  projektu (`ekocity-hostivar-c`) - bez kontroly sa tak dáta Hostivaru C
  uložili duplicitne aj pod menom "Ekorezidence Strašnice" (192
  fantómových bytov navyše, 1111→920 po oprave). Oprava: po stiahnutí
  `/cenik` sa porovná slug z pôvodnej URL so `str(resp.url)` po
  presmerovaní - ak sa nezhodujú, riadok sa preskočí s warningom.
  **Poučenie:** tento typ bugu (tichá duplicita cez presmerovanie) sa
  ťažko odhalí len z počtu/tvaru dát - všimlo sa to až porovnaním FE
  datalistu (9 projektov) so skutočnou stránkou developera (7 v
  marketingovom prehľade) používateľom, nie automatickým testom.
- **Finep** — funguje, vracia 102 bytov (`app/scrapers/finep.py`
  prepísaný). `/cenik` stránka nepoužíva `<table>` vôbec ani JSON blok —
  byty sú priamo v HTML ako "karty" (`div.tile[data-item-id]`),
  stránkované cez `?page=N` query parameter (`?page=2`, `?page=3`, ...).
  Scraper číta `.pagination` odkazy, zistí posledné číslo stránky a
  postupne stiahne všetky. Dve poučenia z ladenia:
  - `PROJECT_LINK_SELECTOR` pôvodne hľadal `href^='/cs/byty-'`
    (relatívne odkazy), ale skutočné odkazy na stránke sú absolútne
    (`https://www.finep.cz/cs/byty-...`) — treba `href*='/cs/byty-'`.
  - Text vlastností bytu ("balkon (5,9 m²), garáž ...") sa nesmie
    rozdeľovať obyčajným `split(",")`, lebo čeština používa čiarku ako
    desatinný oddeľovač ("5,9") — treba `re.split(r",(?!\d)", text)`
    (nerozdeliť čiarku, po ktorej hneď nasleduje číslica).
- **Sekyra Group** — funguje, vracia 30 bytov (`app/scrapers/sekyra.py`,
  nový). Najjednoduchší z piatich: `/units/` je jeden centrálny prehľad
  VŠETKÝCH aktuálne ponúkaných bytov naprieč projektmi, statická HTML
  `<table>` s hotovými dátovými riadkami (žiadne stránkovanie - `?page=2`
  vráti identický obsah), rovnaký vzor ako `generic_table.py` vyžaduje.
  Dve drobnosti:
  (a) `www.sekyragroup.cz` má nesediaci TLS certifikát (hostname
  mismatch) - treba `sekyragroup.cz` bez `www.`; (b) stĺpec "Dostupnosť"
  mal hodnotu "Předrezervováno", ktorá chýbala v zdieľanom `_STATUS_MAP`
  (`parsing_utils.py`) - doplnené (mapuje sa na RESERVED). Táto oprava
  (`generic_table.py`) pri tejto príležitosti odhalila aj všeobecnú chybu
  vo `extract_units_from_table`: prázdna bunka sa predtým ukladala ako
  `""` namiesto `None` (napr. prázdna "Dispozice") - opravené na
  `_cell_text(cell) or None`, s doplnenou ochranou aby prázdny
  `unit_number` nespôsobil pád Pydantic validácie (`if not
  values.get("unit_number")` namiesto `if "unit_number" not in values`).
- **Skanska Reality** — funguje, vracia 360 bytov (`app/scrapers/
  skanska.py` prepísaný, **už nededí z `PlaywrightTableScraper`** —
  Playwright sa nepoužíva vôbec). `/byty` je SPA s prázdnym HTML, ale JS
  widget si dáta ťahá z verejného JSON API bez potreby autentifikácie:
  `GET /api/v1/filters/apartments_page_cs/snapshot`. Endpoint bol nájdený
  **bez prehliadača** - stiahnutím hlavných JS bundlov stránky (`<script
  src>` z HTML) a regexovým hľadaním `/api/`, `fetch(`, `apiBaseUrl` v
  ich obsahu; `filterSetId` (`apartments_page_cs`) bol v inline JS priamo
  v HTML. Odpoveď obsahuje `data.apartments` (číselné polia ako
  `projects`/`localities`/`amenities` sú cudzie kľúče) a
  `data.reference_tables` so slovníkmi id → čitateľný text pre tieto
  polia. Pole `state` bytu má hodnoty `"empty"`/`"registered"`, nie
  "available"/"reserved" - ich reálny význam ("Volný" / "V jednání") sa
  zistil až z JS bundlu (tooltip texty priradené k CSS triede odznaku).
- **Central Group** — funguje, vracia 537 bytov (`app/scrapers/
  central_group.py` prepísaný, žiadny Playwright). Web je "webclient" SPA
  (Vue) s úplne prázdnym HTML bez pripojeného JS. Interné REST API sa
  našlo rovnakým postupom ako pri Skanske - stiahnutím `<script src>` JS
  bundlov (`/wms3/js/app.<hash>.js`) a regexom na `/api/`. Tri potrebné
  endpointy:
  - `GET /api/system/time-version` → vráti holé číslo (`timeId`) - CMS
    "časovú verziu" obsahu, ktorú treba poslať pri každom ďalšom volaní.
  - `GET /api/location` → zoznam projektov (`id`, `name`, `city`, ...).
  - `GET /api/apartment/search` → byty pre danú lokalitu; vyžaduje
    `langId`, `timeId`, `sort`, `sortDirections`, `limit`, `offset`.
  **Kľúčový a najťažšie odhaliteľný detail:** volanie `/api/apartment/
  search` BEZ `locationIds` filtra (t.j. "všetky lokality naraz", presne
  to, čo by som skúsil ako prvé) spoľahlivo padá na serveri (500, prázdne
  telo, niekedy až po dlhom timeoute namiesto rýchlej chyby) - funguje to
  LEN keď sa pošle `locationIds` pre jednu konkrétnu lokalitu naraz.
  Scraper preto musí iterovať `/api/location` a volať `search` pre každú
  zvlášť (rovnaký vzor "zoznam projektov → dáta per projekt" ako pri
  Ekospole/Finepe, len cez JSON API namiesto HTML). Druhé poučenie:
  server pri zlom/chýbajúcom parametri (`sort=id` namiesto správneho
  `sort=TotalPrice`) vráti ASP.NET validation-error JSON s presným
  dôvodom (400) - to bolo kľúčové na uhádnutie správnych enum hodnôt bez
  prístupu k zdrojovému kódu servera.

**Obrázok/pôdorys jednotky (`UnitData.plan_url`, doplnené 2026-08):**
zdroj a spôsob získania sa líši per developer - **u 2 z 5 sa dá
skonštruovať bez extra requestu** z dát, ktoré scraper aj tak už sťahuje
(Finep, Skanska - viď nižšie). `generic_table.py` má aj špeciálnu
hodnotu aliasu `"plan_url"` - pri nej sa namiesto textu bunky vezme
`href` prvého `<a>` v bunke (viď `_resolve_alias`/`extract_units_from_table`) -
pripravené pre budúci web s tabuľkovým cenníkom, ktorý má PDF/obrázok
priamo v stĺpci.
- **Ekospol** — `/assets/ekospol/<interny-slug>/pudorys/<cislo-bytu>.png`.
  `<interny-slug>` NIE JE odvoditeľný z URL slugu projektu (napr.
  `ekocity-hostivar-a` → `eko_hostivar_a`). **PÔVODNE** sa vyťahoval z
  `<img>` v `.project-pic-col` na `/cenik` stránke (bez extra requestu) -
  **toto bolo nespoľahlivé a opravené (2026-08, nahlásil používateľ):**
  pri projekte "Ekocity Hostivar B" tento marketingový obrázok chybne
  odkazoval na priečinok "Hostivar A" (chyba/zdieľaný asset na strane
  Ekospolu, nie na strane scrapera), takže celý projekt mal nefunkčné
  `plan_url` odkazy s cudzím slugom. Overené: slug sa NEDÁ spoľahlivo
  získať ani z `/cenik`, ani z hlavnej stránky projektu - jediný
  potvrdený zdroj je **detail konkrétnej jednotky** (`.../detail/<číslo>`).
  Oprava: `EkospolScraper._discover_asset_slug()` stiahne 1 detail na
  projekt (nie za každý byt - stále len 8 extra requestov na celý beh,
  rovnaký rád ako existujúcich 8 `/cenik` requestov) a slug vytiahne z
  odkazu na pôdorys tam, kde je zaručene správny. Po oprave: 24/24
  (100 %) namiesto predošlých nespoľahlivých hodnôt vo vzorke naprieč
  všetkými 8 projektmi. **Stále platí:** `plan_url` funguje spoľahlivo
  len pre voľné byty - pri predaných sa súbor s pôdorysom z webu zjavne
  odstráni (samostatné, nesúvisiace obmedzenie).
- **Finep** — `https://www.finep.cz/files/images/item/plan/1/<data-item-id>.png`,
  kde `<data-item-id>` je atribút už prítomný na `div.tile` každej karty.
- **Skanska** — `https://residential.skanska.cz/files/<code>.svg`, kde
  `<code>` je pole z API odpovede (`apartments[].code`) - **musí sa
  dať na malé písmená**, súbor je `bm10811.svg`, nie `BM10811.svg`
  (API vracia `code` veľkými písmenami).

  **Preskúmané a ZAMIETNUTÉ (2026-08): plocha terasy/predzáhradky z
  obsahu SVG pôdorysu.** Používateľ nahlásil konkrétny príklad
  (`residential.skanska.cz/.../bb20106`), kde FE ukazoval len "Áno
  (plocha neuvedená)" pre terasu/predzáhradku, hoci na stránke sú
  vidieť rozmery. Overené: SVG pôdorys MÁ tieto plochy vpísané ako
  `<text>` popisky miestností (napr. "Terasa" / "15,0 m²",
  "Předzahrádka" / "24,3 m²") - dalo by sa parsovať bez Playwrightu,
  len stiahnutím `plan_url`, ktorý už scraper má. **Problém:** na
  vzorke 7 SVG súborov naprieč 4 projektmi sa našli minimálne 4 RÔZNE
  štruktúry (rôzne generátory pôdorysov naprieč rokmi/projektmi):
  (a) text rozdelený na fragmenty kvôli diakritike/superscriptu `²`
  (dá sa poskladať), (b) čistý text bez rozdelenia (dá sa parsovať),
  (c) podobne ako (b) ale s drobnými artefaktmi (duplicitný text,
  cudzie znaky), (d) SVG úplne bez `<text>` elementov (čisto grafický
  obrázok), (e) SVG, ktoré NIE JE pôdorys, ale pohľad na fasádu
  budovy, (f) text rozbitý na jednotlivé písmená (extrémne
  fragmentovaný, vyžadoval by úplne inú logiku). Reálne pokrytie na
  tejto vzorke bolo len ~4/7 (~57 %), a to len pri formátoch (a)-(c).
  **Používateľ sa rozhodol toto NEIMPLEMENTOVAŤ** vzhľadom na
  nekonzistentnú štruktúru a neúplné pokrytie (navyše 356 extra
  requestov pri každom refreshi bez ohľadu na úspešnosť). Ak sa k
  tomu niekto vráti: prototyp parsera (state machine nad `<text>`/
  `<tspan>` blokmi, rozpoznáva formáty (a) aj (b)/(c)) je zdokumentovaný
  v histórii tejto konverzácie/scratchpade, netreba začínať od nuly -
  ale očakávaj, že úplné pokrytie nie je dosiahnuteľné bez podpory
  ďalších formátov.
- **Sekyra** — vyžaduje extra request: `/units/` prehľad odkaz na
  obrázok neobsahuje, treba navštíviť detail `/units/<id>` (odkaz je
  v bunke "Číslo") a vziať `src` z `<img alt="Plán jednotky">`. Pri
  ~30 bytoch je to zanedbateľné (na rozdiel od Ekospolu s 1111 bytmi,
  kde by to bolo neúmerne veľa requestov - preto sa tam obrázok skladá
  bez návštevy detailu).
- **Central Group** — `plan_url`, `detail_url` aj správny `unit_number`
  sú HOTOVÉ (doplnené 2026-08, nahlásil používateľ na konkrétnom
  príklade `central-group.cz/byt-detail/189-08-338`):
  - **`detail_url`** sa dá poskladať priamo z `catalogNumber` (ktorý už
    máme z `/api/apartment/search`): `/byt-detail/{catalogNumber}`, resp.
    `/byt-detail-premium.aspx?idByt={catalogNumber}` ak `isPremium=true`
    (presný vzor z JS bundlu). **Predtým sa mylne predpokladalo, že
    Central Group nemá žiadne per-byt URL pole** - `catalogNumber` bol
    už celý čas v dátach, len sa nepoužil na stavbu URL.
  - **`unit_number`** sa zmenil z `catalogNumber` (interný kód
    "189-08-338") na **`"{housingBlockName} {number}"`** (napr.
    "H 338") - presne v tvare, akým developer byt označuje na vlastnej
    stránke (`${housingBlockName} ${number} / ${layoutLabel}` v JS
    bundli). Oba vstupné polia (`housingBlockName`, `number`) sú tiež
    už v `/api/apartment/search` odpovedi, žiadny extra request netreba.
  - **`plan_url` bol nakoniec nájdený vďaka používateľovi**, ktorý poslal
    skutočný request z DevTools (Network tab, pripojený prehliadač -
    toto prostredie prehliadač nemá, žiadna statická analýza JS bundlu
    ani skúšanie API naslepo to nedokázalo nahradiť). Skutočný
    endpoint:

        GET /api/resource/image/24885/ground-plan
            ?timeId=<timeId>&langId=1&boIdMapping[13]=<catalogNumber>

    `24885` je **globálna konštanta** CMS "resource" bloku (rovnaká pre
    všetky projekty/byty - overené naživo), nie per-projekt/blok ID, ako
    sa pôvodne predpokladalo pri predchádzajúcich neúspešných pokusoch s
    `/api/resource/image/{id}/{typ}`. `boIdMapping[13]` (13 = enum
    "Apartment" v JS) stačí samotné - `catalogNumber` už máme z
    `/api/apartment/search`, netreba lokalitu/blok/poschodie navyše.
    Odpoveď je zoznam variantov obrázka v rôznych veľkostiach, berie sa
    najväčší (`width × height`), finálna URL `/Uloziste/<path>`.
    Implementované ako `CentralGroupScraper._fetch_plan_url()` - **1
    extra request na jednotku** (pri 537 bytoch citeľne viac než u
    iných developerov, ale zvládnuteľné). Živo overené: **537/537
    (100 %)** bytov má `plan_url`, náhodná vzorka 20/20 reachable
    (HTTP 200). Jednotlivé zlyhané requesty (napr. dočasný výpadok
    servera) sa logujú ako warning a nezhodia celý beh - daná jednotka
    ostane len bez `plan_url`.

**Nedokončené / treba spraviť** - scrapery pre aktuálny Top 5 (Ekospol,
Finep, Sekyra, Skanska, Central Group) už nechýbajú. Zvyšok je rozdelený
na (A) nové časti z rozšíreného zadania (sekcia 8) a (B) staršie
vylepšenia naprieč appkou:

**(A) Z rozšíreného zadania (2026-08):**
1. ~~**Obrázok/pôdorys jednotky**~~ — HOTOVO pre všetkých 5 (`UnitData.
   plan_url`, pozri sekciu 4C nižšie).
2. ~~**Cenový index podľa okresu/mesta**~~ — HOTOVO ako funkčný skelet
   (`app/price_index.py` + `app/data/price_index_praha.json`,
   `pricing.estimate_price_by_locality_index()`, zapojené do `/unit`
   ako samostatné pole `index_price_estimate`). Pozri sekciu 4D nižšie -
   **hodnoty v JSON súbore sú zatiaľ len placeholder**, zámerne (užívateľ
   potvrdil, že reálny zdroj doplní neskôr - dôležité je, aby zvyšok
   appky/FE fungoval end-to-end už teraz).
3. ~~**Export do PDF**~~ — HOTOVO (2026-08). `GET /unit/pdf` (rovnaké
   query parametre ako `/unit`) vráti PDF ako `attachment`. Implementácia
   cez Playwright (`app/pdf_export.py`) - nie reportlab/weasyprint (krehké
   natívne závislosti na Windows) - postaví sa samostatná HTML stránka v
   rovnakom vizuálnom jazyku ako `app/static/index.html` (farby, badge na
   stav, typografia), headless Chromium ju vykreslí (vrátane stiahnutia
   obrázka pôdorysu cez sieť) a `page.pdf()` z toho spraví PDF. Obsahuje
   všetky fakty o byte, pôdorys, rozpis miestností (ak je k dispozícii) a
   oba cenové odhady s poznámkami. FE má tlačidlo "Exportovat do PDF"
   (`#pdf-export-link`) vedľa nadpisu výsledku, aktívne po úspešnom
   vyhľadaní. Content-Disposition rieši diakritiku v názve súboru
   správne (ASCII fallback + `filename*=UTF-8''...`, keďže obyčajný
   `filename=` nepovoľuje percent-encoding podľa RFC 6266). Overené
   naživo na Central Group (GIF pôdorys) aj Ekospol (PNG pôdorys, iný
   stav bytu "Prodaný") - naživo aj cez skutočný klik na tlačidlo vo FE
   (Playwright `expect_download`), žiadne console chyby, ~4 s na
   vygenerovanie jedného PDF (spustenie headless Chromia + stiahnutie
   obrázka).
4. ~~**Frontend**~~ — HOTOVO, `app/static/index.html` servírované z
   FastAPI (`/`). Pozri sekciu 4E nižšie.

**(B) Staršie vylepšenia:**
5. **Fuzzy vyhľadávanie projektu** — API očakáva presný `project_name`
   tak, ako je uložený v DB (rôzny naprieč developermi - Ekospol/Skanska/
   Central Group/Sekyra používajú "marketingový" názov projektu, Finep
   názov konkrétnej budovy/fázy). FE to čiastočne obchádza `<datalist>`
   nápovedou z cache (pozri sekciu 4E), ale to nenahrádza fuzzy matching
   (napr. `rapidfuzz`) pre používateľa bez dát v cache.
6. **Plánovaný refresh** — momentálne sa scraping spúšťa len on-demand
   (`auto_refresh` pri `/unit`, ručne `/refresh/{developer}`, alebo
   tlačidlo vo FE). Chýba scheduler (cron/APScheduler).
7. **Rate limiting / zdvorilé scrapovanie** — `base.py` zatiaľ nemá
   žiadne oneskorenia medzi requestmi (Central Group robí 537+ requestov
   za sebou len na pôdorysy, ďalšie na lokality/apartmány). Pred
   nasadením do produkcie treba pridať throttling a skontrolovať
   `robots.txt`/ToS každého webu (pozri README.md, sekcia "Právne a
   etické poznámky"). **Zistené naživo (2026-08):** `central-group.cz`
   je citlivý na rýchly sled requestov bez pauzy - 30 GET requestov na
   `/byt-detail/<id>` v rýchlom slede bez odstupu vrátilo **HTTP 500 pre
   všetky** (nie 429 "Too Many Requests", ale rovno 500 - vyzerá to ako
   pád servera/WAF, nie zámerný rate-limit response). S odstupom ~0,6 s
   medzi requestami fungovalo 15/15 bez problémov. Appka samotná do
   tohto scenára pri bežnom používaní nespadne (vždy len 1 request
   naraz na `/unit` dopyt), ale akákoľvek budúca hromadná verifikácia
   odkazov (napr. HEAD-check všetkých `detail_url`/`plan_url` naraz) by
   pre Central Group MUSELA mať throttling, inak nahlási falošné
   zlyhania.
8. ~~Zvážiť, či `playwright` v `requirements.txt` ešte držať~~ - **už
   sa aktívne používa** (2026-08): žiadny scraper ho stále nepoužíva na
   scraping (všade sa našlo priame JSON API alebo statické HTML), ale
   `app/pdf_export.py` ho používa na renderovanie exportu do PDF
   (headless Chromium, `page.pdf()`) - viď sekciu 7, bod 3.
   `playwright_base.py` (fallback pre budúceho JS-ťažkého developera)
   zostáva nepoužitý, to platí ďalej.
9. **`upsert_units()` nikdy nemaže záznamy, ktoré zmiznú zo zdroja.**
   Zistené 2026-08 pri opravovaní "duch" bytu u Skanska (nižšie) -
   scraper prestal daný byt vracať, ale v DB ostal navždy, kým sa
   ručne nezmazal. **Používateľ vedome potvrdil, že toto je OK** -
   historické dáta držať netreba mazať, dôležité je len to, aby sa
   ZMENY (predaj bytu, zmena ceny, nové/doplnené pole) prejavili pri
   ďalšom refreshi. `/unit` sa preto **zámerne NEROBÍ automatický TTL-
   based refresh** pri každom dopyte na už cachovaný byt (zvažované,
   používateľ to zamietol - "nech sa to updatne až vtedy keď kliknem
   obnoviť dáta") - aktualizácia dát je viazaná výhradne na explicitné
   `POST /refresh/{developer}` (ručne cez API, alebo tlačidlo "Obnoviť
   dáta developera" vo FE). Toto je teda vedomé rozhodnutie, nie
   nedorobená vec.

**Skanska - "duch" záznamy v API (opravené 2026-08, nahlásil používateľ
na konkrétnom odkaze `residential.skanska.cz/unknown/byty-2-plus-kk-praha/te00058`):**
naživo sa našiel byt, ktorého `projects` referencia v API odpovedi
neexistuje v `reference_tables` - dôsledok: appka mu priradila fallback
`project_name="Skanska Reality"`, a **na strane Skanska** to znamená aj
rozbitú vlastnú stránku (URL s "unknown" namiesto názvu projektu,
prázdny obsah). Bolo to na vzorke 1/355 bytov - ojedinelé, nie systémové.
`SkanskaScraper._is_ghost_record()` teraz takéto záznamy (fallback názov
projektu SÚČASNE s chýbajúcou cenou AJ plochou - všetky tri naraz, aby
sa neodfiltrovali bežné byty s jednou legitímne chýbajúcou hodnotou)
vynechá z výsledku `fetch_all_units()` a zaloguje warning. Existujúci
záznam v cache (z behu spred opravy) bolo treba zmazať ručne - viď bod 9
vyššie prečo automaticky nezmizol.

**Obvod/lokalita (`UnitData.locality`, doplnené 2026-08) — vstup pre
cenový index:** vypĺňajú ju Ekospol (`lokalita` priamo vo `window.flats`),
Central Group (`locationCity` + `locationCityPart` spojené), Skanska
(`reference_tables.localities` lookup).

**Finep a Sekyra pôvodne nemali (0 %) - doplnené 2026-08 (druhá vlna).**
Ani jeden zo scrapovaných zdrojov (karty/`/cenik` u Finepu, `/units/`
tabuľka u Sekyry) lokalitu neuvádza - je len na inej stránke, ktorú
scraper predtým nenavštevoval:
- **Finep** — lokalita ("Praha 5") je tag na **vlastnej stránke KAŽDÉHO
  projektu** (`a[href*='developerske-projekty-praha-']`, napr.
  `finep.cz/cs/byty-britska-ctvrt`) - `FinepScraper._discover_locality()`
  stiahne túto stránku 1x na projekt (nie na byt). Živé pokrytie po
  oprave: **83/95 (87 %)** - zvyšných 12 (2 projekty "Rezidence U Šárky")
  tento tag na svojej stránke jednoducho nemajú (overené, nie bug).
- **Sekyra** — lokalita ("Praha 8, Rohanský ostrov") je text "Lokalita:
  ..." pri KAŽDOM projekte na **jednej spoločnej** stránke
  `/pages/byty-prodej-praha` - `SekyraScraper.
  _discover_project_localities()` ju stiahne **1x CELKOVO** (najlacnejšia
  z opráv - žiadny extra request na projekt/byt), spáruje nadpis
  projektu s textom lokality (`find_previous` najbližšieho nadpisu) a
  priradí jednotkám podľa `project_name` ako substring v nadpise (táto
  stránka používa marketingové názvy typu "Rohan City – Vision Karlín",
  `/units/` tabuľka len "Vision Karlín"). Živé pokrytie po oprave:
  **29/30 (97 %)** - projekt "Nekázanka 17" na tejto stránke nemá
  štruktúrované pole "Lokalita:", len opisný text (overené, nie bug).

**4D. Cenový index podľa lokality - implementačné detaily:**
`price_index.py` číta `data/price_index_praha.json` (mapa "lokalita" →
Kč/m², jeden riadok `_POZNAMKA` vysvetľujúci, že ide o placeholder).
Keďže developeri uvádzajú lokalitu v rôznych tvaroch ("Praha 5", "Praha
5 - Smíchov", len "Smíchov"...), `get_price_per_m2()` skúša viacero
odvodených kľúčov (celý text, časť za pomlčkou, vzor "Praha N") - naživo
otestované s pokrytím 1111/1111 (Ekospol), 359/359 (Skanska), 543/543
(Central Group) nájdených zhôd. `pricing.
estimate_price_by_locality_index()` vracia `PriceEstimate` s
`method=comparable_market` a poznámkou upozorňujúcou na placeholder
dáta - `main.py` ho pridáva do odpovede `/unit` ako `index_price_estimate`
(alebo `null`, ak lokalita/index chýba), nezávisle od `price_estimate`.
**Doplnené 2026-08 (na žiadosť používateľa):** predtým počítala len z
`area_m2` a úplne ignorovala vonkajšie priestory (balkón/terasa/
predzahrádka) - teraz ich pripočítava rovnako ako `estimate_price()`,
za `OUTDOOR_AREA_VALUE_FACTOR` (pôvodne 30 %, od 2026-08 zmenené na
50 % - viď nižšie) hodnoty vnútornej plochy, aby boli obe metódy
odhadu konzistentné.

**`OUTDOOR_AREA_VALUE_FACTOR` zvýšený z 0.3 na 0.5 (2026-08, na
žiadosť používateľa)** - vonkajšie priestory (balkón/terasa/
predzahrádka) sa teraz oceňujú polovicou (nie tretinou) ceny za m²
vnútornej plochy. Týka sa oboch metód (`comparable_avg` aj
`comparable_market`), keďže obe zdieľajú tú istú konštantu.

**Zároveň opravené dvojité počítanie balkóna v `estimate_price()`
(`comparable_avg`, 2026-08):** cena/m² sa predtým odvodzovala ako
`porovnávaný_byt.price_czk / porovnávaný_byt.area_m2` - keďže
zverejnená cena porovnávaného bytu v sebe už mala hodnotu JEHO balkóna,
táto (nafúknutá) sadzba sa potom násobila plochou cieľového bytu A
NAVYŠE sa pripočítalo 30 % za balkón cieľového bytu - hodnota balkóna sa
tak počítala dvakrát, keď mali porovnávané byty tiež balkóny. Oprava:
`_effective_area_m2()` počíta sadzbu z `price_czk / (area_m2 +
outdoor_area_m2 * FACTOR)` - "efektívnej" plochy porovnávaného bytu,
nie len jeho vnútornej. Regresný test `tests/test_pricing.py::
test_comparable_estimate_does_not_double_count_comparables_own_balcony`.
Naživo overené na Ekospol A208 (34 porovnateľných bytov v projekte,
väčšina s balkónom): medián klesol zo 136 084 na 132 219 Kč/m²,
odhad zo 4 481 037 na 4 353 774 Kč.

**4E. Frontend - implementačné detaily:** `app/static/index.html` je
jeden súbor (inline CSS + vanilla JS, žiadny build krok, žiadna nová
závislosť - `fastapi.staticfiles.StaticFiles` a `FileResponse` sú už
súčasťou FastAPI/Starlette). `main.py` ho mountuje na `/static` a
servíruje na `/` (`GET /`), pridaný je aj `GET /developers` (zoznam
enum hodnôt pre `<select>`). FE volá len existujúce API endpointy
(`/developers`, `/units`, `/unit`, `/refresh/{developer}`) - žiadna
duplicitná logika. Riešenie "presný názov projektu" (fuzzy search
zatiaľ chýba, bod 5 vyššie): pri zmene developera sa z `/units?developer=X`
natiahnu unikátne názvy projektov z cache do `<datalist>` pri poli
"Názov projektu" - funguje to až po prvom "Obnoviť dáta developera".

**Doplnené 2026-08 (na žiadosť používateľa):** keď mali polia "Názov
projektu"/"Číslo bytu" už vyplnenú hodnotu (napr. z predošlého
hľadania), klik do poľa len umiestnil kurzor na koniec - na nové
hľadanie iného projektu/budovy bolo treba starý text najprv ručne
zmazať. Oprava: `focus` na oboch poliach teraz rovno označí celý
existujúci text (`e.target.select()`), takže stačí začať písať a
starý názov sa prepíše. Pridané aj `autocomplete="off"`, aby sa do
toho neplietol prehliadačov vlastný našepkávač histórie formulára
popri `<datalist>`.

**Doplnené 2026-08 (na žiadosť používateľa) - automatické vyprázdnenie
závislých polí:** zmena developera teraz vyprázdni aj "Názov projektu"
aj "Číslo bytu" (starý projekt/byt patril inému developerovi, takže
prestáva dávať zmysel); zmena "Názov projektu" vyprázdni "Číslo bytu"
(rovnaký dôvod). Implementované ako `clearProjectAndUnitFields()`/
`clearUnitField()` volané z `change` na `#developer`, resp. `input` na
`#project-name`. Otestované cez Playwright (zmena projektu → číslo bytu
sa vyprázdni; zmena developera → oboje sa vyprázdni), žiadne console
chyby.

Otestované naživo cez Playwright (headless Chromium, nie len importom
funkcií) - `nav` → vyber developera → klik "Obnoviť dáta" → vyplniť
formulár → klik "Vyhľadať" → screenshot + `console --errors`. Žiadne JS
chyby. **Pri testovaní sa našiel a opravil reálny bug:** pôvodná
detekcia "je `plan_url` obrázok alebo PDF?" kontrolovala len príponu
`.pdf` na konci URL - ale jeden vtedy testovaný developer servíroval PDF
na URL BEZ prípony (`Content-Disposition: attachment` rozhodoval o type,
nie URL), takže sa nesprávne renderoval ako `<img>` (rozbitý obrázok).
Opravené na opačnú logiku: `<img>` sa renderuje len pri jednoznačne
obrázkovej prípone (`.png/.jpg/.jpeg/.svg/.gif/.webp`), inak sa ponúkne
len odkaz "Otvoriť/stiahnuť pôdorys" - obrana zostáva v kóde aj keď
aktuálnych 5 developerov servíruje len obrázky s jednoznačnou príponou
(pre prípad budúceho developera s PDF pôdorysom).

## 5. Známe obmedzenia (aktualizované)

Projekt pôvodne vznikol vo vývojovom prostredí bez prístupu na internet
(pozri git históriu/README pre pôvodný kontext), preto boli prvé
scrapery napísané len na základe odhadu štruktúry stránky.
**Toto obmedzenie už neplatí** — všetkých 5 scraperov bolo odvtedy
overených (a opravených, resp. prepísaných) proti živým stránkam (viď
sekcia 4). Kľúčové poučenia z tejto práce pre prípadného ďalšieho (6.)
developera alebo údržbu existujúcich:
- Nestačí len skontrolovať, či stránka OBSAHUJE `<table>` — treba overiť,
  že tabuľka má aj reálne dátové riadky v surovom HTML (`httpx.get(...)`,
  nie prehliadač s vykonaným JS). Ekospol mal `<table>` s hlavičkou, ale
  bez riadkov (vypĺňal ju JS).
- Keď tabuľka/karty nefungujú, skontroluj `<script>` bloky na stránke pred
  siahnutím po Playwrighte — je bežné, že vývojári embedujú štruktúrované
  dáta (JSON) priamo do stránky pre klientský JS namiesto samostatného API
  volania. Je to rýchlejšie a stabilnejšie parsovať než HTML aj než
  headless browser.
- Pri mapovaní stĺpcov podľa hlavičky nepoužívaj presnú zhodu — reálne
  texty hlavičiek bývajú rozvitejšie, než by si čakal (`generic_table.py`
  teraz používa "obsahuje" zhodu s víťazstvom najdlhšieho kľúča).
- Pri čistom SPA (prázdne HTML aj bez `<script>` JSON-u, ako pri Skanske
  a Central Group) netreba rovno siahnuť po Playwrighte — stiahni
  `<script src>` JS bundly cez `httpx.get()` a prehľadaj ich regexom na
  `/api/`, `fetch(`, `apiBaseUrl`, `endpoint`. Vo všetkých štyroch
  JS-ťažkých prípadoch (Ekospol, Finep, Skanska, Central Group) sa dáta
  nakoniec dali získať bez prehliadača - boli buď priamo v HTML
  (Ekospol/Finep), alebo za verejným JSON API bez auth (Skanska/Central
  Group). Playwright nechaj až na overený posledný prostriedok - doteraz
  nebol treba ani raz.
- Keď objavené API vráti 500 s prázdnym telom, neznamená to nutne zlý
  endpoint - skús pridať/meniť parametre, kým nedostaneš 400 s
  validačnou správou (ASP.NET a podobné frameworky často vypíšu presný
  dôvod pri zlej hodnote, ale mlčia pri neošetrenej výnimke hlbšie v kóde
  pri chýbajúcom kontexte). Central Group takto prezradil, že `sort` musí
  byť `"TotalPrice"` (nie `"id"`), a že bez `locationIds` filtra celý
  endpoint spadne.

## 6. Ako spustiť a testovať

```bash
python3 -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
# playwright install chromium    # zatiaľ nepotrebné - žiadny hotový scraper
                                  # Playwright nepoužíva, len fallback pre budúci web

# offline testy (nepotrebujú internet ani DB):
python3 tests/test_parsing_and_table.py
python3 tests/test_pricing.py

# manuálne overenie jedného scrapera:
python -m app.scrapers.ekospol

# API server:
uvicorn app.main:app --reload
```

Príklad API volania:
```bash
curl -X POST http://localhost:8000/refresh/ekospol
curl "http://localhost:8000/unit?developer=ekospol&project_name=Ekocity%20Hostivar%20A&unit_number=A208"
```

## 7. Odporúčaný ďalší postup pre AI asistenta

Scrapery pre Top 5 (Ekospol, Finep, Sekyra, Skanska, Central Group) sú
hotové a overené naživo (viď sekcia 4) - "pokračuj v projekte" už
neznamená dorábať developerov. FE a cenový index (skelet s placeholder
dátami) sú tiež hotové (sekcia 4D/4E). Ak ťa používateľ požiada o
pokračovanie, odporúčam v tomto poradí (podrobný kontext v sekcii 8):
1. Spusti offline testy, over, že prechádzajú aj u teba.
2. Over, či medzitým nepribudli konkrétnejšie zadania/priority od
   používateľa - hlavne **reálny zdroj dát pre cenový index** (nahradiť
   `data/price_index_praha.json`) je téma, ktorú používateľ explicitne
   odložil na neskôr a môže sa k nej kedykoľvek vrátiť.
3. Všetky položky z pôvodného rozšíreného zadania (bod 4A) sú HOTOVÉ -
   vrátane exportu do PDF (`GET /unit/pdf`, `app/pdf_export.py`,
   2026-08). Zostávajúce nedokončené veci sú len staršie vylepšenia
   (bod 4B) - fuzzy vyhľadávanie projektu, plánovaný refresh (scheduler),
   rate limiting.
4. Ak budeš meniť FE, otestuj ho naozaj v prehliadači (Playwright +
   headless Chromium, `python -m playwright install chromium` ak ešte
   nie je nainštalovaný) - nestačí len skontrolovať, že sa HTML/JS dá
   naimportovať/spustiť bez syntaktickej chyby. Presne takto sa pri
   stavaní FE našiel reálny bug (plan_url PDF vs. image detekcia,
   sekcia 4E) - nebol by odhalený len čítaním kódu.
5. Priebežne dopĺňaj/rozširuj offline testy pre nové prípady, ktoré
   nájdeš na živých stránkach (iný formát ceny, nové stavy bytu a pod.).

## 8. Rozšírené zadanie (2026-08, plné znenie od používateľa)

Používateľ doplnil pôvodné zadanie (sekcia 1) o nasledovné - text je
zámerne ponechaný blízko originálu (čeština, vrátane skratiek), aby sa
nestratili nuansy:

> FE stránka, kde bude možné zadat odkaz na web stránky
> developera/projektu/nabídky bytů (Alternativa – stránky jsou řízené
> offline seznamem a při dotazu se na FE vybere konkrétní
> developer/projekt, Backend pak automaticky prohledává konkrétní
> stránky). APP je schopna zobrazit informace o konkrétní jednotce podle
> standardizovaného označení (typicky číslo jednotky) – patro, dispozice,
> podlahová plocha, celková cena atd. Pokud je k dispozici příslušenství
> (balkon, garáž atd.) je k dispozici flag (ano/ne) nebo hodnota (typicky
> výměra). Pokud je možné stáhnout obrázek / plán BJ.

("BJ" = bytová jednotka.)

**Dôsledky pre appku:**
- **FE vs. offline zoznam** je otvorená otázka, ktorú si používateľ sám
  kladie v zadaní ("Problémy – stránky nejsou standardizované – otázka
  zda směřovat na stránku developera (obecnější) nebo na konkrétní
  projekt"). Používateľ zatiaľ potvrdil len smerovanie na **projektovú
  úroveň** (presnejšie dáta, viac údržby pri nových projektoch) - to je
  presne to, čo dnešné scrapery robia (cielia na cenníky/zoznamy bytov
  konkrétnych projektov, nie na homepage developera).
- **Obrázok/pôdorys BJ** - `UnitData.plan_url`, hotové pre všetkých 5
  developerov (viď sekcia 4).
- **Rozpis podlahovej plochy po miestnostiach (`UnitData.rooms`,
  doplnené 2026-08 na žiadosť používateľa)** - `list[dict]`, napr.
  `[{"name": "Ložnice", "area_m2": 13.8}, ...]`. Spoľahlivo dostupné len
  tam, kde je to štruktúrované dáta (nie krehké parsovanie obrázka):
  **Central Group** (`rooms` pole priamo v `/api/apartment/
  {catalogNumber}` JSON) a **Ekospol** (čistá HTML tabuľka "Místnost" na
  detaile bytu, `<td data-name="...">plocha</td>`). Preskúmané a
  ZAMIETNUTÉ pre Skanska (SVG pôdorys má text miestností, ale
  minimálne 4 nekonzistentné formáty naprieč projektmi, ~50-60 %
  pokrytie - rovnaký problém ako pri hľadaní plochy terasy/
  predzáhradky, viď sekcia 4C nižšie). Finep/Sekyra nemajú rozpis
  miestností dostupný vôbec (statický obrázok bez textu).

  **Architektúra - lazy fetch, nie hromadný (doplnené 2026-08, po
  sťažnosti používateľa na pomalý `/refresh`):** pôvodná implementácia
  volala extra request na KAŽDÝ byt priamo v `fetch_all_units()` -
  pri Ekospole (~920 bytov) a Central Group (537 bytov, navyše ešte k
  už existujúcemu extra requestu na `plan_url`) to spôsobilo, že
  hromadný `/refresh` trval rádovo desiatky minút namiesto sekúnd.
  Používateľ navrhol riešenie sám ("nie je lepšie aby sa stiahli iba
  informácie o tom aké sú projekty a byty a až po výbere bytovej
  jednotky sa stiahnu parametre?") - presne to teraz appka robí:
  - `BaseScraper.fetch_extra_details_for_unit(record) -> dict` - nová
    voliteľná metóda (default `{}`, no-op), ktorú `EkospolScraper` a
    `CentralGroupScraper` prepisujú. Central Group v nej dotiahne
    NAJEDNÁ aj `plan_url` aj `rooms` (zdieľajú jeden `_fetch_time_id()`
    request) - `plan_url` sa preto **už nedotahuje v `fetch_all_units()`
    vôbec** (bulk `/refresh` teraz nerobí ŽIADNE extra requesty na
    jednotku, len zoznam lokalít + apartment-search per lokalita).
  - `main.py`, `GET /unit`: po nájdení `record` v DB, ak `not
    record.rooms or not record.plan_url`, zavolá sa
    `fetch_extra_details_for_unit()` pre TENTO jeden byt, výsledok sa
    zapíše do `UnitRecord` a commitne - takže druhý dopyt na ten istý
    byt už nič nesťahuje (vidno v `scraped_at`, ktorý sa needituje pri
    lazy dopĺňaní).
  - **Namerané naživo:** Central Group `/refresh` kleslo z desiatok
    minút na **1,1 s** (537 bytov, žiadny extra request na jednotku),
    Ekospol na **~25 s** (920 bytov, tabuľkové/JSON parsovanie, tiež
    žiadny extra request na jednotku). Jeden `/unit` dopyt na
    konkrétny byt s lazy dotiahnutím trvá ~1-3 s (1-3 extra requesty),
    druhý dopyt na ten istý byt je okamžitý (dáta už v DB).
- **Užitná plocha (doplnené 2026-08, na žiadosť používateľa; pôvodne
  pomenované "Užitková plocha" - používateľ opravil na gramaticky
  správne "Užitná plocha")** - pole vo FE/PDF exporte, hneď pod
  "Podlahová plocha". V poradí priority:
  1. **Priama hodnota od developera** - nové pole `usable_area_m2` v
     `UnitData`/DB (`app/models.py`, `app/db.py`), na rozdiel od
     pôvodnej verzie tejto funkcie TOTO **je** uložené v DB, nielen
     dopočítané pri renderovaní. Zatiaľ len dvaja developeri toto pole
     priamo publikujú (overené naživo, ostatní - Ekospol, Finep,
     Sekyra - toto pole nikde nezverejňujú):
     - **Central Group** - `innerFloorArea` v `/api/apartment/{catalogNumber}`
       (na rozdiel od `totalFloorArea`, ktorá zodpovedá `area_m2`/
       podlahovej ploche). Lazy, zdieľa jeden request s rozpisom
       miestností cez `_fetch_apartment_detail()`.
     - **Skanska** - "Užitná plocha" v `listitem` bloku na detaile
       bytu (`residential.skanska.cz/.../<code>`) - snapshot API ju
       nemá. Lazy, `fetch_extra_details_for_unit()` v `skanska.py`
       (spolu s "Orientace", ktorá sa zatiaľ ukladá do `orientation`,
       ale FE/PDF ju nezobrazuje - použité len Finepom, ktorý ju má v
       bulk dátach).
  2. Ak priama hodnota chýba, ale je k dispozícii rozpis miestností
     (`rooms` - Ekospol, Central Group), užitná plocha = súčet plôch
     VNÚTORNÝCH miestností (vylučujú sa položky, ktorých názov
     obsahuje balkon/terasa/predzahradka/zahrada/zahradka/lodzie -
     inak by ju vonkajšie priestory umelo nafúkli nad podlahovú
     plochu, viď napr. Central Group H195, kde rozpis obsahuje aj
     "Terasa"). Počíta sa na strane FE (`index.html`,
     `usableAreaInfo()`) a v PDF exporte (`pdf_export.py`,
     `_usable_area_info()`).
  3. Inak (Finep/Sekyra, alebo keď rozpis aj priama hodnota chýbajú)
     sa odhadne ako **podlahová plocha − 5 %** a označí hviezdičkou
     `*` s vysvetľujúcou poznámkou pod výpisom faktov ("Užitná plocha
     je odhad...").
  - Logika priority 2/3 duplikovaná v JS aj Pythone (nie je spoločný
    zdroj) - pri zmene faktora/pravidla treba upraviť oba súbory.
    Overené naživo: Central Group H195 → priama hodnota 33,3 m² (bez
    hviezdičky, `innerFloorArea`); Skanska BM10811 → priama hodnota
    55,5 m² (oproti podlahovej ploche 58,0 m²); Finep 305/C3 (nemá ani
    priamu hodnotu, ani rozpis) → 23,2 m² * (24,4 × 0,95).
  - **Architektúra lazy dotiahnutia (`needs_extra_details`)** - keďže
    `fetch_extra_details_for_unit()` teraz reálne robí HTTP request aj
    pre Skanska (predtým len no-op), pôvodná podmienka v `main.py`
    (`if not record.rooms or not record.plan_url`) by pre Skanska
    (ktorá `rooms` nikdy nemá) spôsobovala **opakovaný extra request
    pri KAŽDOM jednom `/unit` dopyte navždy dokola**, aj keď už máme
    všetko potrebné uložené. Opravené pridaním
    `BaseScraper.needs_extra_details(record) -> bool` (default
    `False`) - každý scraper, ktorý prepisuje
    `fetch_extra_details_for_unit`, MUSÍ prepísať aj toto, presnou
    kontrolou polí, ktoré jeho konkrétna implementácia vie naplniť:
    Ekospol (`not record.rooms`), Central Group (`not record.rooms or
    not record.plan_url`), Skanska (`record.usable_area_m2 is None or
    not record.orientation`). `main.py`/`_load_unit_with_price()`
    volá `scraper.needs_extra_details(record)` namiesto natvrdo
    zapísanej podmienky. Overené naživo cez log servera - druhý dopyt
    na ten istý (už obohatený) byt už nevyvolá žiadny outbound
    request na developera.
- **Príslušenstvo ako flag/hodnota** - pokryté: `UnitData.features`
  (zoznam prítomných vlastností = flag "áno"), `outdoor_area_m2` (výmera
  všetkých vonkajších priestorov spolu) a `outdoor_area_by_type`
  (doplnené 2026-08, na žiadosť používateľa "namiesto Áno chcem veľkosť
  balkóna", následne rozšírené na "aby tam boli samostatné všetky časti
  vonkajších priestorov") - `dict[str, float]` s plochou KAŽDÉHO
  vonkajšieho priestoru ZVLÁŠŤ (kľúč = názov ako vo `features`, napr.
  `{"terasa": 45.8, "predzahradka": 45.3}`), nie len súčet. Pôvodne bolo
  toto pole len pre balkón (`balcony_area_m2`), zovšeobecnené na
  ľubovoľný typ priestoru, keď používateľ chcel to isté aj pre terasu/
  predzáhradku/záhradu. Zdrojové dáta rozlišujú plochu per-príslušenstvo
  len u Ekospolu (`flat.balkon`/`terasa`/`predzahradka` sú priamo čísla,
  nie flagy), Finepu (plocha je pri každom segmente v texte
  príslušenstva - `_parse_accessories()` ju vracia zvlášť pre každý typ,
  nielen v súčte) **a Sekyry (doplnené 2026-08, nahlásil používateľ na
  konkrétnom screenshote detailu bytu z `sekyragroup.cz/units/<id>`)** -
  detail jednotky (ktorý sa aj tak už navštevuje kvôli `plan_url`, žiadny
  extra request navyše) má "attr-box__item" prvky "Balkóny"/"Terasy"/
  "Předzahrádka"/"Lodžie", podmienene renderované len keď ich byt reálne
  má, s presnou plochou v m² - `SekyraScraper._fetch_detail_extras()`
  ich naparsuje popri pôdoryse (`_ATTR_LABEL_TO_KEY` mapuje normalizovaný
  label na náš kľúč). Naživo overené: 26/30 bytov malo aspoň jeden typ s
  plochou. Pri Skanske/Central Group je vonkajší priestor v zdroji stále
  len flag bez plochy, takže pre daný kľúč `outdoor_area_by_type` nemá
  záznam a FE ukáže "Áno (plocha neuvedená)". Vo FE (`index.html`,
  `outdoorTypeDisplay()`) sú teraz
  samostatné riadky "Balkón"/"Terasa"/"Predzáhradka"/"Záhrada"/"Lodžie",
  každý buď s plochou, alebo Nie/Áno (plocha neuvedená); "Vonkajšie priestory
  spolu" ostáva ako súhrnný riadok bez rozpisu (ten je teraz v
  jednotlivých riadkoch nižšie).

  **Lodžia doplnená ako 5. typ vonkajšieho priestoru (2026-08).** Nájdené
  cez Coverage Blueprint audit - appka mala vo `features` surový reťazec
  "lodzie"/"lodžie" od 3 z 5 developerov (Finep 45 %, Sekyra 20 %, Skanska
  39 % bytov), ale FE ho nikde nevypisoval samostatne (jednotky s lodžiou
  namiesto balkóna tak vo FE riadkoch "Balkón"/"Terasa" ukazovali "Nie",
  aj keď mali iný typ vonkajšieho priestoru). Oprava: FE má nový riadok
  "Lodžie" (`outdoorTypeDisplay(unit, "lodzie")` - diakritiku-necitlivé
  porovnanie automaticky zachytí aj "lodžie"/"lodžia"). **Bonus oprava u
  Finepu:** `_OUTDOOR_AREA_KEYS` v `finep.py` predtým neobsahovalo
  "lodzie" vôbec, takže sa jej plocha ani neparsovala z textu
  príslušenstva, ani sa nezapočítavala do `outdoor_area_m2` (súhrnu) -
  bol to skutočný, dovtedy neodhalený bug (nie len chýbajúca funkcia),
  keďže "Vonkajšie priestory spolu" bolo pre bytu s lodžiou systematicky
  podhodnotené. Overené naživo: 41/92 bytov u Finepu má teraz správnu
  plochu lodžie aj v `outdoor_area_by_type`, aj v súčte.

> Dává smysl se soustředit na developery, které mají u RB možnost vkládat
> 2 plomby + zazelenal jsem cca. Top5 které můžeme použit v základů:
> Ekospol, Finep, Sekyra, Skanska, Central group.

**Dôsledok:** zmena Top 5 zoznamu oproti sekcii 1 - Trigema von, Sekyra
Group dnu (implementované, viď sekcia 4). Kontext "2 plomby" (RB =
pravdepodobne banka, napr. Raiffeisenbank) je z interného Word dokumentu
používateľa, appke nedostupný - nemá vplyv na scraper, len na výber
developerov.

> Navíc: pokud je k dispozici konkrétní okres/město – bude k dispozici
> indexová cena za m2 (v DB/vstupní soubor), kterou app pronásobí
> podlahovou plochu BJ a zobrazí "odhadnutou" cenu. Stránka si dopočítává
> cenu za m2 podle ceny a podlahové plochy. Export informací o BJ (pdf
> format např.).

**Dôsledky:**
- Prepočet ceny/m² z ceny a plochy už appka robí (`UnitData.
  price_per_m2` property v `models.py`).
- **Cenový index podľa okresu/mesta** — implementované ako funkčný
  skelet s placeholder dátami (2026-08, viď sekcia 4D) - používateľ
  explicitne potvrdil, že reálny zdroj dát doplní neskôr, cieľom teraz
  bolo mať to "zapojené a dávajúce zmysel" end-to-end. Mapovanie
  projekt/adresa → lokalita nie je jednotné naprieč developermi (viď
  `UnitData.locality`, sekcia 4C/4D) - vyriešené best-effort skúšaním
  viacerých odvodených tvarov kľúča, nie presnou zhodou.
- **PDF export** - HOTOVO (2026-08), viď sekciu 7, bod 3 a
  `app/pdf_export.py`.

> Problémy – stránky nejsou standardizované – otázka zda směřovat na
> stránku developera (obecnější) nebo na konkrétní projekt (přesnější,
> ale při přidání projektu bude třeba aktualizovat). Otázky – Bude
> přístup z banky na stránky mimo? Bude k dispozici nějaké API na
> napojení stránek?

**Otvorené otázky - stav odpovedí:**
1. Developer- vs. projekt-úroveň → odpovedané vyššie (zostáva projekt).
2. **Prístup z banky na externé stránky naživo?** — **ZODPOVEDANÉ
   (2026-08): áno, nemal by byť problém.** Architektúra preto zostáva
   priamy `httpx` scraping z bežiaceho backendu, bez batch/feed vrstvy -
   toto rozhodnutie sa už NEMÁ prehodnocovať bez nového explicitného
   podnetu od používateľa.
3. **Bude k dispozícii oficiálne API od developerov?** — stále otvorené.
   Ak sa objaví, dnešné scrapery (postavené na neoficiálnych, reverzne
   inžinierovaných endpointoch/HTML) by sa dali nahradiť/doplniť
   stabilnejším zdrojom - kým sa nepotvrdí, appka pokračuje so
   scrapingom verejne dostupných stránok/API (viď sekcia 4 pre presné
   endpointy jednotlivých developerov).
