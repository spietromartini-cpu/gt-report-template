#!/usr/bin/env python3
"""
GT Global Estates & Capital — Investment Report Generator (v2)
Generates institutional-grade PDF investment reports.
Updated to match all form fields from sovereign_build website.

Placeholders map directly to Make.com webhook payload fields.
"""

import os
import sys
import re
from datetime import datetime
from pathlib import Path

try:
    from weasyprint import HTML, CSS
except ImportError:
    print("WeasyPrint not installed. Run: pip install weasyprint")
    sys.exit(1)


class InvestmentReportGenerator:
    """
    Generates institutional PDF investment reports.
    Placeholders use {{key}} syntax — replace from Make.com / Gemini AI data.
    """

    def __init__(self, template_path: str):
        self.template_path = template_path
        self.template_content = self._load_template()

    def _load_template(self) -> str:
        try:
            with open(self.template_path, 'r', encoding='utf-8') as f:
                return f.read()
        except FileNotFoundError:
            raise FileNotFoundError(f"Template not found: {self.template_path}")

    def generate(self, data: dict, output_path: str) -> bool:
        try:
            html_content = self.template_content
            for key, value in data.items():
                placeholder = "{{" + key + "}}"
                html_content = html_content.replace(placeholder, str(value))

            remaining = re.findall(r'\{\{(\w+)\}\}', html_content)
            if remaining:
                print(f"Warning — unreplaced placeholders: {remaining}")

            HTML(string=html_content).write_pdf(output_path)
            print(f"PDF generated: {output_path}")
            return True

        except Exception as e:
            print(f"Error: {str(e)}")
            return False

    @staticmethod
    def compute_derived_fields(data: dict) -> dict:
        """
        Computes display/derived fields from raw webhook payload.
        Call this before generate() to enrich the data dict.
        """
        # Investor profile badge
        profile = data.get("investorProfile", "retail")
        data["investor_profile_class"] = "profile-retail" if profile == "retail" else "profile-inst"
        data["investor_profile_label"] = "Private Investor" if profile == "retail" else "Institutional Investor"

        # Strategy label
        strategy_map = {
            "rent":  ("Buy & Rent", "Cash flow via rental income"),
            "hold":  ("Buy & Hold", "Long-term capital appreciation"),
            "sell":  ("Buy & Sell", "Value-add resale strategy"),
            "flip":  ("Fix & Flip", "Short-term renovation arbitrage"),
        }
        strat = data.get("strategy", "rent")
        data["strategy_label"] = strategy_map.get(strat, ("—", "—"))[0]
        data["strategy_sub"]   = strategy_map.get(strat, ("—", "—"))[1]

        # TRI score bar percentage (score is 0-10, bar is 0-100)
        try:
            tri_raw = float(str(data.get("tri_score", "5")).replace(",", "."))
            data["tri_score_pct"] = round(tri_raw * 10)
        except:
            data["tri_score_pct"] = 50

        # GRI bar percentages (scores are 1-10, map to 10-100%)
        for gri_key in ["gri_conflict", "gri_fiscal", "gri_exit", "gri_composite"]:
            try:
                val = float(str(data.get(gri_key, "3.8")).replace(",", "."))
                data[gri_key + "_pct"] = round(min(max(val * 10, 5), 100))
            except:
                data[gri_key + "_pct"] = 38

        # Veto warning block HTML — shown only if seismic or legal veto triggered
        seismic_class = str(data.get("seismic_veto_class", "ok"))
        court_class   = str(data.get("court_veto_class", "ok"))
        ownership_class = str(data.get("ownership_veto_class", "ok"))

        if "alert" in (seismic_class, court_class, ownership_class):
            data["veto_warning_block"] = """
<div style="background:rgba(183,21,21,.1);border:1px solid rgba(248,113,113,.4);
     border-left:3px solid #f87171;padding:16px 20px;margin-bottom:20px;">
  <div style="font-family:'Montserrat',sans-serif;font-size:6.5pt;font-weight:700;
       letter-spacing:.2em;text-transform:uppercase;color:#f87171;margin-bottom:8px;">
    ⛔ &nbsp; Critical Asset Warning
  </div>
  <div style="font-family:'Montserrat',sans-serif;font-size:7.5pt;font-weight:300;
       color:rgba(248,180,150,.9);line-height:1.7;">
    Initial due diligence reveals structural or juridical parameters that constitute
    binary knockout criteria. Review all veto flags before proceeding with capital allocation.
  </div>
</div>"""
        else:
            data["veto_warning_block"] = ""

        # Format currency fields for display
        def fmt_eur(val, key_out):
            try:
                v = float(str(data.get(val, 0)).replace(",", ".").replace("€","").replace(" ",""))
                data[key_out] = f"€ {v:,.0f}".replace(",", ".")
            except:
                data[key_out] = data.get(val, "—")

        fmt_eur("price",              "price")
        fmt_eur("equity",             "equity")
        fmt_eur("ancillaryCosts",     "ancillary_costs")
        fmt_eur("monthlyRent",        "monthly_rent")
        fmt_eur("managementCost",     "management_cost")

        # Percent fields
        def fmt_pct(src, dst, suffix="%"):
            try:
                v = float(str(data.get(src, 0)).replace("%","").replace(",","."))
                data[dst] = f"{v:.1f} {suffix}"
            except:
                data[dst] = data.get(src, "—")

        fmt_pct("interestRate",    "interest_rate", "% p.a.")
        fmt_pct("vacancyRate",     "vacancy_rate_asset", "%")

        # Pass-through fields with fallbacks
        passthrough = {
            "city":           "city",
            "country":        "country",
            "district":       "district",
            "address":        "address",
            "assetType":      "asset_type",
            "seismic":        "seismic_class",
            "legal":          "legal_status",
            "taxResidency":   "tax_residency",
            "griConflict":    "gri_conflict",
            "griFiscal":      "gri_fiscal",
            "griExit":        "gri_exit",
            "griComposite":   "gri_composite",
            "grAdjPct":       "gr_adj_pct",
            "name":           "client_name",
        }
        for src, dst in passthrough.items():
            if dst not in data:
                data[dst] = data.get(src, "—")

        return data

    @staticmethod
    def get_sample_data() -> dict:
        """Sample data for testing — mirrors website form fields."""
        today = datetime.now().strftime("%d.%m.%Y")
        return {
            # ── From website form ──────────────────────────────────
            "name":               "Pietro Martini",
            "email":              "pietro@gtglobal.com",
            "investorProfile":    "institutional",
            "strategy":           "rent",
            "city":               "Bucharest",
            "country":            "Romania",
            "district":           "Floreasca",
            "address":            "Calea Floreasca 88",
            "assetType":          "Historic Residential — Art Nouveau Villa",
            "price":              465000,
            "area":               280,
            "monthlyRent":        2650,
            "interestRate":       4.2,
            "loanYears":          20,
            "equity":             120000,
            "ancillaryCosts":     32000,
            "vacancyRate":        5,
            "managementCost":     220,
            "maintenanceReserve": 150,
            "taxResidency":       "Romania (Non-Resident)",
            "seismic":            "RS III",
            "legal":              "Active Restitution Proceeding",
            # GRI
            "griConflict":        3.2,
            "griFiscal":          4.1,
            "griExit":            3.5,
            "griComposite":       3.6,
            "grAdjPct":           9.0,

            # ── Computed / AI-generated ────────────────────────────
            "date":               today,
            "property_name":      "Bucharest — Floreasca Art Nouveau Villa",
            "segment":            "Luxury Real Estate",
            "tri_score":          "5.8",
            "tri_score_pct":      58,

            # Veto checks
            "seismic_veto_class":      "ok",
            "seismic_veto":            "✓ PASS",
            "court_veto_class":        "alert",
            "court_veto":              "⚠ ACTIVE",
            "registration_veto_class": "ok",
            "registration_veto":       "✓ PASS",
            "ownership_veto_class":    "alert",
            "ownership_veto":          "⚠ UNRESOLVED",

            # Market & Analysis
            "macro_analysis":     "Romania's economy demonstrates robust growth with average GDP expansion of 4.2% over five years. EU membership since 2007 provides legal certainty and capital flow freedoms. Bucharest continues to attract institutional investors as an emerging technology hub.",
            "ownership_layers":   "The Art Nouveau villa in Floreasca carries ownership complexity rooted in 1944. A restitution proceeding initiated in 2003 remains active and directly affects title. This constitutes a binary veto criterion for retail investors but a manageable risk for institutional capital.",
            "gdp_growth":         "+4.2% p.a.",
            "unemployment":       "5.1%",
            "vacancy_rate":       "3.8%",
            "price_appreciation": "+8.5% p.a.",
            "rent_gap_analysis":  "The rent-gap between current market price (€465,000) and potential ARV post title normalization (€650,000) represents €185,000 in unrealized value — realizable exclusively by institutional investors with 10+ year horizons.",

            # SR-IIT
            "tri_veto_logic":     "The TRI-Veto System distinguishes gradual risks (incorporated into TRI calculation) from binary risks (which halt the formula entirely). The active restitution proceeding is a binary knockout for retail investors. For institutional investors, it is priced into the Juridical Premium (JP) component of the MAO calculation.",
            "sr_iit_formula":     "V* = V_phys + V_econ + V_soz(s) + IA − TRI_adj − GR_adj. V_phys derives from Vitruvius' Triad (Firmness, Utility, Beauty); V_econ from discounted cash flow analysis; V_soz from spatial theory and cultural capital; IA from information asymmetry premiums; TRI_adj from title risks; GR_adj from geopolitical considerations.",
            "tr_retail":          "25%",
            "tr_institutional":   "15%",
            "mao_retail":         "€ 220,000",
            "mao_institutional":  "€ 380,000",

            # Financial
            "usable_area":        "280 m²",
            "year_built":         "1912",
            "energy_rating":      "D",
            "condition":          "Requires Renovation",
            "net_yield":          "4.2%",
            "cap_rate":           "5.1%",
            "coc_return":         "18.5%",
            "irr_projection":     "12.3%",
            "junkspace_analysis": "This Art Nouveau villa embodies the antithesis of Junkspace — it carries the Rossi Density of Memory intrinsic to its neighborhood. Unlike peripheral new construction in Cluj, whose value drivers are market momentum and thus vulnerable during corrections, this villa is substance-anchored.",
            "recommendation":     "For Institutional Investors: CONDITIONAL BUY. The rent-gap is realizable; the IRR projection is compelling. Recommended MAO: €380,000. For Retail Investors: VETO. The active restitution proceeding is a hard knockout criterion. The asking price of €465,000 represents a 111% premium over the appropriate MAO of €220,000.",

            # Narrative (generated by Gemini AI)
            "narrative_text":     "Als Gründer von GT Global Estates betrachte ich das Asset in Floreasca durch die Linse purer Marktrealität. Die chirurgische Dekonstruktion der Fundamentaldaten liefert ein messerscharfes Bild: Mit einem TRI-Score von 5.8 positioniert sich das Objekt in einer sehr spezifischen Marktdynamik. Für institutionelle Portfolios gilt absolute Pragmatik — die Korrelation aus Ticketgröße und TRI-Rating verlangt kompromisslose Cashflow-Modellierung. Emotionen verbrennen Rendite. Daten generieren sie.\n\nFazit: Die Metriken sprechen für sich. Die Entscheidung ist nun eine Frage der disziplinierten Kapitalallokation.",
        }


def main():
    base_dir = Path(__file__).parent
    template_path = str(base_dir / "investment_report_en.html")
    output_path   = str(base_dir / "Investment_Report_Sample.pdf")

    generator = InvestmentReportGenerator(template_path)

    raw_data   = generator.get_sample_data()
    final_data = InvestmentReportGenerator.compute_derived_fields(raw_data)

    success = generator.generate(final_data, output_path)

    if success:
        size_kb = os.path.getsize(output_path) / 1024
        print(f"Report created: {output_path}  ({size_kb:.1f} KB)")
    else:
        print("PDF generation failed.")
        sys.exit(1)


if __name__ == "__main__":
    main()
