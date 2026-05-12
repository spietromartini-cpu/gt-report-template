import os
import sys
import re
import io
from datetime import datetime
from pathlib import Path

from flask import Flask, request, send_file, jsonify

try:
    from weasyprint import HTML
except ImportError:
    print("WeasyPrint not installed. Run: pip install weasyprint")
    sys.exit(1)


app = Flask(__name__)

API_KEY = os.environ.get("API_KEY")

class InvestmentReportGenerator:
    """
    Generates institutional PDF investment reports.
    Placeholders use [[key]] syntax — replace from Make.com / Gemini AI data.
    """

    def __init__(self, template_path: str):
        self.template_path = template_path
        self.template_content = self._load_template()
        self.base_url = str(Path(template_path).parent.resolve())

    def _load_template(self) -> str:
        try:
            with open(self.template_path, 'r', encoding='utf-8') as f:
                return f.read()
        except FileNotFoundError:
            raise FileNotFoundError(f"Template not found: {self.template_path}")

    def generate_pdf_bytes(self, data: dict) -> io.BytesIO:
        html_content = self.template_content
        for key, value in data.items():
            placeholder = "[[" + key + "]]"
            html_content = html_content.replace(placeholder, str(value))

        remaining = re.findall(r'\[\[(.*?)\]\]', html_content)
        if remaining:
            print(f"Warning — unreplaced placeholders: {remaining}")

        pdf_buffer = io.BytesIO()
        HTML(string=html_content, base_url=self.base_url).write_pdf(pdf_buffer)
        pdf_buffer.seek(0)
        return pdf_buffer

    @staticmethod
    def compute_derived_fields(data: dict) -> dict:

        # ── Investor profile badge ──────────────────────────────────────────
        profile = data.get("investorProfile", "retail")
        data["investor_profile_class"] = "profile-retail" if profile == "retail" else "profile-inst"
        data["investor_profile_label"] = "Private Investor" if profile == "retail" else "Institutional Investor"

        # ── Strategy label
        # FIX: Only compute if Gemini did NOT already send strategy_label
        if not data.get("strategy_label"):
            strategy_map = {
                "rent":  ("Buy & Rent",  "Cash flow via rental income"),
                "hold":  ("Buy & Hold",  "Long-term capital appreciation"),
                "sell":  ("Buy & Sell",  "Value-add resale strategy"),
                "flip":  ("Fix & Flip",  "Short-term renovation arbitrage"),
            }
            strat = data.get("strategy", "rent")
            data["strategy_label"] = strategy_map.get(strat, ("—", "—"))[0]
            data["strategy_sub"]   = strategy_map.get(strat, ("—", "—"))[1]

        # ── TRI score bar percentage ────────────────────────────────────────
        try:
            tri_raw = float(str(data.get("tri_score", "5")).replace(",", "."))
            data["tri_score_pct"] = round(tri_raw * 10)
        except:
            data["tri_score_pct"] = 50

        # ── GRI bar percentages ─────────────────────────────────────────────
        for gri_key in ["gri_conflict", "gri_fiscal", "gri_exit", "gri_composite"]:
            try:
                val = float(str(data.get(gri_key, "3.8")).replace(",", "."))
                data[gri_key + "_pct"] = round(min(max(val * 10, 5), 100))
            except:
                data[gri_key + "_pct"] = 38

        # ── Veto warning block HTML ─────────────────────────────────────────
        seismic_class   = str(data.get("seismic_veto_class", "ok"))
        court_class     = str(data.get("court_veto_class", "ok"))
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

        # ── Format currency fields ──────────────────────────────────────────
        def fmt_eur(val, key_out):
            try:
                v = float(str(data.get(val, 0)).replace(",", ".").replace("€","").replace(" ",""))
                data[key_out] = f"€ {v:,.0f}".replace(",", ".")
            except:
                data[key_out] = data.get(val, "—")

        fmt_eur("ancillaryCosts", "ancillary_costs")
        fmt_eur("monthlyRent",    "monthly_rent")
        fmt_eur("managementCost", "management_cost")
        # FIX: Only format price/equity from raw fields if Gemini did NOT send them already
        if not data.get("price"):
            fmt_eur("price", "price")
        if not data.get("equity"):
            fmt_eur("equity", "equity")

        # ── Percent fields ──────────────────────────────────────────────────
        def fmt_pct(src, dst, suffix="%"):
            try:
                v = float(str(data.get(src, 0)).replace("%","").replace(",","."))
                data[dst] = f"{v:.1f} {suffix}"
            except:
                data[dst] = data.get(src, "—")

        fmt_pct("interestRate", "interest_rate", "% p.a.")
        fmt_pct("vacancyRate",  "vacancy_rate_asset", "%")

        # ── Pass-through fields ─────────────────────────────────────────────
        passthrough = {
            "city":            "city",
            "country":         "country",
            "district":        "district",
            "address":         "address",
            "assetType":       "asset_type",
            "seismic":         "seismic_class",
            "legal":           "legal_status",
            "taxResidency":    "tax_residency",
            "griConflict":     "gri_conflict",
            "griFiscal":       "gri_fiscal",
            "griExit":         "gri_exit",
            "griComposite":    "gri_composite",
            "grAdjPct":        "gr_adj_pct",
            "name":            "client_name",
            # FIX: New SR-IIT fields — pass through from Gemini output
            "deal_decision_badge": "deal_decision_badge",
            "deal_decision_box":   "deal_decision_box",
            "deal_decision_text":  "deal_decision_text",
            "v_base_str":          "v_base_str",
            "v_intrinsic_str":     "v_intrinsic_str",
            "mao_str":             "mao_str",
            "pi_risk_pct":         "pi_risk_pct",
            "evr_priority_status": "evr_priority_status",
            "brown_discount_impact": "brown_discount_impact",
            "tax_regime_warning":  "tax_regime_warning",
            "tri_veto_logic":      "tri_veto_logic",
            "seismic_veto":        "seismic_veto",
            "seismic_veto_class":  "seismic_veto_class",
            "court_veto":          "court_veto",
            "court_veto_class":    "court_veto_class",
            "ownership_veto":      "ownership_veto",
            "ownership_veto_class":"ownership_veto_class",
            "macro_analysis":      "macro_analysis",
            "ownership_layers":    "ownership_layers",
            "rent_gap_analysis":   "rent_gap_analysis",
            "recommendation":      "recommendation",
            "narrative_text":      "narrative_text",
            "gdp_growth":          "gdp_growth",
            "unemployment":        "unemployment",
            "vacancy_rate":        "vacancy_rate",
            "price_appreciation":  "price_appreciation",
            "return_label":        "return_label",
            "return_value":        "return_value",
            "coc_return":          "coc_return",
            "loan_years_str":      "loan_years_str",
            "monthly_rent_estimate": "monthly_rent_estimate",
        }
        for src, dst in passthrough.items():
            if dst not in data:
                data[dst] = data.get(src, "—")

        # ── Date ────────────────────────────────────────────────────────────
        # FIX: Only set date if Gemini did NOT already send one
        if not data.get("date"):
            data["date"] = datetime.now().strftime("%d. %B %Y")

        # ── Property name ───────────────────────────────────────────────────
        # FIX: Only compute if Gemini did NOT already send property_name
        if not data.get("property_name"):
            city  = data.get("city", "—")
            asset = data.get("asset_type", data.get("assetType", "—"))
            data["property_name"] = f"{city} — {asset}"

        # ── Usable area ─────────────────────────────────────────────────────
        data["usable_area"] = f"{data.get('area', '—')} m²"

        # ── Financial metrics ───────────────────────────────────────────────
        # FIX: Only compute if Gemini did NOT already calculate net_yield
        if not data.get("net_yield") or data.get("net_yield") == "—":
            try:
                raw_price = str(data.get("price", "0")).replace("€","").replace(".","").replace(" ","").replace(",",".")
                price_raw = float(raw_price)
                rent_raw  = float(str(data.get("monthlyRent", "0")).replace(",","."))
                mgmt_raw  = float(str(data.get("managementCost", "0")).replace(",","."))

                net_y = round((rent_raw * 12) / price_raw * 100, 2) if price_raw > 0 else 0
                cap_r = round(((rent_raw - mgmt_raw) * 12) / price_raw * 100, 2) if price_raw > 0 else 0

                data["net_yield"] = f"{net_y:.2f}%"
                data["cap_rate"]  = f"{cap_r:.2f}%"
                data["yield"]     = f"{net_y:.2f}%"
            except:
                data["net_yield"] = "—"
                data["cap_rate"]  = "—"
                data["yield"]     = "—"

        # ── Fallbacks ───────────────────────────────────────────────────────
        data.setdefault("coc_return",     "—")
        data.setdefault("irr_projection", "—")
        data.setdefault("net_yield",      "—")
        data.setdefault("cap_rate",       "—")

        return data


# ── Server setup ─────────────────────────────────────────────────────────────
base_dir      = Path(__file__).parent
template_path = str(base_dir / "investment_report_en.html")

try:
    generator = InvestmentReportGenerator(template_path)
except Exception as e:
    print(f"Critical Error: Could not load template. {e}")
    generator = None


# ── API Endpoints ─────────────────────────────────────────────────────────────

@app.route('/', methods=['GET'])
def health_check():
    return "GT PDF Server is LIVE and ready to accept requests from Make.com!"

@app.route('/generate-pdf', methods=['POST'])
def generate_pdf_endpoint():
    req_key = request.headers.get("x-api-key")
    if API_KEY and req_key != API_KEY:
        return jsonify({"error": "Unauthorized. Invalid or missing API Key."}), 401

    if not generator:
        return jsonify({"error": "Template file missing on server."}), 500

    try:
        raw_data   = request.json or {}
        final_data = InvestmentReportGenerator.compute_derived_fields(raw_data)
        pdf_file   = generator.generate_pdf_bytes(final_data)

        return send_file(
            pdf_file,
            download_name="GT_Investment_Report.pdf",
            mimetype='application/pdf'
        )
    except Exception as e:
        return jsonify({"error": str(e)}), 500


if __name__ == "__main__":
    app.run(host='0.0.0.0', port=5000)
