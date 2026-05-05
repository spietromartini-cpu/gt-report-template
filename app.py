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

# Der API-Key Türsteher (Render holt sich den Key aus den Environment Variables)
API_KEY = os.environ.get("API_KEY") 

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

    # NEU: Diese Methode druckt das PDF direkt in den Arbeitsspeicher für Make.com
    def generate_pdf_bytes(self, data: dict) -> io.BytesIO:
        html_content = self.template_content
        for key, value in data.items():
            placeholder = "{{" + key + "}}"
            html_content = html_content.replace(placeholder, str(value))

        remaining = re.findall(r'\{\{(\w+)\}\}', html_content)
        if remaining:
            print(f"Warning — unreplaced placeholders: {remaining}")

        pdf_buffer = io.BytesIO()
        HTML(string=html_content).write_pdf(pdf_buffer)
        pdf_buffer.seek(0)
        return pdf_buffer

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


# --- SERVER SETUP ---
base_dir = Path(__file__).parent
template_path = str(base_dir / "investment_report_en.html")

try:
    generator = InvestmentReportGenerator(template_path)
except Exception as e:
    print(f"Critical Error: Could not load template. {e}")
    generator = None

# --- API ENDPOINTS ---

# 1. Health-Check (Damit du im Browser sehen kannst, ob der Server läuft)
@app.route('/', methods=['GET'])
def health_check():
    return "GT PDF Server is LIVE and ready to accept requests from Make.com!"

# 2. Die Haupt-URL für Make.com
@app.route('/generate-pdf', methods=['POST'])
def generate_pdf_endpoint():
    # Security: Überprüfe den API Key
    req_key = request.headers.get("x-api-key")
    if API_KEY and req_key != API_KEY:
        return jsonify({"error": "Unauthorized. Invalid or missing API Key."}), 401

    if not generator:
        return jsonify({"error": "Template file missing on server."}), 500

    try:
        # Daten von Make.com empfangen
        raw_data = request.json or {}
        
        # Deine Berechnungen durchführen
        final_data = InvestmentReportGenerator.compute_derived_fields(raw_data)
        
        # PDF generieren
        pdf_file = generator.generate_pdf_bytes(final_data)
        
        # PDF direkt als Datei-Download zurückschicken
        return send_file(
            pdf_file,
            download_name="GT_Investment_Report.pdf",
            mimetype='application/pdf'
        )
    except Exception as e:
        return jsonify({"error": str(e)}), 500


if __name__ == "__main__":
    app.run(host='0.0.0.0', port=5000)
