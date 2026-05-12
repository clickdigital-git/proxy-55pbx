from flask import Flask, request, jsonify
import requests
import os

app = Flask(__name__)

API_KEY = os.environ.get("PBX_API_KEY", "4f3b7c18-0a03-4ebe-98c6-9f204a9bcdcb-202651218369")
PBX_BASE = "https://reportapi01.55pbx.com:50500"

@app.route("/api/relatorio", methods=["GET"])
def relatorio():
    dt_inicio = request.args.get("dt_inicio", "")
    dt_fim    = request.args.get("dt_fim", "")
    fila      = request.args.get("fila", "all_queues")
    agente    = request.args.get("agente", "all_agent")
    tipo      = request.args.get("tipo", "report_02")

    if not dt_inicio or not dt_fim:
        return jsonify({"erro": "Informe dt_inicio e dt_fim"}), 400

    url = (
        f"{PBX_BASE}/api/pbx/reports/metrics"
        f"/{dt_inicio}T00:00:00.000-03:00"
        f"/{dt_fim}T23:59:59.999-03:00"
        f"/{fila}/all_numbers/{agente}/{tipo}/undefined/undefined/-3"
    )

    try:
        resp = requests.get(url, headers={"key": API_KEY}, timeout=120, verify=False)
        resp.raise_for_status()
        return jsonify(resp.json()), 200
    except requests.exceptions.RequestException as e:
        return jsonify({"erro": str(e)}), 502

@app.route("/health")
def health():
    return jsonify({"status": "ok"})

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)
