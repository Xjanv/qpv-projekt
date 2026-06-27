# Režim „Nejhustší rozkres na arch" — návrh

Datum: 2026-06-27
Soubor: `02-narezove-plany/app_quality.py`

## Problém

Klient (Radek) má jeden (nebo více) dílec a několik archů z **různých materiálů**.
Potřebuje pro **každý materiál zvlášť** vidět nejhustší možný rozkres jednoho archu
(maximum kusů na arch) + výtěžnost + nákres. Nezajímají ho počty kusů, počet archů,
ani zbytkový arch.

Současná appka dělá pravý opak: bere všechny archy jako **zaměnitelnou zásobu** a
vybere nejvýhodnější kombinaci, ostatní označí jako „nepotřebné". Pro různé
materiály je to chybné — materiály nelze zaměnit.

## Řešení: přepínač režimu úlohy (varianta A)

Nad vstupními tabulkami bude přepínač *Typ úlohy*:

- **Pokrýt zakázku** (default) — současné chování beze změny. Pro zakázky s počty
  kusů a více dílci; archy jako zaměnitelná zásoba; MIP výběr + ořez na poptávku.
- **Nejhustší rozkres na arch** (nový) — pro každý zadaný arch samostatně najde
  nejhustší rozkres. Žádné počty, žádné kombinování archů, žádný zbytek.

## Chování nového režimu

### Vstup
- Tabulka dílců: sloupec **Počet kusů se schová** (nehraje roli). Zůstávají rozměry
  + „Lze otočit".
- Tabulka archů: každý řádek = jeden materiál. Sloupec **Dostupné množství se
  ignoruje** (může zůstat zobrazený, jen se nepoužije).
- Postranní panel: posuvníky **„Co optimalizovat" se schovají** (vždy jde jen o max.
  zaplnění). **Okraj archu + mezera mezi dílci zůstávají** a platí.
- „Maximální počet variant nařezání" se v tomto režimu nepoužije (schovat).

### Výpočet
Pro **každý arch zvlášť** se najde jeden nejhustší vzor pomocí existujících
generátorů (`_grid_pattern`, `_shelf_pattern`, `_gen_pattern` přes heuristiky a
split rules). Dílce se berou jako **neomezené**. Vybere se vzor s nejvyšším
`total_items` (resp. nejvyšší `utilization`). Rotace se respektuje podle
„Lze otočit" + globálního přepínače otáčení.

Žádný MIP, žádný `_trim_overproduction`, žádné `parent_key`/setup počítání.

### Výstup
Pro každý arch jedna karta:
> **Materiál: <název> (Š × V mm)** — N ks/arch, výtěžnost X %
> [nákres jednoho archu]

Nákres používá stávající `draw_sheet_figure` (počet opakování = 1).

### Více dílců
Packer zaplní arch co nejhustěji — může dominovat jeden (prostorově nejúspornější)
dílec. Poměrové/rovnoměrné zastoupení dílců se **neřeší** (YAGNI). Pokud to klient
reálně bude potřebovat, doděláme v samostatné iteraci.

## Dotčená místa v kódu

- **UI (`main`)**: přidat `st.radio`/`st.segmented_control` pro režim; podmíněně
  schovat sloupec počtu, posuvníky, max_patterns; větvení výsledkové sekce.
- **Nová funkce** `densest_per_sheet(parts, formats, margin, gap, force_no_rotate)
  -> List[Tuple[SheetFormat, Pattern]]` — pro každý formát vrátí nejhustší vzor.
  Staví na existujících `_grid_pattern`/`_shelf_pattern`/`_gen_pattern`.
- **Výsledková sekce**: nová větev, která vykreslí kartu na každý arch. PDF export
  v tomto režimu = jedna stránka na arch (volitelné — lze přidat později).

## Co se NEmění

- Optimalizační jádro režimu „Pokrýt zakázku" zůstává netknuté.
- Generátory vzorů, `draw_sheet_figure`, mm konverze — beze změny, jen se použijí.

## Testy

- Nový test: 1 dílec + 3 různé archy → každý arch dostane vlastní nejhustší vzor,
  `total_items` ≥ než greedy a == grid optimum tam, kde mřížka platí.
- Existující sada `test_comprehensive.py` musí zůstat 10/10 (režim „Pokrýt
  zakázku" se nedotýká).
