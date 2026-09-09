"""
Raio X do Cliente — busca sob demanda (Flask), pra clientes que NÃO estão no
raiox_cliente_detalhe_data.js gerado em lote (exportacao_raiox_cliente_detalhe.py).

Contexto: esse arquivo em lote só cobre clientes com RCA Off Trade ativo
(CODUSUR1/2/3 com "OFF TRADE" no nome). Cliente parado nos "baldes" RCA
10 (Inativo) ou 200 (Novos Clientes) — que é exatamente quem aparece em
clientes_rca.html?rcas=10,200 — fica de fora. Tentamos incluir esses
clientes no lote (pedido do usuário em 09/09/2026), mas RCA 10 sozinho tem
~86 mil clientes acumulados em todas as bases (décadas de histórico) —
o arquivo estourou pra 144 MB e o GitHub rejeitou o push (limite de 100 MB,
achado no mesmo dia). Em vez de inflar o lote, essa API resolve UM cliente
por vez (WHERE CODCLI = :codcli, rápido mesmo pro balde gigante) quando
raiox_cliente_detalhe.html não encontra a chave no arquivo em lote.

Não faz o cálculo de "recomendações de produto" (cross-sell) — precisaria
do grupo inteiro de clientes do mesmo ramo/estado, que só existe no lote;
aceitável aqui porque cliente inativo/novo não é o público desse recurso.

Uso local: python raiox_cliente_api.py (abre em http://localhost:5058)
Na VPS roda atrás do nginx em /api/raiox-cliente/ (ver
deploy_raiox_cliente_api_vps.py), no mesmo padrão de metas_builder_api.py
(dentro de /opt/offtrade-pipeline, venv com Oracle já configurado).
"""
import os
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd
from dotenv import load_dotenv
from flask import Flask, Blueprint, request

load_dotenv()

RUNTIME = os.getenv("OFFTRADE_RUNTIME", "local")

# meta.py já chama oracledb.init_oracle_client() — importar antes de
# qualquer outra coisa evita "Oracle Client library has already been
# initialized" (mesmo padrão de metas_builder_api.py/report_diario_vendedor.py).
import meta  # noqa: E402
from meta import engine, engine_spon, engine_mgon, carregar_dados  # noqa: E402

app = Flask(__name__)
bp = Blueprint("raiox_cliente", __name__, url_prefix="/api/raiox-cliente")

_SCHEMA_POR_ESTADO = {"RJ": ("CRC", engine), "ES": ("CRC", engine), "SP": ("SPON", engine_spon), "MG": ("MGON", engine_mgon)}

HOJE = date.today()
MES_INI = f"{HOJE.year}-01-01"
MES_FIM = HOJE.strftime("%Y-%m-%d")

MIN_COMPRAS_CICLO_CONFIAVEL = 3
MIN_FATURAMENTO_RISCO = 200.0


def _query_cadastro(schema, codcli):
    return f"""
        SELECT C.CODCLI, C.CLIENTE, COALESCE(C.FANTASIA, C.CLIENTE) FANTASIA,
               COALESCE(C.MUNICENT,'') CIDADE, COALESCE(A.RAMO,'OUTROS') RAMO,
               C.CODUSUR1, C.CODUSUR2, C.CODUSUR3, C.DTULTCOMP, COALESCE(R.DESCRICAO,'') REDE
        FROM {schema}.PCCLIENT C
        LEFT JOIN {schema}.PCATIVI A ON C.CODATV1 = A.CODATIV
        LEFT JOIN {schema}.PCREDECLIENTE R ON C.CODREDE = R.CODREDE
        WHERE C.CODCLI = {codcli}
    """


def _query_vendas(schema, codcli):
    return f"""
        SELECT TRUNC(M.DTMOV,'MM') AS MES,
               COALESCE(F.FANTASIA,'SEM FANTASIA') AS FORNECEDOR,
               M.CODUSUR, SUM(M.PUNIT*M.QT) AS FATURAMENTO
        FROM {schema}.PCMOV M
        JOIN {schema}.PCFORNEC F ON M.CODFORNEC = F.CODFORNEC
        WHERE M.CODCLI = {codcli}
          AND M.CODOPER = 'S' AND M.NUMNOTADEV IS NULL AND M.DTCANCEL IS NULL
          AND TRUNC(M.DTMOV) >= TO_DATE('{MES_INI}','YYYY-MM-DD')
          AND TRUNC(M.DTMOV) <= TO_DATE('{MES_FIM}','YYYY-MM-DD')
        GROUP BY TRUNC(M.DTMOV,'MM'), COALESCE(F.FANTASIA,'SEM FANTASIA'), M.CODUSUR
    """


def _query_historico(schema, codcli):
    return f"""
        SELECT TRUNC(M.DTMOV) AS DATA,
               COALESCE(F.FANTASIA,'SEM FANTASIA') AS FORNECEDOR,
               COALESCE(P.DESCRICAO, 'Produto ' || M.CODPROD) AS PRODUTO,
               M.QT, (M.PUNIT*M.QT) AS VALOR
        FROM {schema}.PCMOV M
        JOIN {schema}.PCFORNEC F ON M.CODFORNEC = F.CODFORNEC
        LEFT JOIN {schema}.PCPRODUT P ON M.CODPROD = P.CODPROD
        WHERE M.CODCLI = {codcli}
          AND M.CODOPER = 'S' AND M.NUMNOTADEV IS NULL AND M.DTCANCEL IS NULL
          AND TRUNC(M.DTMOV) >= TO_DATE('{MES_INI}','YYYY-MM-DD')
          AND TRUNC(M.DTMOV) <= TO_DATE('{MES_FIM}','YYYY-MM-DD')
        ORDER BY TRUNC(M.DTMOV) DESC
    """


def _query_usuarios(schema, coduusrs):
    ids = ",".join(str(int(c)) for c in coduusrs)
    return f"""
        SELECT U.CODUSUR, U.NOME,
               COALESCE(S.NOME, 'Sem supervisor') AS SUPERVISOR,
               COALESCE(G.NOMEGERENTE, 'Sem gerente') AS GERENTE
        FROM {schema}.PCUSUARI U
        LEFT JOIN {schema}.PCSUPERV S ON U.CODSUPERVISOR = S.CODSUPERVISOR
        LEFT JOIN {schema}.PCGERENTE G ON S.CODGERENTE = G.CODGERENTE
        WHERE U.CODUSUR IN ({ids})
    """


def _ciclo_e_risco(hist_cliente: list) -> dict:
    """Mesma lógica de exportacao_raiox_cliente_detalhe.py::_ciclo_e_risco."""
    datas = sorted({datetime.strptime(h["data"], "%d/%m/%Y").date() for h in hist_cliente})
    resultado = {
        "ciclo_medio_dias": None, "dias_desde_ultima_compra": None,
        "atrasado_recompra": False, "em_risco_queda": False, "queda_pct": 0.0,
        "faturamento_30d": 0.0, "faturamento_30_60d": 0.0,
    }
    if not datas:
        return resultado

    dias_desde_ultima = (HOJE - datas[-1]).days
    resultado["dias_desde_ultima_compra"] = dias_desde_ultima

    if len(datas) >= MIN_COMPRAS_CICLO_CONFIAVEL:
        gaps = [(b - a).days for a, b in zip(datas[:-1], datas[1:])]
        ciclo_medio = sum(gaps) / len(gaps)
        resultado["ciclo_medio_dias"] = round(ciclo_medio, 1)
        resultado["atrasado_recompra"] = dias_desde_ultima > max(ciclo_medio * 1.5, 30)

    janela_recente = HOJE - timedelta(days=30)
    janela_anterior = HOJE - timedelta(days=60)
    fat_recente = sum(h["valor"] for h in hist_cliente if datetime.strptime(h["data"], "%d/%m/%Y").date() > janela_recente)
    fat_anterior = sum(h["valor"] for h in hist_cliente if janela_anterior < datetime.strptime(h["data"], "%d/%m/%Y").date() <= janela_recente)
    resultado["faturamento_30d"] = round(fat_recente, 2)
    resultado["faturamento_30_60d"] = round(fat_anterior, 2)
    if fat_anterior >= MIN_FATURAMENTO_RISCO and fat_recente < fat_anterior * 0.6:
        resultado["em_risco_queda"] = True
        resultado["queda_pct"] = round((1 - fat_recente / fat_anterior) * 100, 1)

    return resultado


@bp.route("/detalhe", methods=["GET"])
def detalhe():
    chave = request.args.get("chave", "").strip()
    if "-" not in chave:
        return {"ok": False, "motivo": "Parâmetro 'chave' inválido — use ESTADO-CODCLI (ex: RJ-84665)."}, 400
    estado, _, codcli_s = chave.partition("-")
    estado = estado.upper()
    if estado not in _SCHEMA_POR_ESTADO or not codcli_s.isdigit():
        return {"ok": False, "motivo": f"Chave inválida: {chave}"}, 400
    codcli = int(codcli_s)
    schema, eng = _SCHEMA_POR_ESTADO[estado]

    try:
        cad = carregar_dados(_query_cadastro(schema, codcli), eng, "raiox_cliapi_cadastro")
    except Exception as e:
        return {"ok": False, "motivo": f"Falha ao consultar Oracle: {str(e)[:200]}"}, 502
    if cad.empty:
        return {"ok": False, "motivo": "Cliente não encontrado."}, 404
    cad.columns = cad.columns.str.upper()
    linha = cad.iloc[0]

    coduusrs = [int(linha[c]) for c in ("CODUSUR1", "CODUSUR2", "CODUSUR3") if pd.notna(linha[c])]

    vd = carregar_dados(_query_vendas(schema, codcli), eng, "raiox_cliapi_vendas")
    vd.columns = vd.columns.str.upper()
    vd["MES"] = pd.to_datetime(vd["MES"])
    vd["FORNECEDOR"] = vd["FORNECEDOR"].fillna("SEM FANTASIA").str.strip()
    coduusrs += [int(c) for c in vd["CODUSUR"].dropna().unique() if int(c) not in coduusrs]

    usuarios = {}
    if coduusrs:
        u = carregar_dados(_query_usuarios(schema, coduusrs), eng, "raiox_cliapi_usuarios")
        u.columns = u.columns.str.upper()
        for _, r in u.iterrows():
            usuarios[int(r["CODUSUR"])] = {
                "nome": r["NOME"].replace("- OFF TRADE", "").replace("-OFF TRADE", "").strip(),
                "supervisor": r["SUPERVISOR"], "gerente": r["GERENTE"],
            }

    def _nome_rca(rca):
        return usuarios.get(rca, {}).get("nome", f"RCA {rca}")

    fat_ytd = float(vd["FATURAMENTO"].sum())
    n_meses = vd["MES"].nunique() or 1
    media_mensal = fat_ytd / n_meses if not vd.empty else 0.0
    por_mes = {d.strftime("%Y-%m"): round(float(f), 2) for d, f in vd.groupby("MES")["FATURAMENTO"].sum().items()}
    por_industria = vd.groupby("FORNECEDOR")["FATURAMENTO"].sum().sort_values(ascending=False)
    top_industrias = [
        {"fantasia": f, "faturamento": round(float(v), 2), "pct": round(float(v) / fat_ytd * 100, 1) if fat_ytd else 0.0}
        for f, v in por_industria.items()
    ]
    por_vendedor = vd.groupby("CODUSUR")["FATURAMENTO"].sum().sort_values(ascending=False)
    top_vendedores = [
        {"rca": int(rca), "nome": _nome_rca(int(rca)), "faturamento": round(float(v), 2)}
        for rca, v in por_vendedor.items() if pd.notna(rca)
    ]

    h = carregar_dados(_query_historico(schema, codcli), eng, "raiox_cliapi_historico")
    h.columns = h.columns.str.upper()
    h["DATA"] = pd.to_datetime(h["DATA"])
    h["FORNECEDOR"] = h["FORNECEDOR"].fillna("SEM FANTASIA").str.strip()
    h["PRODUTO"] = h["PRODUTO"].fillna("").str.strip()
    historico_compras = [
        {
            "data": r["DATA"].strftime("%d/%m/%Y"), "industria": r["FORNECEDOR"], "produto": r["PRODUTO"],
            "quantidade": float(r["QT"]), "valor": round(float(r["VALOR"]), 2),
        }
        for _, r in h.iterrows()
    ]

    vendedores_cadastro = [
        {"rca": int(linha[c]), "nome": _nome_rca(int(linha[c]))}
        for c in ("CODUSUR1", "CODUSUR2", "CODUSUR3") if pd.notna(linha[c])
    ]
    _hier = None
    for c in ("CODUSUR1", "CODUSUR2", "CODUSUR3"):
        if pd.notna(linha[c]) and int(linha[c]) in usuarios:
            _hier = usuarios[int(linha[c])]
            break

    ultima_compra = ""
    if pd.notna(linha.get("DTULTCOMP")):
        try:
            ultima_compra = pd.to_datetime(linha["DTULTCOMP"]).strftime("%d/%m/%Y")
        except Exception:
            ultima_compra = ""

    ciclo_risco = _ciclo_e_risco(historico_compras)

    registro = {
        "codcli": int(linha["CODCLI"]),
        "estado": estado,
        "chave": chave,
        "nome": (linha["FANTASIA"] or linha["CLIENTE"] or f"Cliente {codcli}").strip(),
        "razao_social": linha["CLIENTE"],
        "cidade": (linha["CIDADE"] or "N/D").strip() or "N/D",
        "ramo": linha["RAMO"],
        "rede": linha["REDE"] or "Sem rede",
        "gerente": _hier["gerente"] if _hier else "Sem gerente",
        "supervisor": _hier["supervisor"] if _hier else "Sem supervisor",
        "vendedores_cadastro": vendedores_cadastro,
        "ultima_compra": ultima_compra,
        "ativo_periodo": not vd.empty,
        "faturamento_ytd": round(fat_ytd, 2),
        "media_mensal": round(media_mensal, 2),
        "por_mes": por_mes,
        "top_industrias": top_industrias,
        "top_vendedores": top_vendedores,
        "historico_compras": historico_compras,
        "ciclo_medio_dias": ciclo_risco["ciclo_medio_dias"],
        "dias_desde_ultima_compra": ciclo_risco["dias_desde_ultima_compra"],
        "atrasado_recompra": ciclo_risco["atrasado_recompra"],
        "em_risco_queda": ciclo_risco["em_risco_queda"],
        "queda_pct": ciclo_risco["queda_pct"],
        "faturamento_30d": ciclo_risco["faturamento_30d"],
        "faturamento_30_60d": ciclo_risco["faturamento_30_60d"],
        "prioridade_visita_score": round(media_mensal * (1 + (ciclo_risco["dias_desde_ultima_compra"] or 0) / 30), 2),
        "recomendacoes": [],
        "fonte": "sob_demanda",
    }
    return {"ok": True, "cliente": registro}


app.register_blueprint(bp)

if __name__ == "__main__":
    debug = RUNTIME != "vps"
    host = "127.0.0.1" if RUNTIME == "vps" else "0.0.0.0"
    app.run(host=host, port=5058, debug=debug, threaded=True)
