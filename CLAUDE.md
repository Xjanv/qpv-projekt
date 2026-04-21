# QPV Projekt

## Struktura
- `02-narezove-plany/app_quality.py` — hlavni aplikace (Streamlit + Guillotine algoritmus, ~1850 radku)
- `02-narezove-plany/app_quality_maxrects_backup.py` — zaloha predchozi MaxRects verze
- `02-narezove-plany/_archive/` — stara JS verze (neaktivni, jen pro referenci)
- `02-narezove-plany/requirements.txt` — Python deps (streamlit, matplotlib, reportlab)

## Spusteni
```bash
cd 02-narezove-plany && streamlit run app_quality.py
```

## Testovani
```bash
cd 02-narezove-plany && python test_comprehensive.py   # 10 testu vcetne guillotine feasibility
```

Mock streamlit pro unit testy algoritmu:
```python
import sys; sys.modules['streamlit'] = type(sys)('mock')
from app_quality import parse_parts, parse_formats, optimize, Objectives
```

## Architektura app_quality.py
- Radky 1-150: datove modely (PartSpec, SheetFormat, Item, FreeRect, Placement, Pattern, Objectives)
- Radky 200-400: Guillotine geometrie (_guillotine_score, _guillotine_split, _guillotine_find_best)
- Radky 400-500: generovani vzoru (_gen_pattern s `split_rule` param, _generate_all_patterns)
- Radky 500-620: cutting-stock vyber (_best_fresh_pattern, _select_patterns_mip)
- Radky 680-820: hlavni optimizer (optimize, _optimize_single_run)
- Radky 830-960: vizualizace + PDF (draw_sheet_figure, build_pdf)
- Radky 1000+: Streamlit UI (main)

## Dulezite konvence
- PDF pouziva DejaVu Sans font (ceska diakritika) s fallback na Helvetica
- `unsafe_allow_html=True` — vzdy escapovat uzivatelske vstupy pres `html_mod.escape()`
- Utilization se pocita z usable area (po odecteni margins), ne z celkove plochy archu
- `st.file_uploader` + `st.rerun()` vyzaduje `_project_imported` flag proti nekonecne smycce
- Demand coverage musi vzdy zobrazit `st.error()` pokud nejaky dilec chybi

## Guillotine algoritmus
- `_gen_pattern` ma parametr `split_rule` (SAS/LAS/SLAS/MAXAS/MINAS) — pri pridani noveho mista pro item se provede jeden guillotinovy rez na 2 sub-recty
- `_generate_all_patterns` cykluje 3 heuristiky x 5 split rules = 15 kombinaci
- Zaloha MaxRects verze: `app_quality_maxrects_backup.py` — pro navrat staci prekopirovat zpet
- Gilotinova validace v testu 10: rekurzivne overi, ze vsechny vzory jsou fyzicky rezatelne

## Gotchas
- `matplotlib.font_manager.findfont()` nikdy nevraci None — vzdy overit "dejavu" in path.lower()
- Vnoreny `st.download_button` uvnitr `st.button` v Streamlit nefunguje — pouzit primo download_button
- Streamlit blokuje hlavni vlakno behem optimalizace — budget 600s = 10min freeze
- Guillotine constraint: kazde umisteni dilce rozdeli volny obdelnik na max 2 sub-recty — tzn. vzory jsou vzdy fyzicky rezatelne pilou
