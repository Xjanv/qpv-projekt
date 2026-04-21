# QPV - Narezovy plan Optimizer

Aplikace pro optimalizaci rozmisteni dilcu na archy materialu s minimalizaci odpadu.
Vyvinuto pro QPV spol. s r.o.

## Funkce

- **Guillotine algoritmus** s portfolio heuristik (BAF, BSSF, BLSF) a split rules (SAS, LAS, SLAS, MAXAS, MINAS) — vzory jsou vzdy fyzicky rezatelne pilou
- **Vice formatu archu** — program sam vybere nejlepsi kombinaci
- **Vicecilova optimalizace** — vahy pro cenu, pocet archu, odpad, rezy, formaty
- **Limit poctu vzoru** — omezi pocet ruznych rozlozeni pro vyrobu
- **Vizualizace** — nakresy vzoru s kotami a rozmery
- **PDF export** — souhrn + detailni nakresy s ceskou diakritikou
- **CSV import** — nahrani dilcu a formatu ze souboru
- **Export/import projektu** — ulozeni/nacteni kompletniho nastaveni jako JSON
- **Pokryti poptavky** — tabulka s kontrolou, zda jsou vsechny dilce pokryty
- **Benchmark** — srovnani vysledku pro 10/60/300 sekund

## Instalace

```powershell
cd 02-narezove-plany
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
```

## Spusteni

```powershell
cd 02-narezove-plany
streamlit run app_quality.py
```

Aplikace se otevre na `http://localhost:8501`.

## Pouziti

1. V **sidebaru** nastav okraj, mezeru (kerf), dobu hledani a priority optimalizace.
2. V hlavni oblasti vyplnte **dilce** (CSV format: `nazev, sirka, vyska, pocet, rotace`).
3. Vyplnte **dostupne archy** (CSV format: `nazev, sirka, vyska, cena, mnozstvi`).
4. Kliknete na **Spustit optimalizaci**.
5. Prohlednete vysledky — metriky, pokryti poptavky, nakresy vzoru.
6. Stahnete **PDF report** nebo spuste **Srovnavaci test**.

### CSV format dilcu

```
nazev, sirka_cm, vyska_cm, pocet_kusu, rotace(1/0)
```

Priklad:
```
Deska A, 30, 40, 500, 1
Hrebet, 6, 40, 250, 0
```

### CSV format archu

```
nazev, sirka_cm, vyska_cm, cena_kc, dostupne_mnozstvi(0=neomezene)
```

Priklad:
```
Velky arch, 100, 140, 90, 0
Maly arch, 50, 70, 30, 200
```

## Struktura projektu

```
02-narezove-plany/
  app_quality.py      # Hlavni aplikace (Streamlit + algoritmus)
  requirements.txt    # Python zavislosti
  qpv_logo.png        # Logo pro PDF export
  assets/
    qpv-logo.svg      # Logo pro UI
  .streamlit/
    config.toml       # Streamlit konfigurace (theme, server)
  _archive/           # Stara JS verze (neaktivni)
```

## Poznamky

- Vyssi cas vypoctu = vice otestovanych variant = lepsi vysledek (s klesajicimi prinosy).
- Algoritmus: Guillotine bin-packing + MIP cutting-stock pattern selection (OR-Tools). Kazdy vzor je fyzicky realizovatelny gilotinovymi rezy.
- Pri zapnuti "Zakazat otaceni" dilce zustanou vzdy v zadane orientaci (pro material s vlakny/vzorem).
