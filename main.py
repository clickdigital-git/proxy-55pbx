from flask import Flask, request, jsonify
from datetime import date, timedelta
import requests
import os
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

app = Flask(__name__)

@app.after_request
def add_cors(response):
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type"
    response.headers["Access-Control-Allow-Methods"] = "GET, OPTIONS"
    return response

API_KEY = os.environ.get("PBX_API_KEY", "4f3b7c18-0a03-4ebe-98c6-9f204a9bcdcb-202651218369")
PBX_BASE = "https://reportapi01.55pbx.com:50500"

FILAS = {
    "betel": "65241af636cb95f687fb1b06",
    "nf":    "65f8adde8f420605ae9b2ac1"
}

def buscar_pbx(fila_id, dt, tipo):
    url = (
        f"{PBX_BASE}/api/pbx/reports/metrics"
        f"/{dt}T00:00:00.000-03:00"
        f"/{dt}T23:59:59.999-03:00"
        f"/{fila_id}/all_numbers/all_agent/{tipo}/undefined/undefined/-3"
    )
    r = requests.get(url, headers={"key": API_KEY}, timeout=180, verify=False)
    r.raise_for_status()
    return r.json()

def to_sec(t):
    if not t: return 0
    parts = str(t).split(":")
    try:
        return int(parts[0])*3600 + int(parts[1])*60 + int(parts[2])
    except:
        return 0

def sec_to_hms(sec):
    sec = int(sec)
    h = sec // 3600
    m = (sec % 3600) // 60
    s = sec % 60
    return f"{h:02d}:{m:02d}:{s:02d}"

def resumir_r02(data):
    calls = data.get("data_report02", [])
    atendidas   = [c for c in calls if c.get("type_call") == "call_attended"]
    abandonadas = [c for c in calls if c.get("type_call") == "call_abandoned"]
    n = len(atendidas)
    total_falado = sum(to_sec(c.get("call_time_spoken","0:0:0")) for c in atendidas)
    total_espera = sum(to_sec(c.get("call_time_waiting","0:0:0")) for c in atendidas)
    por_agente = {}
    for c in atendidas:
        nome = c.get("name","")
        if not nome: continue
        if nome not in por_agente:
            por_agente[nome] = {"ligacoes": 0, "falado_sec": 0}
        por_agente[nome]["ligacoes"]   += 1
        por_agente[nome]["falado_sec"] += to_sec(c.get("call_time_spoken","0:0:0"))
    return {
        "total": len(calls),
        "atendidas": n,
        "abandonadas": len(abandonadas),
        "taxa_atendimento": f"{round(n/len(calls)*100,1)}%" if calls else "0%",
        "tempo_medio_espera_sec": round(total_espera/n) if n else 0,
        "tempo_total_falado_sec": total_falado,
        "por_agente": {
            k: {"ligacoes": v["ligacoes"], "falado_min": round(v["falado_sec"]/60,1)}
            for k, v in sorted(por_agente.items(), key=lambda x: -x[1]["ligacoes"])
        }
    }

def resumir_r03(data):
    resultado = {}
    for a in data.get("data_report03", []):
        nome = a.get("name","")
        if not nome: continue
        resultado[nome] = {
            "logado":        a.get("timeTotalLogged",""),
            "falado":        a.get("timeTotalSpoken",""),
            "falado_sec":    to_sec(a.get("timeTotalSpoken","0:0:0")),
            "pausa":         a.get("timeTotalPaused",""),
            "ocioso":        a.get("timeIdle",""),
            "produtividade": a.get("productivity",""),
            "ligacoes":      a.get("attendedReceptive", 0),
            "recusas":       a.get("quantityRefusal", 0)
        }
    return resultado

@app.route("/api/resumo", methods=["GET", "OPTIONS"])
def resumo():
    if request.method == "OPTIONS":
        return jsonify({}), 200

    dt_inicio = request.args.get("dt_inicio", "")
    dt_fim    = request.args.get("dt_fim", "")
    filas_req = request.args.get("filas", "betel,nf")

    if not dt_inicio or not dt_fim:
        return jsonify({"erro": "Informe dt_inicio e dt_fim (YYYY-MM-DD)"}), 400
    try:
        d_ini = date.fromisoformat(dt_inicio)
        d_fim = date.fromisoformat(dt_fim)
    except:
        return jsonify({"erro": "Formato invalido. Use YYYY-MM-DD"}), 400
    if (d_fim - d_ini).days > 31:
        return jsonify({"erro": "Periodo maximo: 31 dias por consulta"}), 400

    filas_sel = [f.strip() for f in filas_req.split(",") if f.strip() in FILAS]
    if not filas_sel:
        return jsonify({"erro": "Filas invalidas. Use: betel, nf"}), 400

    resultado = {"periodo": {"inicio": dt_inicio, "fim": dt_fim}, "filas": {}}

    for fila_nome in filas_sel:
        fila_id  = FILAS[fila_nome]
        dias_r02 = []
        agentes_r03 = {}
        erros = []

        d = d_ini
        while d <= d_fim:
            ds = d.isoformat()
            try:
                raw02 = buscar_pbx(fila_id, ds, "report_02")
                r = resumir_r02(raw02)
                r["data"] = ds
                dias_r02.append(r)
            except Exception as e:
                erros.append({"data": ds, "tipo": "report_02", "erro": str(e)})

            try:
                raw03 = buscar_pbx(fila_id, ds, "report_03")
                for agente, dados in resumir_r03(raw03).items():
                    if agente not in agentes_r03:
                        agentes_r03[agente] = {
                            "ligacoes_total": 0,
                            "falado_sec_total": 0,
                            "recusas_total": 0,
                            "dias_com_atividade": 0,
                            "historico": []
                        }
                    agentes_r03[agente]["ligacoes_total"]    += dados["ligacoes"]
                    agentes_r03[agente]["falado_sec_total"]  += dados["falado_sec"]
                    agentes_r03[agente]["recusas_total"]     += dados["recusas"]
                    if dados["ligacoes"] > 0 or dados["falado_sec"] > 0:
                        agentes_r03[agente]["dias_com_atividade"] += 1
                    agentes_r03[agente]["historico"].append({
                        "data":          ds,
                        "ligacoes":      dados["ligacoes"],
                        "falado":        dados["falado"],
                        "produtividade": dados["produtividade"],
                        "ocioso":        dados["ocioso"],
                        "pausa":         dados["pausa"]
                    })
            except Exception as e:
                erros.append({"data": ds, "tipo": "report_03", "erro": str(e)})

            d += timedelta(days=1)

        total_calls      = sum(d["total"]                for d in dias_r02)
        total_atend      = sum(d["atendidas"]            for d in dias_r02)
        total_aband      = sum(d["abandonadas"]          for d in dias_r02)
        total_falado_sec = sum(d["tempo_total_falado_sec"] for d in dias_r02)

        agentes_ligacoes = {}
        for dia in dias_r02:
            for agente, v in dia.get("por_agente", {}).items():
                if agente not in agentes_ligacoes:
                    agentes_ligacoes[agente] = {"ligacoes": 0, "falado_min": 0.0}
                agentes_ligacoes[agente]["ligacoes"]   += v["ligacoes"]
                agentes_ligacoes[agente]["falado_min"] += v["falado_min"]

        produtividade_cons = {}
        for agente, v in agentes_r03.items():
            dias_ativos = v["dias_com_atividade"] or 1
            produtividade_cons[agente] = {
                "ligacoes_total":   v["ligacoes_total"],
                "falado_total":     sec_to_hms(v["falado_sec_total"]),
                "media_ligacoes_dia": round(v["ligacoes_total"] / dias_ativos, 1),
                "recusas_total":    v["recusas_total"],
                "historico_diario": v["historico"]
            }

        resultado["filas"][fila_nome] = {
            "resumo_geral": {
                "total_ligacoes":   total_calls,
                "atendidas":        total_atend,
                "abandonadas":      total_aband,
                "taxa_atendimento": f"{round(total_atend/total_calls*100,1)}%" if total_calls else "0%",
                "total_falado":     sec_to_hms(total_falado_sec)
            },
            "por_dia": dias_r02,
            "ranking_ligacoes":      dict(sorted(agentes_ligacoes.items(),    key=lambda x: -x[1]["ligacoes"])),
            "produtividade_agentes": dict(sorted(produtividade_cons.items(),  key=lambda x: -x[1]["ligacoes_total"])),
            "erros": erros
        }

    return jsonify(resultado), 200

@app.route("/api/relatorio", methods=["GET", "OPTIONS"])
def relatorio():
    if request.method == "OPTIONS":
        return jsonify({}), 200
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
        resp = requests.get(url, headers={"key": API_KEY}, timeout=180, verify=False)
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
