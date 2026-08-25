# Byty Scraper — prototyp

Aplikácia, ktorá pre zadanú bytovú jednotku (developer + projekt + číslo
bytu) vráti jej parametre a odhadovanú/zverejnenú cenu. Podporovaní
developeri (podľa toho, ktorí majú u RB možnosť vkladania 2 plomb):
**Ekospol, Finep, Sekyra Group, Skanska Reality, Central Group** - presne
týchto 5, žiadny iný developer sa do appky zámerne nepridáva.

Súčasťou je aj jednoduché webové FE (`app/static/index.html`, servírované
priamo z FastAPI na `/`) - vyhľadanie bytu, zobrazenie pôdorysu/obrázka,
oba cenové odhady (porovnanie v projekte aj podľa cenového indexu
lokality) a tlačidlo na obnovenie dát developera.

## Stav implementácie (dôležité, prečítaj pred použitím)

Pôvodne toto vzniklo v prostredí bez prístupu na internet, takže scrapery
neboli overené proti živým stránkam. Odvtedy boli **všetkých 5
scraperov overených a opravených/prepísaných proti živým stránkam**
(2026-08) - **žiadny nepoužíva Playwright**, všetky volajú buď statické
HTML, alebo priamo objavené JSON API:

| Developer | Stav scrapera | Poznámka |
|---|---|---|
| Ekospol | **Funkčný, overený naživo** (~920 bytov) | Tabuľka na `/cenik` je len kostra vypĺňaná JS-om - dáta sa parsujú priamo z `window.flats` JSON vloženého v `<script>` na tej istej stránke (pozri `ekospol.py`) |
| Finep | **Funkčný, overený naživo** (102 bytov) | `/cenik` nemá `<table>` ani JSON - byty sú v HTML "kartách" (`div.tile`), stránkované cez `?page=N` (pozri `finep.py`) |
| Sekyra Group | **Funkčný, overený naživo** (30 bytov) | Jeden centrálny prehľad všetkých bytov na `/units/` (statická HTML tabuľka, žiadne stránkovanie) - najjednoduchší z piatich (pozri `sekyra.py`). Pozor: `www.sekyragroup.cz` má nesediaci TLS certifikát, treba `sekyragroup.cz` bez `www.` |
| Skanska Reality | **Funkčný, overený naživo** (360 bytov) | SPA bez dát v HTML, ale s verejným JSON API (`/api/v1/filters/apartments_page_cs/snapshot`) - žiadny Playwright netreba (pozri `skanska.py`) |
| Central Group | **Funkčný, overený naživo** (537 bytov) | SPA s interným REST API (`/api/apartment/search`) nájdeným prehľadaním JS bundlov - **musí sa volať per lokalita** (`locationIds=...`), volanie bez filtra na lokalitu spoľahlivo padá na serveri (pozri `central_group.py`) |

## Obrázok/pôdorys jednotky (`plan_url`)

Všetkých 5 developerov teraz vracia aj `plan_url` - odkaz na obrázok/PDF
pôdorysu jednotky, ak ho developer zverejňuje. Formát sa líši (Ekospol/
Finep: PNG pôdorys; Skanska: SVG pôdorys; Sekyra: JPEG "Plán jednotky",
zisťovaný z detailu jednotky; Central Group: GIF pôdorys z interného CMS
"resource" API).
**`unit_number`** pre Central Group je "H 338" (ako u developera), nie
interný kód "189-08-338".

**Central Group** bol najťažší z piatich - žiadna statická analýza JS
bundlu ani skúšanie API naslepo (`/api/apartment/{catalogNumber}`,
rôzne `/api/resource/image/{id}/{typ}` kombinácie) nenašlo funkčný
endpoint, kým používateľ neposlal skutočný request z DevTools Network
tabu. Skutočný endpoint:

    GET /api/resource/image/24885/ground-plan
        ?timeId=<timeId>&langId=1&boIdMapping[13]=<catalogNumber>

`24885` je globálna konštanta CMS bloku (rovnaká naprieč projektmi),
`boIdMapping[13]` je `catalogNumber` (už máme z `/api/apartment/
search`). Vyžaduje 1 extra request na jednotku - naživo overené
537/537 (100 %) s náhodnou vzorkou 20/20 reachable. Detaily v
PROJECT_BRIEF.md, sekcia 4C.

**Dôležité (doplnené 2026-08):** tento extra request sa u Central Group
**nerobí hromadne pre všetky byty naraz** pri `/refresh` - to pri 537
bytoch spôsobovalo, že hromadný refresh trval desiatky minút. Namiesto
toho sa `plan_url` (a rozpis miestností, viď nižšie) dotiahne až
**lenivo**, keď si niekto konkrétny byt vyhľadá cez `GET /unit` (a
uloží sa do cache, takže sa druhýkrát už nefetchuje) - viď
PROJECT_BRIEF.md, sekcia 8 pre detaily architektúry.

Pri niektorých developeroch sa `plan_url` skladá **bez extra requestu za
byt** (URL sa dá poskladať priamo z dát, ktoré scraper aj tak už sťahuje):
Finep (`data-item-id` atribút karty), Skanska (`code` pole z API, pozor
na malé písmená v URL súboru). Výnimka je **Sekyra**, kde treba navyše
1 request na detail/pôdorys každej jednotky (zvládnuteľné pri desiatkach
bytov, u Ekospolu s ~1000 bytmi by to bolo neúmerne veľa - preto sa tam
`plan_url` skladá bez návštevy detailu).

**Ekospol** potrebuje interný slug projektu (URL slug "ekocity-hostivar-b"
≠ interný slug "eko_hostivar_b") - ten sa zisťuje **1 requestom na
projekt** (nie za byt) z detailu jednej náhodnej jednotky. **Pôvodne** sa
skladal z marketingového obrázka `.project-pic-col` na `/cenik` stránke,
čo bolo lacnejšie (0 extra requestov), ale **nespoľahlivé** - naživo sa
zistilo (nahlásil používateľ), že tento obrázok pri projekte "Ekocity
Hostivar B" chybne odkazoval na priečinok "Hostivar A" (chyba na strane
Ekospolu, zdieľaný/nesprávny asset), takže sa skladal nefunkčný
`plan_url` pre celý projekt. Detail jednotky je jediný overený spoľahlivý
zdroj.

**Pozor pri Ekospole:** `plan_url` sa dá skonštruovať pre každý byt, ale
naživo funguje spoľahlivo len pre **voľné** byty - pri predaných sa
súbor s pôdorysom zjavne z webu odstráni (vzorka: 5/5 voľných bytov malo
funkčný obrázok, len 7/10 predaných).

## Rozpis miestností (`rooms`)

Pri **Central Group** a **Ekospol** appka vracia aj `rooms` - rozpis
podlahovej plochy po jednotlivých miestnostiach (napr. "Ložnice - 13,8
m²"), tak ako ho uvádza developer. Central Group ho má priamo v JSON
(`/api/apartment/{catalogNumber}`), Ekospol v HTML tabuľke "Místnost" na
detaile bytu. Finep/Sekyra/Skanska ho nemajú spoľahlivo dostupný
(statický obrázok bez textu, resp. nekonzistentné SVG formáty) - pozri
PROJECT_BRIEF.md.

Rovnako ako `plan_url` pri Central Group, aj toto sa dotahuje **lenivo**
- len pre byt, ktorý si niekto vyhľadá cez `GET /unit`, nie hromadne pri
`/refresh` (viď vyššie).

## Odkaz na byt u developera (`detail_url`)

Popri `plan_url` (obrázok/PDF pôdorysu) appka vracia aj `detail_url` -
priamy odkaz na stránku TEJTO KONKRÉTNEJ jednotky u developera (na
rozdiel od `source_url`, ktorý je pri niektorých developeroch spoločný
pre celý cenník). Vypĺňa sa pre všetkých 5 - Ekospol (`uri` z
`window.flats`), Finep (`.tile-link` z karty), Skanska (`detail_url` z
API), Sekyra (odkaz z bunky "Číslo", zisťovaný spolu s `plan_url`),
Central Group (`/byt-detail/{catalogNumber}`, poskladané z poľa, ktoré
scraper aj tak už má z `/api/apartment/search`, žiadny extra request).
Vo FE sa zobrazuje ako odkaz "Zobraziť na stránke developera ↗" priamo
pod nadpisom výsledku.

**Prečo takto:** namiesto škatuľkovania na presné CSS triedy (ktoré sa
menia pri redesignoch) mapujú statické scrapery stĺpce **podľa textu
hlavičky tabuľky** (`generic_table.py` + `parsing_utils.py`) — je to
odolnejšie. Zhoda hlavičiek je "obsahuje" (nie presná zhoda), keďže
reálne hlavičky bývajú rozvitejšie ako alias kľúče (napr. "Podlahová
plocha bytu" vs. alias "plocha"). `generic_table.py` navyše ošetruje
weby, ktoré duplikujú obsah bunky pre mobilné zobrazenie (Bootstrap
`d-none`/`d-block` triedy) - bez toho by sa do jednej hodnoty zlepil aj
skrytý text.

**Dôležité zistenia z Ekospolu, Finepu, Skanska a Central Group:** nie
každý developer, ktorý má na stránke `<table>`, ju aj reálne vypĺňa v
HTML - Ekospol necháva `<table>` prázdnu a plní ju JavaScriptom z
`window.flats` JSON premennej vloženej v `<script>` tagu. Finep `<table>`
vôbec nepoužíva - byty sú priamo v HTML ako "karty" (`div.tile`),
stránkované cez `?page=N`. Skanska aj Central Group sú čisté SPA (prázdne
HTML, dáta len cez JS), no OBE mali verejné JSON API bez potreby
autentifikácie alebo prehliadača:
- Skanska: `/api/v1/filters/apartments_page_cs/snapshot`.
- Central Group: `/api/apartment/search` (+ `/api/system/time-version`
  pre `timeId` a `/api/location` pre zoznam projektov) - tu ale volanie
  BEZ `locationIds` filtra spoľahlivo spadne na serveri (500), treba teda
  iterovať lokality a volať search pre každú zvlášť.

**V žiadnom z piatich prípadov sa nakoniec nemusel použiť Playwright** -
API endpointy sa vždy našli prehľadaním JS bundlov stránky (`httpx.get`
na `<script src>` z HTML + regex na `/api/`, `fetch(`, `apiBaseUrl`,
`endpoint`...), nie cez DevTools Network tab (na to by bol potrebný
pripojený prehliadač). `playwright_base.py` ostáva v kóde ako
zdokumentovaný fallback pre prípad budúceho 6. developera, kde by sa
API naozaj nedalo nájsť.

## Ako doladiť tabuľkový scraper (aktuálne Sekyra)

```bash
python -m app.scrapers.sekyra
```

Ak vypíše chybu o nenájdenej tabuľke/stĺpcoch, otvor stránku v
prehliadači, pozri skutočné texty hlavičiek a priprav chýbajúci alias v
`HEADER_ALIASES` v danom module (`app/scrapers/<developer>.py`).

## Ako nájsť skryté API na JS-ťažkom webe (Skanska/Central Group ako vzor)

1. Stiahni hlavné JS bundly stránky (`httpx.get` na `<script src>` z
   HTML) a prehľadaj ich regexom na `/api/`, `fetch(`, `apiBaseUrl`,
   `endpoint` a podobne.
2. Over nájdené endpointy priamym `httpx.get`/`curl` (mimo prehliadača).
   Ak vrátia 400/validation error, telo chyby často prezradí presné
   povolené hodnoty parametrov (takto sa napr. zistilo, že Central Group
   `sort` očakáva `"TotalPrice"`, nie `"id"`).
3. Ak endpoint vyžaduje "session"/"time-version" parameter, hľadaj ďalší
   jednoduchý GET endpoint, ktorý ho vracia (Central Group:
   `/api/system/time-version`).
4. Ak API naozaj nie je dostupné (skús to len ako naozaj poslednú
   možnosť): doplň `wait_selector` v novom scraperi (pozri
   `playwright_base.py`) a priprav parser pre kartový layout namiesto
   `find_price_table` (ten funguje len pre `<table>`).

## Architektúra

```
app/
  models.py       - spoločný dátový model (UnitData, PriceEstimate, enumy)
  db.py           - SQLAlchemy modely (UnitRecord, PriceHistoryRecord) + SQLite
  repository.py   - upsert/čítanie jednotiek z DB, history cien
  pricing.py      - výpočet/odhad ceny (nezávislý od DB, testovateľný samostatne)
  price_index.py  - orientačný cenový index podľa lokality (Kč/m² × plocha)
  registry.py     - mapovanie Developer -> trieda scrapera
  main.py         - FastAPI endpointy + servírovanie FE
  static/
    index.html    - jednostránkové FE (vanilla JS, žiadny build krok)
  data/
    price_index_praha.json  - vstupný súbor cenového indexu (PLACEHOLDER hodnoty)
  scrapers/
    base.py             - spoločné HTTP rozhranie (httpx klient)
    parsing_utils.py    - parsovanie čísel/cien/stavov/skratiek
    generic_table.py    - hľadanie a parsovanie <table> podľa hlavičiek
    playwright_base.py  - headless browser fallback pre JS weby
    ekospol.py, finep.py, sekyra.py  - scrapery pre statické HTML weby
    skanska.py, central_group.py     - scrapery pre SPA weby (volajú objavené JSON API)
tests/
  test_parsing_and_table.py  - offline testy parsovania a extrakcie z tabuľky
  test_pricing.py            - offline testy pricing engine
```

### Prečo takto (zhrnutie z diskusie)

- **Scraper na mieru pre každý web** — každý developer má inú štruktúru,
  spoločné je len finálne API a dátový model (`UnitData`).
- **DB ako cache + história cien** — nescrapuje sa pri každom dopyte,
  dáta sa refreshujú cez `POST /refresh/{developer}` a ukladajú so
  zápisom histórie ceny pri zmene.
- **Pricing engine je oddelený od scraperov aj od DB** (typované len cez
  `TYPE_CHECKING`) — dá sa testovať a neskôr vymeniť/rozšíriť (napr. o
  regresný model) bez zásahu do zvyšku aplikácie.

## Výpočet ceny

1. Ak developer cenu priamo publikuje → použije sa priamo (metóda
   `published`).
2. Ak nie ("na dotaz") → zoberú sa ostatné byty **v tom istom projekte**
   so zverejnenou cenou, spočíta sa medián ceny za m², upraví sa o
   rozdiel v poschodí (+0.5 % za poschodie oproti mediánu porovnávaných)
   a pripočíta sa hodnota vonkajších priestorov (balkón/terasa/záhrada)
   ako 50 % z ceny za m² (`OUTDOOR_AREA_VALUE_FACTOR` v `pricing.py`).
   Vráti sa aj `confidence` (low/medium/high podľa
   počtu porovnateľných bytov) a zoznam `notes` vysvetľujúcich výpočet.

Toto je zámerne jednoduchý, transparentný model - keď bude v DB dosť dát
naprieč projektmi/developermi, dá sa nahradiť regresiou bez zmeny API.

### Druhý, nezávislý odhad — cenový index podľa lokality

Popri odhade vyššie appka počíta aj **druhý, nezávislý** odhad (ak je
lokalita k dispozícii - `UnitData.locality`, vypĺňa ju všetkých 6 zo 6
developerov, aj keď u Finepu (~87 %) a Sekyry (~97 %) nie stopercentne -
pár projektov ju na zdrojovej stránke jednoducho neuvádza): vynásobí
podlahovú plochu jednotky orientačnou cenou za m² pre danú lokalitu
(`app/price_index.py` + `app/data/price_index_praha.json`). Vracia sa v
API ako samostatné pole `index_price_estimate` (metóda
`comparable_market`), nezávisle od toho, či prvý odhad/zverejnená cena
existuje - dávajú zmysel vedľa seba ako "developer vs. orientačný trh".

**Hodnoty v `price_index_praha.json` sú zatiaľ len ilustratívny
placeholder** (viď poznámka priamo v súbore) - úloha bola zámerne
postavená tak, aby fungovala end-to-end už teraz, kým sa nedoplní
skutočný zdroj dát (napr. ČSÚ, cenová mapa). Nahradenie reálnymi
hodnotami znamená len prepísať tento JSON súbor v rovnakom tvare
(mapa "lokalita" → Kč/m²) - žiadny zásah do kódu. FE aj API odpoveď
túto skutočnosť viditeľne označujú (`index_price_estimate.notes` a
`placeholder-warning` vo FE).

## Spustenie

```bash
python3.11 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
# playwright install chromium   # zatiaľ nepotrebné, žiadny hotový scraper ho nepoužíva

# offline testy (bez siete, bez DB):
python3 tests/test_parsing_and_table.py
python3 tests/test_pricing.py

# API server + FE:
uvicorn app.main:app --reload
# FE beží na http://localhost:8000/
# REST API (aj bez FE) na tých istých endpointoch, napr.
# http://localhost:8000/unit?developer=ekospol&project_name=...&unit_number=...
```

FE vo formulári vyžaduje **presný** názov projektu (fuzzy vyhľadávanie
zatiaľ nie je implementované, viď "Ďalšie kroky") - pri výbere
developera sa preto z cache automaticky ponúkne `<datalist>` nápoveda
s reálnymi názvami projektov (treba najprv aspoň raz kliknúť "Obnoviť
dáta developera").

Príklad použitia po naštartovaní servera:

```bash
curl -X POST http://localhost:8000/refresh/ekospol
curl "http://localhost:8000/unit?developer=ekospol&project_name=Ekocity%20Hostivar%20A&unit_number=A208"
curl "http://localhost:8000/units?developer=ekospol"
```

## Právne a etické poznámky (neprehliadnuť)

- Pred nasadením do produkcie skontroluj **robots.txt** a **obchodné
  podmienky** každého webu — scraping môže byť v rozpore s ich ToS.
  Bezpečnejšia (a presnejšia) cesta je osloviť developerov priamo a
  dohodnúť sa na dátovom feede/API, ak existuje.
- Dodržiavaj rozumný **rate limiting** (v `base.py` zatiaľ nie je nič
  agresívne - jeden request naraz, žiadna paralelizácia) a nastav
  primeraný interval refreshovania (napr. raz denne, nie pri každom
  dopyte).
- Neuchovávaj scrapnuté dáta dlhšie, než je nutné, a označuj `scraped_at`/
  zdrojové URL (už je súčasťou modelu) pre transparentnosť pôvodu dát.

## Ďalšie kroky (návrh priorít)

Top 5 scraperov (Ekospol, Finep, Sekyra, Skanska, Central Group) sú
hotové a overené naživo. Prístup appky na externé developerské stránky
naživo bol potvrdený ako nie problém - architektúra ostáva priamy
scraping (žiadny batch/feed mimo appky).

1. ~~Obrázok/pôdorys jednotky na stiahnutie.~~ Hotovo pre všetkých 5
   (pozri sekciu vyššie).
2. ~~Cenový index podľa okresu/mesta.~~ Hotovo ako funkčný skelet -
   `price_index.py` + `data/price_index_praha.json`, zapojené do
   `/unit` ako `index_price_estimate` a zobrazené vo FE (pozri sekciu
   "Druhý, nezávislý odhad" vyššie). **Hodnoty v JSON súbore sú zatiaľ
   len placeholder** - dohodnuté zámerne, reálny zdroj (napr. ČSÚ,
   cenová mapa) príde neskôr a nahradí sa len prepísaním JSON súboru.
   `locality` pre Finep a Sekyra bola pôvodne 0 %, doplnená 2026-08
   (druhá vlna) - pozri PROJECT_BRIEF.md pre presný zdroj/postup.
3. ~~Frontend.~~ Hotovo - `app/static/index.html`, servírované z
   FastAPI (`/`). Vyhľadanie bytu podľa developera + presného názvu
   projektu + čísla bytu (s `<datalist>` nápovedou z cache), zobrazenie
   pôdorysu/PDF, oboch cenových odhadov, tlačidlo na refresh dát.
   Otestované naživo cez Playwright (headless Chromium) - žiadne JS
   chyby v konzole, obrázky sa načítavajú, oba typy pôdorysu (PDF aj
   PNG/SVG) sa zobrazujú správne.
4. ~~**Export do PDF**~~ — Hotovo. `GET /unit/pdf` vráti PDF s celým
   detailom jednotky (fakty, pôdorys, rozpis miestností, oba cenové
   odhady) - generované cez Playwright/headless Chromium
   (`app/pdf_export.py`), rovnaký vizuálny štýl ako FE. Tlačidlo
   "Exportovat do PDF" vo FE vedľa nadpisu výsledku.
5. **Fuzzy vyhľadávanie projektu** — API očakáva presný `project_name`
   tak, ako je uložený v DB (rôzny naprieč developermi - napr. Ekospol/
   Skanska/Central Group/Sekyra používajú "marketingový" názov projektu,
   Finep názov konkrétnej budovy/fázy). FE to zatiaľ obchádza
   `<datalist>` nápovedou z cache, ale to nenahrádza fuzzy matching
   (napr. `rapidfuzz`) pre používateľa, ktorý ešte nemá dáta v cache.
6. **Plánovaný refresh** — momentálne sa scraping spúšťa len on-demand
   (`auto_refresh` pri `/unit`, alebo ručne `/refresh/{developer}`
   / tlačidlo vo FE). Chýba scheduler (cron/APScheduler) na pravidelné
   obnovovanie dát.
7. **Rate limiting / zdvorilé scrapovanie** — `base.py` zatiaľ nemá
   žiadne oneskorenia medzi requestmi (Central Group scraper napr. robí
   8+ requestov za sebou, jeden na lokalitu). Pred nasadením do produkcie
   treba pridať throttling a skontrolovať `robots.txt`/ToS každého webu
   (pozri sekciu "Právne a etické poznámky" vyššie).
8. ~~Zvážiť odstránenie `playwright` z `requirements.txt`~~ - už sa
   aktívne používa (`app/pdf_export.py`, export do PDF cez headless
   Chromium). `playwright_base.py` (fallback pre scraping JS-ťažkých
   webov) zostáva nepoužitý.

**Otvorená otázka, ktorú kód sám nevie zodpovedať** (podrobnejšie v
PROJECT_BRIEF.md, sekcia 8): existuje/bude k dispozícii oficiálne API
od developerov na napojenie, namiesto scrapovania ich verejných
(neoficiálnych) stránok/API? (Prístup na externé stránky je už
potvrdený ako v poriadku - to už nie je otvorená otázka.)
