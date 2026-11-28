"""
GT Global Estates — GRID Dossier (Commercial / Industrial & Logistics)
GRID = Geospatial Risk & Investment Decisioning · GRID Engine (Software) · GRID Value Model (Bewertungsmethode)
Entscheidungslogik: TRI (Titelrisiko-Index), VETO (Veto-Prüfung), MAO (maximales Gebot).

POST /generate-dossier  (Header x-api-key = DOSSIER_API_KEY, Pflicht)
Body: multipart/form-data mit den Feldern engine (= renderEngineJson aus /api/engine mode "dossier")
      und texts (= Antworttext von Gemini), oder JSON {"engine": …, "texts": …} (Objekt oder JSON-Text).

- Zahlen kommen ausschließlich aus engine.resultJson (deterministische GRID Engine).
- Beschriftungen der Prüfpunkte (engine.labels) und Kundenangaben (engine.answers) kommen von der Engine.
- Texte kommen aus Gemini; sie werden als reiner Text behandelt (Jinja2-Autoescape),
  in Absätze geteilt und an Satzgrenzen gekürzt, damit nichts über den Seitenrand läuft.
"""
import hmac
import io
import json
import os
import re
from datetime import datetime, timezone

from flask import jsonify, request, send_file
from jinja2 import Environment, FileSystemLoader, select_autoescape

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
TEMPLATE_NAME = "dossier_grid.html"

_env = Environment(
    loader=FileSystemLoader(BASE_DIR),
    autoescape=select_autoescape(["html"]),
    trim_blocks=True,
    lstrip_blocks=True,
)

# Maximale Zeichen je Textfeld (passend zum Seitenlayout, mit Reserve)
TEXT_LIMITS = {
    "macro_analysis": 1650,
    "spatial_analysis": 1250,
    "income_analysis": 1150,   # v3.4
    "asset_quality": 1050,     # v3.4
    "ownership_layers": 1500,
    "value_gap_analysis": 1100,
    "risk_commentary": 1150,
    "recommendation": 900,
    "narrative": 1000,  # je Absatz
}

VERDICT = {
    "GO": ("PROCEED", "#15803D", "30pt"),
    "CAUTION": ("CAUTION", "#B45309", "30pt"),
    "NO_GO_PRICE": ("NOT AT THIS PRICE", "#B91C1C", "22pt"),
    "VETO": ("VETO", "#B91C1C", "38pt"),
    "INCOMPLETE": ("DATA INCOMPLETE", "#B45309", "22pt"),
}

ASSET = {"COM": "Commercial", "IND": "Industrial & Logistics"}
SIGNAL = {"ABOVE_MARKET": ("ABOVE MARKET", "#B91C1C"), "IN_LINE": ("IN LINE WITH MARKET", "#B45309"), "BELOW_MARKET": ("BELOW MARKET", "#15803D")}
STRATEGY = {"core": "Core", "core_plus": "Core+", "value_add": "Value-add", "opportunistic": "Opportunistic"}
PROFILE = {"private": "Private / Family Office", "institutional": "Institutional", "retail": "Retail"}
THEORY = {
    "COM": ("Location Economics", "Location economics &middot; Alonso / Glaeser / Sassen"),
    "IND": ("Location and Flows", "Location and flows &middot; Weber / Castells / Rodrigue"),
}
SCORE_PARTS = {
    "COM": [("transit", "Public transport (1 km)", 30), ("metro", "Metro distance", 15), ("certification", "ESG certification", 15),
            ("energy", "Energy class", 15), ("covenant", "Tenant covenant", 15), ("concentration", "Tenant concentration", 10)],
    "IND": [("route", "Truck route to motorway", 25), ("clearHeight", "Clear height", 15), ("floorLoad", "Floor load", 10),
            ("docks", "Docks per 1,000 m²", 10), ("yard", "Yard depth", 10), ("power", "Power capacity", 10),
            ("esfr", "ESFR sprinklers", 8), ("rail", "Rail siding (bonus)", 7), ("expansion", "Expansion land (bonus)", 5)],
}
TRI_LABELS = [("H", "Ownership history density"), ("J", "Legal friction"), ("K", "Cadastre quality"),
              ("D", "Technical documentation"), ("SRI", "Seismic residual risk")]
CODE_LABELS = {
    "PROFILE_NOT_ELIGIBLE": "Profile not eligible for this asset class",
    "DUAL_OWNERSHIP": "Conflicting ownership titles",
    "SEISMIC_RS1": "Seismic class RS I",
    "SEISMIC_RS2_COMMERCIAL": "Seismic class RS II (commercial)",
    "LEGAL_PROCEEDINGS": "Restitution / supreme court proceedings",
    "ISU_MISSING": "Fire safety permit (ISU) missing",
    "PUZ_INCOMPATIBLE": "Zoning (PUZ) incompatible",
    "HGV_ACCESS_RESTRICTED": "Access below 40 t",
    "HGV_CLEARANCE_TOO_LOW": "Clearance below 4.20 m",
    "RIGHT_OF_WAY_MISSING": "No registered right of way",
    "TRI_ABOVE_PROFILE_LIMIT": "Title risk above profile limit",
    "ISU_UNKNOWN": "Fire safety permit status unknown",
    "PUZ_UNKNOWN": "Zoning status unknown",
    "HGV_ACCESS_UNKNOWN": "Heavy-goods access unknown",
    "RIGHT_OF_WAY_UNKNOWN": "Right of way unknown",
    "SEISMIC_UNKNOWN": "Seismic classification unknown",
    "EVR_PRIORITY_HIGH": "Pre-1978 building without seismic classification: structural audit priority",
    "MEPS_EXPOSURE_NO_CAPEX": "Energy class F/G without retrofit budget (EU minimum standards)",
    "PARAMS_CALIBRATED_FOR_RO_ONLY": "Parameters calibrated for Romania only",
    "HGV_RESTRICTION_NEARBY": "Possible weight/height restriction on roads near the site (OpenStreetMap): verify truck route",
    "ROUTE_NOT_VERIFIED": "Truck route to motorway not verified (straight-line distance only)",
    "DATA_SOURCE_UNAVAILABLE": "An external data source did not respond; affected values were not estimated",
    "PASSING_RENT_ABOVE_PRIME": "Passing rent above the prime market rent in broker data",
    "ERV_CAPPED_AT_PRIME_RENT": "Market rent capped at the prime rent in broker data",
    "YIELD_REGIONAL_UNCALIBRATED": "Yields calibrated for Bucharest offices only; result capped at Caution",
    "YIELD_SEGMENT_UNCALIBRATED": "Yields calibrated for offices; result capped at Caution",
    "BUILDING_TAX_NOT_PROVIDED": "Local building tax not provided; not included in costs",
    "DOCKS_UNKNOWN": "Number of docks", "YARD_UNKNOWN": "Yard depth", "POWER_UNKNOWN": "Power capacity",
    "ROUTE_UNKNOWN": "Truck route to motorway", "CLEAR_HEIGHT_UNKNOWN": "Clear height", "FLOOR_LOAD_UNKNOWN": "Floor load",
    "TRANSIT_UNKNOWN": "Public transport count", "METRO_DISTANCE_UNKNOWN": "Metro distance",
    "ENERGY_CLASS_UNKNOWN": "Energy class", "COVENANT_UNKNOWN": "Tenant covenant",
}
GATE_CHECKS = [  # (Label, Veto-/Prüf-Codes, Lücken-Codes, nur für Assetklasse)
    ("Ownership title", ["DUAL_OWNERSHIP"], None, None),
    ("Court / restitution", ["LEGAL_PROCEEDINGS"], None, None),
    ("Land book & third-party rights", ["NOT_IN_LAND_BOOK", "THIRD_PARTY_RIGHTS"], ["LEGAL_UNKNOWN"], None),
    ("Building permit & works", ["PERMIT_MISSING", "UNPERMITTED_WORKS"], ["PERMIT_UNKNOWN"], None),
    ("Seismic class", ["SEISMIC_RS1", "SEISMIC_RS2_COMMERCIAL"], ["SEISMIC_UNKNOWN"], None),
    ("Fire safety permit (ISU)", ["ISU_MISSING"], ["ISU_UNKNOWN"], None),
    ("Zoning (PUZ)", ["PUZ_INCOMPATIBLE"], ["PUZ_UNKNOWN", "PUZ_PENDING"], None),
    ("Agricultural land pre-emption", ["AGRI_LAND_PREEMPTION"], None, None),
    ("Contamination", ["CONTAMINATION_REMEDIATION"], None, None),
    ("Heavy-goods access", ["HGV_ACCESS_RESTRICTED", "HGV_CLEARANCE_TOO_LOW", "BRIDGE_RESTRICTION"], ["HGV_ACCESS_UNKNOWN"], "IND"),
    ("Right of way", ["RIGHT_OF_WAY_MISSING"], ["RIGHT_OF_WAY_UNKNOWN"], "IND"),
    ("Title risk vs. profile limit", ["TRI_ABOVE_PROFILE_LIMIT"], None, None),
    ("Investor profile", ["PROFILE_NOT_ELIGIBLE"], None, None),
]
CONTEXT_LABELS = [("siteHistory", "Site history"), ("previousUse", "Previous use"), ("ownershipHistory", "Ownership history"),
                  ("surroundings", "Surroundings"), ("tenantProfile", "Tenant profile"), ("notes", "Notes")]


# ── Formatierung ──────────────────────────────────────────────────────────────
def _date(iso):
    """ISO-Zeitstempel → '22 November 2026' (ohne Angabe: heute, UTC)."""
    try:
        d = datetime.fromisoformat(str(iso).replace("Z", "+00:00")) if iso else datetime.now(timezone.utc)
    except ValueError:
        d = datetime.now(timezone.utc)
    return f"{d.day} {d.strftime('%B %Y')}"


def eur(n):
    if n is None:
        return "—"
    return "EUR " + f"{round(n):,}".replace(",", ".")


def num(n):
    return "—" if n is None else f"{round(n):,}".replace(",", ".")


def pct(x, dp=2):
    return "—" if x is None else f"{x * 100:.{dp}f}%"


def clip(text, limit):
    """Kürzt an der letzten Satzgrenze vor dem Limit (nie mitten im Wort)."""
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    cut = text[:limit]
    m = max(cut.rfind(". "), cut.rfind("! "), cut.rfind("? "))
    if m > limit * 0.6:
        return cut[: m + 1]
    return cut[: cut.rfind(" ")].rstrip(",;:") + " …"


def paragraphs(value, limit):
    """Gemini-Text → Liste sauberer Absätze. HTML-Tags aus Altbeständen (<br>) werden zu Absätzen."""
    if isinstance(value, list):
        parts = [str(v) for v in value]
    else:
        txt = str(value or "")
        txt = re.sub(r"<br\s*/?>\s*<br\s*/?>", "\n\n", txt, flags=re.I)
        txt = re.sub(r"<[^>]+>", "", txt)  # keine HTML-Tags aus KI-Text übernehmen
        parts = re.split(r"\n\s*\n", txt)
    out = [clip(p.replace("\n", " "), limit) for p in parts if p and p.strip()]
    return out or ["(Text not available.)"]


# ── Kontext für die Vorlage ───────────────────────────────────────────────────
def build_context(payload: dict) -> dict:
    engine = payload.get("engine") or {}
    if isinstance(engine, str):
        engine = json.loads(engine)
    if not engine.get("ok"):
        raise ValueError("engine.ok must be true")
    result = engine.get("resultJson")
    result = json.loads(result) if isinstance(result, str) else result
    if not isinstance(result, dict) or not result.get("ok"):
        raise ValueError("engine.resultJson missing or invalid")

    texts = payload.get("texts") or {}
    if isinstance(texts, str):
        texts = json.loads(texts)

    inp = result["input"]
    ac = inp["assetClass"]
    v = result.get("valuation")
    gate = result["gate"]
    contact = engine.get("contact") or {}
    city = inp["location"].get("city") or ""
    country = inp["location"].get("country") or ""

    label, color, size = VERDICT.get(result["verdict"], ((engine.get("verdictLabel") or result["verdict"]).upper(), "#8A6A2E", "30pt"))
    eng_labels = engine.get("labels") or {}

    def code_label(code):
        return eng_labels.get(code) or CODE_LABELS.get(code) or code

    # Score
    parts = result["score"]["parts"]
    maxp = result["score"].get("max") or {}
    score_rows = []
    for key, lbl, mx in SCORE_PARTS[ac]:
        mx = maxp.get(key, mx)
        pts = parts.get(key, 0) or 0
        score_rows.append({"label": lbl, "points": f"{pts:g}", "max": mx, "pct": max(0, min(100, round(pts / mx * 100)))})

    # TRI
    tri = result.get("tri") or {}
    comps = tri.get("components") or {}
    weights = tri.get("weights") or {}
    tri_rows = [{"label": lbl, "score": f"{comps.get(k, 0):.1f}", "weight": f"{weights.get(k, 0):.3f}"} for k, lbl in TRI_LABELS]
    tri_limit = "5.5 (institutional)" if inp["profile"] == "institutional" else "3.5 (private)"

    # Veto-Gate
    code_of = lambda x: x if isinstance(x, str) else x.get("code")
    trig = {code_of(t) for t in gate.get("triggers", [])}
    rev = {code_of(t) for t in gate.get("reviews", [])}
    gaps_all = {code_of(d) for d in gate.get("dataGaps", [])}
    gaps_crit = {code_of(d) for d in gate.get("dataGaps", []) if isinstance(d, dict) and d.get("critical")}
    gate_rows = []
    for lbl, codes, gap_codes, only in GATE_CHECKS:
        if only and only != ac:
            continue
        gap_codes = gap_codes or []
        if trig.intersection(codes):
            st, cls = "VETO", "badge-alert"
        elif rev.intersection(codes) or gaps_crit.intersection(gap_codes):
            st, cls = ("REVIEW", "badge-warn") if rev.intersection(codes) else ("UNKNOWN", "badge-warn")
        elif gaps_all.intersection(gap_codes):
            st, cls = "NOT PROVIDED", "badge-muted"
        else:
            st, cls = "CLEAR", "badge-ok"
        gate_rows.append({"label": lbl, "status": st, "cls": cls})
    flags = [code_label(code_of(f)) for f in gate.get("flags", [])]
    gaps = [code_label(code_of(d)) for d in gate.get("dataGaps", [])]
    answers = [a for a in (engine.get("answers") or []) if isinstance(a, dict) and a.get("question")]

    def per(x):
        return "—" if x is None else f"EUR {x:.2f} / m² / month"
    lease_rows, asset_rows = [], []
    if v:
        rent = v.get("rent") or {}
        lease_rows = [("Passing rent", per(rent.get("passingPm2"))),
                      ("Market rent used (ERV)", per(rent.get("ervUsedPm2"))),
                      ("Prime rent benchmark", per(rent.get("primeRentPm2")) + " (broker research)" if rent.get("primeRentPm2") else "no benchmark for this segment"),
                      ("Occupancy", pct(inp["occupancy"], 0)),
                      ("Remaining term to first break", f"{inp.get('waultYears', 0):g} years"),
                      ("Indexation (p.a.)", pct(inp.get("indexation"), 1)),
                      ("Renewal probability (model)", pct(v.get("renewalProb"), 0))]
        if ac == "COM":
            com = inp.get("com") or {}
            lease_rows += [("Tenant covenant", com.get("tenantCovenant") or "not provided"),
                           ("Largest tenant share", pct(com.get("largestTenantShare"), 0) if com.get("largestTenantShare") is not None else "not provided")]
    unit = lambda x, u: "not provided" if x is None else f"{x:g}{u}"
    yn = lambda b: "not provided" if b is None else ("yes" if b else "no")
    risk = inp.get("risk") or {}
    asset_rows = [("Construction year", str(risk.get("constructionYear") or "not provided")), ("Seismic class", str(risk.get("seismic") or "unknown").upper())]
    if ac == "IND":
        i = inp.get("ind") or {}
        asset_rows += [("Clear height", unit(i.get("clearHeightM"), " m")), ("Floor load", unit(i.get("floorLoadTm2"), " t/m²")),
                       ("Docks per 1,000 m²", unit(i.get("docksPer1000"), "")), ("Yard depth", unit(i.get("yardDepthM"), " m")),
                       ("Power capacity", unit(i.get("powerMW"), " MW")), ("ESFR sprinklers", yn(i.get("esfr"))),
                       ("Rail siding", yn(i.get("railSiding"))), ("Expansion land", yn(i.get("expansionLand")))]
    else:
        c = inp.get("com") or {}
        asset_rows += [("Certification", (c.get("certification") or "not provided").replace("_", " ")), ("Energy class", c.get("energyClass") or "not provided"),
                       ("Green capex budget", eur(inp.get("costs", {}).get("greenCapex")) if (inp.get("costs", {}).get("greenCapex") or 0) > 0 else "none stated")]

    ctx = {
        "lease_rows": [{"label": a, "value": b} for a, b in lease_rows],
        "asset_rows": [{"label": a, "value": b} for a, b in asset_rows],
        "evidence": engine.get("marketEvidence") or [],
        "title": f"{ASSET[ac]} · {city}" if city else ASSET[ac],
        "asset_label": ASSET[ac],
        "client_name": contact.get("name") or "Client",
        "date": _date(result.get("dossierDate")),
        "analysis_date": _date(result.get("analysisDate")),
        "ref": (engine.get("analysisId") or "")[:8].upper(),
        "location": ", ".join(x for x in [city, country] if x),
        "nla": num(inp["nla"]),
        "occupancy": pct(inp["occupancy"], 0),
        "profile_label": PROFILE.get(inp["profile"], inp["profile"]),
        "profile_short": "institutional" if inp["profile"] == "institutional" else "private",
        "verdict": {"label": label, "color": color, "size": size},
        "theory_title": THEORY[ac][0],
        "theory_label": THEORY[ac][1].replace("&middot;", "·"),
        "score": {"value": f"{result['score']['value']:g}", "rows": score_rows,
                  "unknown": result["score"].get("unknownPoints") or 0},
        "tri": {"value": f"{tri.get('value', 0):.2f}", "rows": tri_rows, "limit": tri_limit},
        "gate_rows": gate_rows,
        "flags": flags,
        "gaps": gaps,
        "context_rows": [{"label": lbl, "value": inp.get("context", {}).get(k)} for k, lbl in CONTEXT_LABELS if inp.get("context", {}).get(k)],
        "assumptions": result.get("assumptionsUsed", []),
        "engine_version": result.get("engine", ""),
        "answers": answers[:30],
        "answers_more": max(0, len(answers) - 30),
        "params_version": result.get("paramsVersion", ""),
        "t": {k: paragraphs(texts.get(k), TEXT_LIMITS[k]) for k in TEXT_LIMITS},
    }

    # Automatische Standortdaten und Datenquellen
    enr = result.get("enrichment") or {}
    macro = (enr.get("macro") or {}) if enr.get("status") == "done" else {}
    ctx["macro_rows"] = [{"label": lbl, "value": f"{macro[k]['value']:g}{unit} ({macro[k]['year']})"}
                         for k, lbl, unit in [("gdpGrowth", "GDP growth", "%"), ("unemployment", "Unemployment", "%"), ("lpiInfrastructure", "LPI infrastructure (1–5)", "")]
                         if k in macro]
    ctx["pv_row"] = f"{enr['pv']['kWhPerKWpYear']:,} kWh/kWp·a".replace(",", ".") if enr.get("pv") else None
    loc = enr.get("location") or {}
    prov = inp.get("provenance") or {}
    auto = []
    if enr.get("status") == "done":
        if ac == "IND":
            j = loc.get("nearestJunction")
            auto.append(("Nearest motorway junction", f"{j['straightLineKm']} km straight-line" + (f" (exit {j['ref']})" if j and j.get("ref") else "") if j else "not determined"))
            rk = (enr.get("routing") or {}).get("hgvRouteKm")
            auto.append(("HGV route to motorway", f"{rk} km" if rk is not None else "not verified"))
            if "railSidingsWithin1km" in loc: auto.append(("Rail tracks (siding/spur/yard) ≤ 1 km", str(loc["railSidingsWithin1km"])))
            if "substationsWithin5km" in loc: auto.append(("Substations ≤ 5 km", str(loc["substationsWithin5km"])))
            rs = loc.get("accessRestrictionsWithin300m") or []
            if loc: auto.append(("Road restrictions ≤ 300 m", "; ".join(f"{r['type']} {r['value']}" for r in rs[:3]) if rs else "none mapped"))
        else:
            if "transitStopsWithin1km" in loc: auto.append(("Bus/tram stops ≤ 1 km", str(loc["transitStopsWithin1km"])))
            if loc: auto.append(("Nearest metro", f"{loc['nearestMetroM']} m" if loc.get("nearestMetroM") is not None else "none within 2 km"))
    if enr.get("status") == "done":
        # GRID v3.3: Hinweise ohne Einfluss auf das Ergebnis (Natura 2000, Erdbeben nach P100-1/2013)
        n2k = enr.get("protectedAreas")
        if n2k is not None:
            sites = lambda arr: "; ".join(f"{x['code']} {x['name']}" for x in arr[:2]) + (f" (+{len(arr) - 2})" if len(arr) > 2 else "")
            if n2k.get("inside"):
                auto.append(("Natura 2000 (EEA)", "inside: " + sites(n2k["inside"])))
            elif n2k.get("nearby"):
                auto.append(("Natura 2000 (EEA)", f"within {n2k.get('nearbyWithinKm', 2):g} km: " + sites(n2k["nearby"])))
            else:
                auto.append(("Natura 2000 (EEA)", f"none within {n2k.get('nearbyWithinKm', 2):g} km"))
        sz = enr.get("seismic")
        if sz:
            fx = lambda x, u: f"{x:.2f}" if u == "g" else f"{x:.1f}"
            rng = lambda r, u: "–" if not r else (f"{fx(r[0], u)} {u}" if r[0] == r[1] else f"{fx(r[0], u)}–{fx(r[1], u)} {u}")
            near = sz.get("nearest") or {}
            auto.append((f"Seismic ag / TC ({sz.get('code', 'P100-1')}, indicative)",
                         f"{rng(sz.get('agRangeG'), 'g')} / {rng(sz.get('tcRangeS'), 's')} · nearest listed town {near.get('town', '?')} ({near.get('km', '?')} km)"))
    ctx["auto_rows"] = [{"label": a, "value": b} for a, b in auto]
    SRC = {"osm_overpass": "OpenStreetMap (Overpass)", "osm_local": "OpenStreetMap (GRID extract, monthly)",
           "openrouteservice": "openrouteservice (HGV routing)", "eu_jrc_pvgis": "EU JRC PVGIS", "world_bank": "World Bank Open Data",
           "eea_natura2000": "EEA Natura 2000 sites", "p100_seismic": "Seismic design code P100-1/2013, Table A1"}
    STATUS = {"ok": "ok", "partial": "partial", "not_configured": "not configured", "not_applicable": "n/a",
              "unavailable": "no response", "error": "error", "skipped": "skipped", "no_route": "no route",
              "out_of_range": "no listed town nearby", "not_available": "not available"}
    ctx["source_rows"] = [{"label": SRC.get(k, k), "status": STATUS.get(v.get("status"), v.get("status"))} for k, v in (enr.get("sources") or {}).items()]
    ctx["retrieved_at"] = (enr.get("fetchedAt") or "")[:16].replace("T", " ") + (" UTC" if enr.get("fetchedAt") else "")
    ctx["provenance_count"] = len(prov)

    if v:
        y = v["yields"]
        mc = v.get("marketCheck") or {}
        sig = SIGNAL.get(mc.get("signal"), ("—", "#6E7788"))
        rng = v.get("marketValueRange") or {}
        strat = v.get("strategy") or {}
        ctx["signal"] = {"label": sig[0], "color": sig[1], "ratio": f"{mc['priceToMarket']:.2f}" if mc.get("priceToMarket") is not None else "—",
                         "text": engine.get("marketSignalLabel") or ""}
        ctx["k"] = {
            "asking": eur(inp["price"]), "asking_m2": eur(v["pricePerM2"]),
            "market_value": eur(v["vMarket"]), "has_comp": v.get("vComp") is not None,
            "range": f"{eur(rng.get('low'))} – {eur(rng.get('high'))}" if rng else "—",
            "strategy": STRATEGY.get(strat.get("key"), strat.get("key") or "—") + ("" if strat.get("fromClient") else " (default)"),
            "mao": eur(v["mao"]), "mao_m2": eur(v["maoPerM2"]),
            "niy": pct(v["netInitialYield"]), "irr": pct(v["irrRiskAdj"]), "target_irr": pct(y["targetIRR"]),
        }
        pr = y.get("primeRange")
        ctx["y"] = {"prime": pct(y["prime"]), "adjusted": pct(y["adjusted"]), "exit": pct(y["exit"]),
                    "discount": pct(y["discountMarket"]), "target": pct(y["targetIRR"]),
                    "prime_range": f"{pct(pr[0])} – {pct(pr[1])}" if pr and pr[0] != pr[1] else None,
                    "source": "Prime yield from broker research (CBRE, Cushman & Wakefield); adjusted for the location score. The discount rate is calibrated to the market yield."}
        ctx["cf"] = {"rows": [{"year": f"{r['year']} ({r['calendarYear']})", "rent": num(r["rent"]), "noi": num(r["noi"]),
                               "leasing": num(r["leasing"]), "cf": num(r["cf"])} for r in v["cashflows"]],
                     "exit": num(v["exitValue"])}
        ctx["val"] = {"income": eur(v["vIncome"]), "comp": eur(v.get("vComp")), "market": eur(v["vMarket"]), "jp": eur(v["jp"]),
                      "mao": eur(v["mao"]), "ratio": f"{v['priceToMao']:.2f}" if v.get("priceToMao") else "—",
                      "irr_cash": pct(v["irrCash"], 1), "irr_risk": pct(v["irrRiskAdj"], 1),
                      "range": f"{eur(rng.get('low'))} – {eur(rng.get('high'))}" if rng else "—",
                      "band": f"±{round((rng.get('yieldBand') or 0) * 10000)} bp",
                      "price_market": f"{mc['priceToMarket']:.2f}" if mc.get("priceToMarket") is not None else "—",
                      "strategy": f"{STRATEGY.get(strat.get('key'), strat.get('key'))} (+{pct(strat.get('targetPremium'), 1)})" if strat else "—",
                      "jp_basis": f"TRI above {v.get('triBaseline')} (excess {v.get('triExcess')})",
                      "calibrated": "yes" if (v.get("yields") or {}).get("calibrated") else "no — result capped at Caution",
                      "gri_shift": f"{round(v.get('griExitYieldShiftDownside', 0) * 10000)} bp"}
        s = v["scenarios"]
        ctx["scen"] = [{"name": n, "mao": eur(s[k]["mao"]), "irr": pct(s[k]["irrRiskAdj"]), "exit": eur(s[k]["exitValue"])}
                       for k, n in [("base", "Base"), ("downside", "Downside"), ("upside", "Upside")]]
    else:
        na = "—"
        ctx["k"] = {"asking": eur(inp["price"]), "asking_m2": na, "market_value": na, "has_comp": False, "mao": "VETO",
                    "mao_m2": na, "niy": na, "irr": na, "target_irr": na, "range": na, "strategy": na}
        ctx["signal"] = None
        ctx["y"] = {"prime": na, "adjusted": na, "exit": na, "discount": na, "target": na, "source": "", "prime_range": None}
        ctx["cf"] = None
        ctx["val"] = {}
        ctx["scen"] = []
    return ctx


def _texts_from_string(s: str) -> dict:
    """Gemini-Antwort als Text → Objekt. Entfernt versehentliche Code-Zäune (```json … ```)."""
    s = s.strip()
    s = re.sub(r"^```(?:json)?\s*|\s*```$", "", s)
    return json.loads(s)


def render_html(payload: dict) -> str:
    return _env.get_template(TEMPLATE_NAME).render(**build_context(payload))


def render_pdf(payload: dict) -> bytes:
    from weasyprint import HTML
    return HTML(string=render_html(payload), base_url=BASE_DIR).write_pdf()


# ── Flask-Route ───────────────────────────────────────────────────────────────
def register(app):
    @app.route("/generate-dossier", methods=["POST"])
    def generate_dossier():
        expected = os.environ.get("DOSSIER_API_KEY")
        if not expected:
            return jsonify({"error": "DOSSIER_API_KEY not configured on server"}), 503
        given = request.headers.get("x-api-key") or ""
        if not hmac.compare_digest(given.encode(), expected.encode()):
            return jsonify({"error": "Unauthorized"}), 401
        try:
            if request.form.get("engine"):
                # Make: multipart/form-data mit den Feldern engine und texts (reiner Text, kein Escaping nötig)
                payload = {"engine": request.form.get("engine"), "texts": request.form.get("texts", "")}
            else:
                payload = request.get_json(force=True, silent=False) or {}
            if isinstance(payload.get("engine"), str):
                payload["engine"] = json.loads(payload["engine"])
            if isinstance(payload.get("texts"), str):
                payload["texts"] = _texts_from_string(payload["texts"])
            pdf = render_pdf(payload)
        except (ValueError, KeyError, TypeError, json.JSONDecodeError) as e:
            return jsonify({"error": f"Invalid payload: {e}"}), 422
        except Exception as e:  # noqa: BLE001
            return jsonify({"error": f"Render failed: {e}"}), 500
        eng = payload.get("engine") or {}
        eng = json.loads(eng) if isinstance(eng, str) else eng
        ref = str(eng.get("analysisId", "dossier"))[:8].upper()
        ac = ((eng.get("resultJson") or {}).get("input") or {}).get("assetClass", "") if isinstance(eng.get("resultJson"), dict) else ""
        return send_file(io.BytesIO(pdf), download_name=f"GT_GRID_Dossier_{ac + '_' if ac else ''}{ref}.pdf", mimetype="application/pdf")
