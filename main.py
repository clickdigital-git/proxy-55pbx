from flask import Flask, request, jsonify
from datetime import date, timedelta
import requests
import os
import uuid
import threading
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

app = Flask(__name__)

API_KEY = os.environ.get("PBX_API_KEY", "4f3b7c18-0a03-4ebe-98c6-9f204a9bcdcb-202651218369")
PBX_BASE = "https://reportapi01.55pbx.com:50500"

FILAS = {
    "betel": "65241af636cb95f687fb1b06",
    "nf":    "65f8adde8f420605ae9b2ac1"
}

JOBS = {}

@app.after_request
def add_cors(response):
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type"
    response.headers["Access-Control-Allow-Methods"] = "GET, OPTIONS"
    return response

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

def calcular_nps(promotores, neutros, detratores):
    total = promotores + neutros + detratores
    if total == 0:
        return None
    return round(((promotores - detratores) / total) * 100, 1)

def extrair_nota(c):
    try:
        n = int(c.get("wh_question_3_2_NOTA_1_a_5"))
        if 1 <= n <= 5:
            return n
    except (TypeError, ValueError):
        pass
    return None

def classificar_nota(n):
    if n == 5:
        return "promotor"
    elif n in (3, 4):
        return "neutro"
    elif n in (1, 2):
        return "detrator"
    return None

def resumir_r02_com_nps(calls):
    atendidas   = [c for c in calls if c.get("type_call") == "call_attended"]
    abandonadas = [c for c in calls if c.get("type_call") == "call_abandoned"]
    n = len(atendidas)
    total_falado = sum(to_sec(c.get("call_time_spoken","0:0:0")) for c in atendidas)
    total_espera = sum(to_sec(c.get("call_time_waiting","0:0:0")) for c in atendidas)

    por_agente = {}
    p_fila = n_fila = d_fila = 0

    for c in atendidas:
        nome = c.get("name","")
        if not nome:
            continue
        if nome not in por_agente:
            por_agente[nome] = {"ligacoes": 0, "falado_sec": 0, "p": 0, "n": 0, "d": 0}
        por_agente[nome]["ligacoes"]   += 1
        por_agente[nome]["falado_sec"] += to_sec(c.get("call_time_spoken","0:0:0"))
        nota = extrair_nota(c)
        if nota:
            cls = classificar_nota(nota)
            if cls == "promotor":
                por_agente[nome]["p"] += 1
                p_fila += 1
            elif cls == "neutro":
                por_agente[nome]["n"] += 1
                n_fila += 1
            elif cls == "detrator":
                por_agente[nome]["d"] += 1
                d_fila += 1

    agentes_resumo = {}
    for k, v in sorted(por_agente.items(), key=lambda x: -x[1]["ligacoes"]):
        agentes_resumo[k] = {
            "ligacoes":   v["ligacoes"],
            "falado_min": round(v["falado_sec"]/60, 1),
            "nps":        calcular_nps(v["p"], v["n"], v["d"]),
            "promotores": v["p"],
            "neutros":    v["n"],
            "detratores": v["d"],
            "respostas":  v["p"] + v["n"] + v["d"]
        }

    return {
        "total":                  len(calls),
        "atendidas":              n,
        "abandonadas":            len(abandonadas),
        "taxa_atendimento":       f"{round(n/len(calls)*100,1)}%" if calls else "0%",
        "tempo_medio_espera_sec": round(total_espera/n) if n else 0,
        "tempo_total_falado_sec": total_falado,
        "nps_fila":               calcular_nps(p_fila, n_fila, d_fila),
        "nps_respostas":          p_fila + n_fila + d_fila,
        "por_agente":             agentes_resumo
    }

def resumir_r03(data):
    resultado = {}
    for a in data.get("data_report03", []):
        nome = a.get("name","")
        if not nome:
            continue
        resultado[nome] = {
            "falado_sec":    to_sec(a.get("timeTotalSpoken","0:0:0")),
            "ocioso_sec":    to_sec(a.get("timeIdle","0:0:0")),
            "pausa":         a.get("timeTotalPaused",""),
            "produtividade": a.get("productivity",""),
            "ligacoes":      a.get("attendedReceptive", 0),
            "recusas":       a.get("quantityRefusal", 0)
        }
    return resultado

def processar_job(job_id, dt_inicio, dt_fim, filas_sel):
    try:
        JOBS[job_id]["status"] = "processando"
        d_ini = date.fromisoformat(dt_inicio)
        d_fim = date.fromisoformat(dt_fim)
        resultado = {"periodo": {"inicio": dt_inicio, "fim": dt_fim}, "filas": {}}

        for fila_nome in filas_sel:
            fila_id  = FILAS[fila_nome]
            dias_r02 = []
            agentes  = {}
            erros    = []

            d = d_ini
            while d <= d_fim:
                ds = d.isoformat()

                # report_02
                try:
                    raw02 = buscar_pbx(fila_id, ds, "report_02")
                    calls = raw02.get("data_report02", [])
                    r = resumir_r02_com_nps(calls)
                    r["data"] = ds
                    dias_r02.append(r)
                except Exception as e:
                    erros.append({"data": ds, "tipo": "report_02", "erro": str(e)})

                # report_03
                try:
                    raw03 = buscar_pbx(fila_id, ds, "report_03")
                    for agente, dados in resumir_r03(raw03).items():
                        if agente not in agentes:
                            agentes[agente] = {
                                "ligacoes_total":   0,
                                "falado_sec_total": 0,
                                "ocioso_sec_total": 0,
                                "recusas_total":    0,
                                "dias_ativos":      0,
                                "p_total": 0, "n_total": 0, "d_total": 0,
                                "historico": []
                            }
                        agentes[agente]["ligacoes_total"]   += dados["ligacoes"]
                        agentes[agente]["falado_sec_total"] += dados["falado_sec"]
                        agentes[agente]["ocioso_sec_total"] += dados["ocioso_sec"]
                        agentes[agente]["recusas_total"]    += dados["recusas"]
                        if dados["ligacoes"] > 0 or dados["falado_sec"] > 0:
                            agentes[agente]["dias_ativos"] += 1
                        agentes[agente]["historico"].append({
                            "data":          ds,
                            "ligacoes":      dados["ligacoes"],
                            "falado":        sec_to_hms(dados["falado_sec"]),
                            "ocioso":        sec_to_hms(dados["ocioso_sec"]),
                            "produtividade": dados["produtividade"],
                            "pausa":         dados["pausa"],
                            "recusas":       dados["recusas"]
                        })
                except Exception as e:
                    erros.append({"data": ds, "tipo": "report_03", "erro": str(e)})

                d += timedelta(days=1)

            # Acumula NPS por agente a partir dos dias
            for dia in dias_r02:
                for ag, v in dia.get("por_agente", {}).items():
                    if ag in agentes:
                        agentes[ag]["p_total"] += v.get("promotores", 0)
                        agentes[ag]["n_total"] += v.get("neutros", 0)
                        agentes[ag]["d_total"] += v.get("detratores", 0)

            # Totais gerais
            total_calls      = sum(d["total"]                  for d in dias_r02)
            total_atend      = sum(d["atendidas"]              for d in dias_r02)
            total_aband      = sum(d["abandonadas"]            for d in dias_r02)
            total_falado_sec = sum(d["tempo_total_falado_sec"] for d in dias_r02)
            total_p = sum(d.get("nps_respostas", 0) and 0 or 0 for d in dias_r02)

            # NPS geral do período
            gp = sum(agentes[a]["p_total"] for a in agentes)
            gn = sum(agentes[a]["n_total"] for a in agentes)
            gd = sum(agentes[a]["d_total"] for a in agentes)

            # Ranking final de agentes
            ranking = {}
            for ag, v in sorted(agentes.items(), key=lambda x: -x[1]["ligacoes_total"]):
                dias_ativos = v["dias_ativos"] or 1
                ranking[ag] = {
                    "ligacoes_total":     v["ligacoes_total"],
                    "falado_total":       sec_to_hms(v["falado_sec_total"]),
                    "ocioso_total":       sec_to_hms(v["ocioso_sec_total"]),
                    "media_ligacoes_dia": round(v["ligacoes_total"] / dias_ativos, 1),
                    "recusas_total":      v["recusas_total"],
                    "nps":                calcular_nps(v["p_total"], v["n_total"], v["d_total"]),
                    "nps_promotores":     v["p_total"],
                    "nps_neutros":        v["n_total"],
                    "nps_detratores":     v["d_total"],
                    "nps_respostas":      v["p_total"] + v["n_total"] + v["d_total"],
                    "historico_diario":   v["historico"]
                }

            resultado["filas"][fila_nome] = {
                "resumo_geral": {
                    "total_ligacoes":   total_calls,
                    "atendidas":        total_atend,
                    "abandonadas":      total_aband,
                    "taxa_atendimento": f"{round(total_atend/total_calls*100,1)}%" if total_calls else "0%",
                    "total_falado":     sec_to_hms(total_falado_sec),
                    "nps_periodo":      calcular_nps(gp, gn, gd),
                    "nps_promotores":   gp,
                    "nps_neutros":      gn,
                    "nps_detratores":   gd,
                    "nps_respostas":    gp + gn + gd
                },
                "por_dia":    dias_r02,
                "agentes":    ranking,
                "erros":      erros
            }

        JOBS[job_id]["status"]    = "pronto"
        JOBS[job_id]["resultado"] = resultado

    except Exception as e:
        JOBS[job_id]["status"] = "erro"
        JOBS[job_id]["erro"]   = str(e)


@app.route("/api/resumo/iniciar", methods=["GET", "OPTIONS"])
def resumo_iniciar():
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
        return jsonify({"erro": "Periodo maximo: 31 dias"}), 400

    filas_sel = [f.strip() for f in filas_req.split(",") if f.strip() in FILAS]
    if not filas_sel:
        return jsonify({"erro": "Filas invalidas. Use: betel, nf"}), 400

    job_id = str(uuid.uuid4())[:8]
    JOBS[job_id] = {"status": "iniciando", "resultado": None, "erro": None}

    t = threading.Thread(target=processar_job, args=(job_id, dt_inicio, dt_fim, filas_sel))
    t.daemon = True
    t.start()

    dias = (d_fim - d_ini).days + 1
    return jsonify({
        "job_id":              job_id,
        "status":              "iniciando",
        "estimativa_segundos": dias * len(filas_sel) * 30,
        "consultar_em":        f"/api/resumo/resultado/{job_id}"
    }), 202


@app.route("/api/resumo/resultado/<job_id>", methods=["GET"])
def resumo_resultado(job_id):
    job = JOBS.get(job_id)
    if not job:
        return jsonify({"erro": "Job nao encontrado"}), 404
    if job["status"] in ("iniciando", "processando"):
        return jsonify({"status": job["status"], "mensagem": "Ainda processando, tente em 30s"}), 202
    if job["status"] == "erro":
        return jsonify({"status": "erro", "erro": job["erro"]}), 500
    return jsonify({"status": "pronto", "resultado": job["resultado"]}), 200


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
