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
    GT Global Estates — SR-IIT v2.1 PDF Generator
    Placeholders: [[key]] syntax. Replaced from Gemini AI / Make.com data.
    """

    def __init__(self, template_path: str):
        self.template_path    = template_path
        self.template_content = self._load_template()
        self.base_url         = str(Path(template_path).parent.resolve())

    def _load_template(self) -> str:
        try:
            with open(self.template_path, 'r', encoding='utf-8') as f:
                return f.read()
        except FileNotFoundError:
            raise FileNotFoundError(f"Template not found: {self.template_path}")

    def generate_pdf_bytes(self, data: dict) -> io.BytesIO:
        html_content = self.template_content

        for key, value in data.items():
            placeholder  = "[[" + key + "]]"
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

        # ── 1. Investor profile ─────────────────────────────────────────────
        profile = data.get("investorProfile", "retail")
        data["investor_profile_class"] = "profile-retail" if profile == "retail" else "profile-inst"
        data["investor_profile_label"] = "Private Investor" if profile == "retail" else "Institutional Investor"

        # ── 2. Strategy label — always force English formatted label ────────
        raw_strategy  = str(data.get("strategy", "rent")).lower().strip()
        current_label = str(data.get("strategy_label", "")).strip()
        raw_values    = {"rent", "hold", "sell", "flip", ""}

        if not current_label or current_label.lower() in raw_values:
            strategy_map = {
                "rent": "Buy & Rent",
                "hold": "Buy & Hold",
                "sell": "Buy & Sell",
                "flip": "Fix & Flip",
            }
            data["strategy_label"] = strategy_map.get(raw_strategy, "Buy & Rent")
            data["strategy_sub"] = {
                "rent": "Cash flow via rental income",
                "hold": "Long-term capital appreciation",
                "sell": "Value-add resale strategy",
                "flip": "Short-term renovation arbitrage",
            }.get(raw_strategy, "—")

        # ── 3. Country — always force English ───────────────────────────────
        country_map = {
            "rumänien":    "Romania",
            "rumaenien":   "Romania",
            "romania":     "Romania",
            "deutschland": "Germany",
            "germany":     "Germany",
            "österreich":  "Austria",
            "austria":     "Austria",
            "ungarn":      "Hungary",
            "hungary":     "Hungary",
            "frankreich":  "France",
            "france":      "France",
            "italien":     "Italy",
            "italy":       "Italy",
            "spanien":     "Spain",
            "spain":       "Spain",
            "schweiz":     "Switzerland",
            "switzerland": "Switzerland",
        }
        raw_country     = str(data.get("country", "Romania")).strip()
        data["country"] = country_map.get(raw_country.lower(), raw_country)

        # ── 4. TRI score bar ────────────────────────────────────────────────
        try:
            tri_raw = float(str(data.get("tri_score", "5")).replace(",", "."))
            data["tri_score_pct"] = round(tri_raw * 10)
        except Exception:
            data["tri_score_pct"] = 50

        # ── 5. GRI bar percentages ──────────────────────────────────────────
        for gri_key in ["gri_conflict", "gri_fiscal", "gri_exit", "gri_composite"]:
            try:
                val = float(str(data.get(gri_key, "3.8")).replace(",", "."))
                data[gri_key + "_pct"] = round(min(max(val * 10, 5), 100))
            except Exception:
                data[gri_key + "_pct"] = 38

        # ── 6. Veto warning block HTML ──────────────────────────────────────
        seismic_class   = str(data.get("seismic_veto_class",   "ok"))
        court_class     = str(data.get("court_veto_class",     "ok"))
        ownership_class = str(data.get("ownership_veto_class", "ok"))

        if "alert" in (seismic_class, court_class, ownership_class):
            data["veto_warning_block"] = (
                '<div style="background:rgba(183,21,21,.1);border:1px solid rgba(248,113,113,.4);'
                'border-left:3px solid #f87171;padding:16px 20px;margin-bottom:20px;">'
                '<div style="font-family:\'Montserrat\',sans-serif;font-size:6.5pt;font-weight:700;'
                'letter-spacing:.2em;text-transform:uppercase;color:#f87171;margin-bottom:8px;">'
                '&#9940;&nbsp; Critical Asset Warning</div>'
                '<div style="font-family:\'Montserrat\',sans-serif;font-size:7.5pt;font-weight:300;'
                'color:rgba(248,180,150,.9);line-height:1.7;">'
                'Initial due diligence reveals structural or juridical parameters that constitute '
                'binary knockout criteria. Review all veto flags before proceeding with capital allocation.'
                '</div></div>'
            )
        else:
            data["veto_warning_block"] = ""

        # ── 7. Currency formatting — ALWAYS reformat regardless of input ────
        def fmt_eur(src_key, dst_key):
            try:
                raw = str(data.get(src_key, "0"))
                # Strip all non-numeric except dot and comma
                raw = raw.replace("EUR", "").replace("€", "").replace(" ", "")
                # Handle dot-as-thousands-separator: 876.576 → 876576
                # Rule: if dot appears and digits after last dot = 3 → thousands sep
                parts = raw.split(".")
                if len(parts) > 1 and len(parts[-1]) == 3:
                    raw = raw.replace(".", "")
                elif len(parts) > 2:
                    # Multiple dots — all are thousands separators
                    raw = raw.replace(".", "")
                raw = raw.replace(",", ".")
                v = float(raw) if raw else 0
                data[dst_key] = "EUR {:,.0f}".format(v).replace(",", ".")
            except Exception:
                data[dst_key] = data.get(src_key, "—")

        # Always reformat these — no conditions
        fmt_eur("price",          "price")
        fmt_eur("equity",         "equity")
        fmt_eur("ancillaryCosts", "ancillary_costs")
        fmt_eur("monthlyRent",    "monthly_rent")
        fmt_eur("managementCost", "management_cost")

        # ── 8. Percent fields ───────────────────────────────────────────────
        def fmt_pct(src, dst, suffix="%"):
            try:
                v = float(str(data.get(src, 0)).replace("%", "").replace(",", ".").strip())
                data[dst] = f"{v:.1f} {suffix}"
            except Exception:
                data[dst] = data.get(src, "—")

        fmt_pct("interestRate", "interest_rate", "% p.a.")
        fmt_pct("vacancyRate",  "vacancy_rate_asset", "%")

        # ── 9. Decision box — inline border color (WeasyPrint CSS fix) ──────
        box = data.get("deal_decision_box", "proceed-box")
        color_map = {
            "proceed-box": "rgba(74,222,128,0.6)",
            "caution-box": "rgba(251,191,36,0.6)",
            "veto-box":    "rgba(248,113,113,0.6)",
        }
        data["decision_border_color"] = color_map.get(box, "rgba(183,121,31,0.4)")

        # ── 10. Pass-through — all Gemini SR-IIT output fields ──────────────
        passthrough = {
            "city":                  "city",
            "country":               "country",
            "district":              "district",
            "address":               "address",
            "assetType":             "asset_type",
            "name":                  "client_name",
            "seismic":               "seismic_class",
            "legal":                 "legal_status",
            "taxResidency":          "tax_residency",
            "griConflict":           "gri_conflict",
            "griFiscal":             "gri_fiscal",
            "griExit":               "gri_exit",
            "griComposite":          "gri_composite",
            "grAdjPct":              "gr_adj_pct",
            "deal_decision_badge":   "deal_decision_badge",
            "deal_decision_box":     "deal_decision_box",
            "deal_decision_text":    "deal_decision_text",
            "v_base_str":            "v_base_str",
            "v_intrinsic_str":       "v_intrinsic_str",
            "mao_str":               "mao_str",
            "pi_risk_pct":           "pi_risk_pct",
            "seismic_veto":          "seismic_veto",
            "seismic_veto_class":    "seismic_veto_class",
            "court_veto":            "court_veto",
            "court_veto_class":      "court_veto_class",
            "ownership_veto":        "ownership_veto",
            "ownership_veto_class":  "ownership_veto_class",
            "tri_veto_logic":        "tri_veto_logic",
            "evr_priority_status":   "evr_priority_status",
            "brown_discount_impact": "brown_discount_impact",
            "tax_regime_warning":    "tax_regime_warning",
            "macro_analysis":        "macro_analysis",
            "ownership_layers":      "ownership_layers",
            "rent_gap_analysis":     "rent_gap_analysis",
            "recommendation":        "recommendation",
            "narrative_text":        "narrative_text",
            "gdp_growth":            "gdp_growth",
            "unemployment":          "unemployment",
            "vacancy_rate":          "vacancy_rate",
            "price_appreciation":    "price_appreciation",
            "return_label":          "return_label",
            "return_value":          "return_value",
            "net_yield":             "net_yield",
            "coc_return":            "coc_return",
            "loan_years_str":        "loan_years_str",
            "monthly_rent_estimate": "monthly_rent_estimate",
        }
        for src, dst in passthrough.items():
            if dst not in data or not data.get(dst):
                val = data.get(src, "")
                data[dst] = val if val else "—"

        # ── 11. Date — ALWAYS set from Python, never trust Gemini ───────────
        data["date"] = datetime.now().strftime("%-d. %B %Y")

        # ── 12. Property name ────────────────────────────────────────────────
        pn = str(data.get("property_name", "")).strip()
        if not pn or pn == "—":
            city = data.get("city", "—")
            data["property_name"] = f"GT Investment Asset - {city}"

        # ── 13. Usable area ──────────────────────────────────────────────────
        area = data.get("area", "")
        data["usable_area"] = f"{area} m\u00b2" if area else "—"

        # ── 14. Net yield fallback ───────────────────────────────────────────
        ny = str(data.get("net_yield", "")).strip()
        if not ny or ny == "—":
            try:
                raw_p = str(data.get("price", "0"))
                raw_p = re.sub(r'[^\d]', '', raw_p.split(".")[0])
                price_f = float(raw_p) if raw_p else 0
                rent_f  = float(str(data.get("monthlyRent", "0")).replace(",", "."))
                mgmt_f  = float(str(data.get("managementCost", "0")).replace(",", "."))
                if price_f > 0:
                    net_y = round((rent_f * 12) / price_f * 100, 2)
                    cap_r = round(((rent_f - mgmt_f) * 12) / price_f * 100, 2)
                    data["net_yield"] = f"{net_y:.2f}%"
                    data["cap_rate"]  = f"{cap_r:.2f}%"
                    data["yield"]     = f"{net_y:.2f}%"
            except Exception:
                pass

        # ── 15. Final fallbacks ──────────────────────────────────────────────
        defaults = {
            "net_yield":             "—",
            "cap_rate":              "—",
            "coc_return":            "—",
            "irr_projection":        "—",
            "monthly_rent_estimate": "—",
            "loan_years_str":        "—",
            "deal_decision_text":    "PROCEED",
            "deal_decision_badge":   "badge-ok",
            "deal_decision_box":     "proceed-box",
            "decision_border_color": "rgba(74,222,128,0.6)",
            "v_base_str":            "—",
            "v_intrinsic_str":       "—",
            "mao_str":               "—",
            "pi_risk_pct":           "—",
            "evr_priority_status":   "—",
            "brown_discount_impact": "—",
            "tax_regime_warning":    "—",
            "seismic_veto":          "CLEAR",
            "seismic_veto_class":    "ok",
            "court_veto":            "CLEAR",
            "court_veto_class":      "ok",
            "ownership_veto":        "CLEAR",
            "ownership_veto_class":  "ok",
            "tri_veto_logic":        "—",
            "macro_analysis":        "—",
            "ownership_layers":      "—",
            "rent_gap_analysis":     "—",
            "recommendation":        "—",
            "narrative_text":        "—",
            "gdp_growth":            "1.8%",
            "unemployment":          "5.4%",
            "vacancy_rate":          "—",
            "price_appreciation":    "—",
            "return_label":          "GROSS YIELD",
            "return_value":          "—",
        }
        for key, fallback in defaults.items():
            data.setdefault(key, fallback)

        return data


# ── Server setup ──────────────────────────────────────────────────────────────
base_dir      = Path(__file__).parent
template_path = str(base_dir / "investment_report_en.html")

try:
    generator = InvestmentReportGenerator(template_path)
except Exception as e:
    print(f"Critical Error: Could not load template. {e}")
    generator = None


# ── API endpoints ─────────────────────────────────────────────────────────────

@app.route('/', methods=['GET'])
def health_check():
    return "GT PDF Server - SR-IIT v2.1 - LIVE"


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
            mimetype="application/pdf"
        )
    except Exception as e:
        return jsonify({"error": str(e)}), 500


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
