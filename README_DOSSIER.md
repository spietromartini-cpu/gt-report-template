## Design (27.11.2026)
Helles Druckdesign in Navy (#1B2A44) und Gold (#B7954E): weiße Seiten, feiner Navy-Rahmen und schmales
Navy-Band oben auf jeder Seite, goldene Rubriken, druckbare Ampelfarben (Grün #15803D, Bernstein #B45309,
Rot #B91C1C). Erzeugt mit `tools/report_light.py` aus dem Website-Repo. Inhalte unverändert.

# GRID-Dossier — PDF-Dienst (Render)

GRID = Geospatial Risk & Investment Decisioning. GRID Engine = Software, GRID Value Model = Bewertungsmethode, Entscheidungslogik TRI / VETO / MAO.

| Datei | Zweck |
|---|---|
| `dossier_grid.py` | Route `POST /generate-dossier`, Aufbereitung der Engine-Zahlen und Gemini-Texte |
| `dossier_grid.html` | Dossier-Vorlage (8–9 Seiten), Design aus `investment_report_en.html`, Farbwelt Bordeaux |
| `app.py` | registriert die Route; `/generate-pdf` (Bericht v2.1) unverändert, nur Namen GRID |
| `investment_report_en.html` | Bericht v2.1: Namen GRID, Farben Bordeaux |

`investment_report_en1.html` wird von keinem Endpunkt verwendet und bleibt unverändert.

## Einbau
1. Die vier Dateien oben plus diese README hochladen: https://github.com/spietromartini-cpu/gt-report-template/upload/main → Commit.
2. Render → Service `gt-report-template` → Environment → `DOSSIER_API_KEY` anlegen (langer Zufallswert). Derselbe Wert kommt in Make in das Modul „Schlüssel“ (Feld `dossierKey`).
3. Render deployt nach dem Commit automatisch (sonst: Manual Deploy → Deploy latest commit).
4. Prüfen: `GET /` zeigt „GT PDF Server - GRID Engine - LIVE“. `POST /generate-dossier` ohne Schlüssel → 401, ohne gesetzte Variable → 503.

## Schnittstelle
`POST /generate-dossier`, Header `x-api-key: <DOSSIER_API_KEY>`
- `multipart/form-data` mit den Feldern `engine` (= `renderEngineJson` aus `/api/engine` mode „dossier“) und `texts` (= Antworttext von Gemini), **oder**
- JSON `{"engine": …, "texts": …}` (Objekt oder JSON-Text).

Zahlen kommen nur aus `engine.resultJson`. Texte werden als reiner Text behandelt (kein HTML aus der KI), in Absätze geteilt und an Satzgrenzen gekürzt. Antwort: PDF. Fehler: 401, 422, 500, 503.

## Stand 23.11.2026 (GRID v3.3)
Automatische Standortprüfungen zeigen zusätzlich „Natura 2000 (EEA)“ und „Seismic ag / TC (P100-1/2013, indicative)“, sofern die Engine diese Daten liefert; die Quellenliste kennt `osm_local`, `eea_natura2000` und `p100_seismic`. Ältere Engine-Antworten ohne diese Felder werden unverändert dargestellt.
