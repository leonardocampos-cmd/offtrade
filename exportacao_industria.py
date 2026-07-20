"""
Gera industria_data.js com vendas agregadas por (fornecedor/indústria, mês, cliente, produto)
nas 4 bases (RJ, SP, ES, MG), para responder:
  - "quais clientes compraram do fornecedor X no período X a Y"
  - cruzando com DTULTCOMP (ERP, empresa toda) para sinalizar clientes
    sem nenhuma compra há 60+ dias, independente do fornecedor.
"""
import json
import subprocess
from datetime import datetime, date
from pathlib import Path

import oracledb
import pandas as pd
from sqlalchemy import create_engine
from dotenv import load_dotenv
import os

load_dotenv(Path(__file__).parent / ".env")

from urllib.parse import quote_plus

from utils import ORACLE_LIB
# meta.py já chama oracledb.init_oracle_client() — importar antes evita o erro
# "Oracle Client library has already been initialized" (só pode rodar 1x/processo).
from meta import _com_timeout_forcado

USER     = os.environ["VPN_USER"]
PASSWORD = os.environ["VPN_PASSWORD"]
CRC_USER = os.getenv("CRC_USER",     USER)
CRC_PASS = os.getenv("CRC_PASSWORD", PASSWORD)
_CRC_DSN = os.getenv("DSN_CRC", "crc_oci")
SPON_USER = os.getenv("SPON_USER",     USER)
SPON_PASS = os.getenv("SPON_PASSWORD", PASSWORD)
_SPON_DSN = os.getenv("DSN_SP", "spon_oci")

MESES_HISTORICO = 6  # mês atual + 5 anteriores

# Mesma topologia de exportacao_metas_gerais.py: RJ e ES compartilham o Oracle
# da CRC, diferindo pela filial.
BASES = [
    {"dsn": os.getenv("DSN_RJ", "crc_oci"),  "schema": "CRC",  "estado": "RJ", "filiais": ["2", "4"]},
    {"dsn": os.getenv("DSN_ES", "crc_oci"),  "schema": "CRC",  "estado": "ES", "filiais": ["1"]},
    {"dsn": os.getenv("DSN_SP", "spon_oci"), "schema": "SPON", "estado": "SP", "filiais": ["1", "2"]},
    {"dsn": os.getenv("DSN_MG", "mgon_oci"), "schema": "MGON", "estado": "MG", "filiais": ["1", "2"]},
]

BASE = Path(__file__).parent

_MESES_PT = ['Jan', 'Fev', 'Mar', 'Abr', 'Mai', 'Jun', 'Jul', 'Ago', 'Set', 'Out', 'Nov', 'Dez']


def _mes_pt(dt) -> str:
    return f"{_MESES_PT[dt.month - 1]}/{str(dt.year)[2:]}"


def _query(schema: str, filiais: list | None) -> str:
    p = f"{schema}."
    fil_clause = f"AND PCMOV.CODFILIAL IN ({','.join(filiais)})" if filiais else ""
    return f"""
        SELECT
            PCFORNEC.FANTASIA                          AS FANTASIA,
            TRUNC(PCMOV.DTMOV, 'MM')                    AS MES,
            PCMOV.CODCLI                                AS CODCLI,
            PCCLIENT.CLIENTE                            AS CLIENTE,
            PCCLIENT.BAIRROENT                          AS BAIRRO,
            PCCLIENT.MUNICENT                           AS CIDADE,
            PCMOV.CODPROD                               AS CODPROD,
            PCMOV.DESCRICAO                             AS PRODUTO,
            MAX(PCMOV.DTMOV)                            AS ULT_COMPRA_MES,
            SUM(PCMOV.PUNIT * PCMOV.QT)                 AS VALOR,
            SUM(PCMOV.QT)                                AS QT,
            RCA_U.NOME                                   AS VENDEDOR,
            RCA_U.CODUSUR                                AS RCA,
            PCCLIENT.DTULTCOMP                           AS DTULTCOMP_GERAL,
            CASE WHEN (PCUSUARI.NOME LIKE '%OFF TRADE%' OR PCUSUARI.NOME LIKE '%W.S%')
                 THEN 'OFF' ELSE 'ON' END                 AS CANAL
        FROM {p}PCMOV
        JOIN {p}PCUSUARI  ON PCMOV.CODUSUR      = PCUSUARI.CODUSUR
        JOIN {p}PCPRODUT  ON PCMOV.CODPROD      = PCPRODUT.CODPROD
        JOIN {p}PCFORNEC  ON PCPRODUT.CODFORNEC = PCFORNEC.CODFORNEC
        JOIN {p}PCCLIENT  ON PCMOV.CODCLI       = PCCLIENT.CODCLI
        LEFT JOIN {p}PCUSUARI RCA_U ON PCCLIENT.CODUSUR1 = RCA_U.CODUSUR
        WHERE PCMOV.DTMOV >= ADD_MONTHS(TRUNC(SYSDATE, 'MM'), -{MESES_HISTORICO - 1})
          AND PCMOV.CODOPER IN ('S', 'SB')
          AND PCMOV.NUMNOTADEV IS NULL
          AND PCMOV.DTCANCEL IS NULL
          {fil_clause}
        GROUP BY PCFORNEC.FANTASIA, TRUNC(PCMOV.DTMOV, 'MM'), PCMOV.CODCLI, PCCLIENT.CLIENTE,
                 PCCLIENT.BAIRROENT, PCCLIENT.MUNICENT, PCMOV.CODPROD, PCMOV.DESCRICAO,
                 RCA_U.NOME, RCA_U.CODUSUR, PCCLIENT.DTULTCOMP,
                 CASE WHEN (PCUSUARI.NOME LIKE '%OFF TRADE%' OR PCUSUARI.NOME LIKE '%W.S%')
                      THEN 'OFF' ELSE 'ON' END
    """


def _query_hierarquia(schema: str) -> str:
    p = f"{schema}."
    return f"""
        SELECT U.CODUSUR, U.NOME AS NOME_VENDEDOR, U.ESTADO AS ESTADO_VENDEDOR,
               S.CODSUPERVISOR, S.NOME AS NOME_SUPERVISOR,
               G.CODGERENTE, G.NOMEGERENTE
        FROM {p}PCUSUARI U
        LEFT JOIN {p}PCSUPERV  S ON U.CODSUPERVISOR = S.CODSUPERVISOR
        LEFT JOIN {p}PCGERENTE G ON S.CODGERENTE     = G.CODGERENTE
        WHERE (U.NOME LIKE '%OFF TRADE%' OR U.NOME LIKE '%W.S%')
          AND U.BLOQUEIO = 'N'
    """


def _carregar() -> tuple[pd.DataFrame, list[str], list[dict]]:
    frames = []
    fontes_indisponiveis = []
    engines = {}

    for cfg in BASES:
        dsn, schema, estado, filiais = cfg["dsn"], cfg["schema"], cfg["estado"], cfg["filiais"]
        if not dsn:
            print(f"  [skip] {estado}: DSN não configurado")
            continue

        if dsn not in engines:
            try:
                if dsn == _CRC_DSN:
                    _u, _p = CRC_USER, CRC_PASS
                elif dsn == _SPON_DSN:
                    _u, _p = SPON_USER, SPON_PASS
                else:
                    _u, _p = USER, PASSWORD
                engines[dsn] = create_engine(
                    f"oracle+oracledb://{_u}:{quote_plus(_p)}@{dsn}",
                    pool_pre_ping=True,
                    pool_recycle=3600,
                    connect_args={"expire_time": 2},
                )
            except Exception as e:
                print(f"  [erro] {estado}: engine falhou — {e}")
                fontes_indisponiveis.append(estado)
                continue

        def _fazer_query(_dsn=dsn, _schema=schema, _filiais=filiais):
            with engines[_dsn].connect() as conn:
                return pd.read_sql(_query(_schema, _filiais), conn)

        ok = False
        for tentativa in range(1, 4):
            try:
                print(f"  Consultando {estado} ({schema} filiais={filiais}) — tentativa {tentativa}/3…")
                df = _com_timeout_forcado(_fazer_query, 90)
                df.columns = df.columns.str.upper()
                df["ESTADO"] = estado
                frames.append(df)
                print(f"    OK {len(df)} linhas")
                ok = True
                break
            except Exception as e:
                print(f"    Erro: {str(e)[:150]}")
        if not ok:
            fontes_indisponiveis.append(estado)

    # ── Hierarquia (estado → gerente → supervisor → vendedor) ────────────────
    # Uma consulta por (dsn, schema) único — RJ e ES compartilham a base CRC,
    # a diferenciação de estado vem do próprio PCUSUARI.ESTADO.
    hier_emp: dict = {}
    vistos = set()
    for cfg in BASES:
        dsn, schema, estado = cfg["dsn"], cfg["schema"], cfg["estado"]
        if not dsn or dsn not in engines or (dsn, schema) in vistos:
            continue
        vistos.add((dsn, schema))
        try:
            def _fazer_query_h(_dsn=dsn, _schema=schema):
                with engines[_dsn].connect() as conn:
                    return pd.read_sql(_query_hierarquia(_schema), conn)
            df_h = _com_timeout_forcado(_fazer_query_h, 90)
            df_h.columns = df_h.columns.str.upper()
            for _, r in df_h.iterrows():
                nome_v = str(r.get("NOME_VENDEDOR") or "").strip()
                if not nome_v:
                    continue
                est_v = str(r.get("ESTADO_VENDEDOR") or "").strip() or estado
                ger   = str(r.get("NOMEGERENTE") or "").strip() or "Sem Gerente"
                sup   = str(r.get("NOME_SUPERVISOR") or "").strip() or "Sem Supervisor"
                rca   = r.get("CODUSUR")
                e_ = hier_emp.setdefault(est_v, {"nome": est_v, "gerentes": {}})
                g_ = e_["gerentes"].setdefault(ger, {"nome": ger, "supervisores": {}})
                s_ = g_["supervisores"].setdefault(sup, {"nome": sup, "vendedores": {}})
                s_["vendedores"][nome_v] = {
                    "nome": nome_v,
                    "rca": str(int(rca)) if pd.notna(rca) else "",
                }
        except Exception as e:
            print(f"  [aviso] hierarquia {schema} falhou: {str(e)[:150]}")

    hierarquia = []
    for est_data in sorted(hier_emp.values(), key=lambda x: x["nome"]):
        gers_out = []
        for g_data in sorted(est_data["gerentes"].values(), key=lambda x: x["nome"]):
            sups_out = []
            for s_data in sorted(g_data["supervisores"].values(), key=lambda x: x["nome"]):
                vends_out = sorted(s_data["vendedores"].values(), key=lambda x: x["nome"])
                sups_out.append({"nome": s_data["nome"], "vendedores": vends_out})
            gers_out.append({"nome": g_data["nome"], "supervisores": sups_out})
        hierarquia.append({"nome": est_data["nome"], "gerentes": gers_out})

    df_out = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    return df_out, fontes_indisponiveis, hierarquia


print("\n=== Carregando vendas por indústria (6 meses, RJ/SP/ES/MG) ===")
df, fontes_indisponiveis, hierarquia = _carregar()

if df.empty:
    meses_out = []
    fornecedores_out = []
    registros_out = []
else:
    hoje = date.today()
    df["MES"]                = pd.to_datetime(df["MES"])
    df["VALOR"]               = pd.to_numeric(df["VALOR"], errors="coerce").fillna(0).round(2)
    df["QT"]                  = pd.to_numeric(df["QT"], errors="coerce").fillna(0).astype(int)
    df["FANTASIA"]            = df["FANTASIA"].fillna("SEM FORNECEDOR").str.strip().str.upper()
    df["CLIENTE"]              = df["CLIENTE"].fillna("")
    df["VENDEDOR"]            = df["VENDEDOR"].fillna("Sem RCA")
    df["RCA"]                  = df["RCA"].apply(lambda v: str(int(v)) if pd.notna(v) else "")
    df["DTULTCOMP_GERAL"]     = pd.to_datetime(df["DTULTCOMP_GERAL"], errors="coerce")
    df["DIAS_SEM_COMPRA"]     = df["DTULTCOMP_GERAL"].apply(
        lambda d: (hoje - d.date()).days if pd.notna(d) else None
    )

    meses_out = sorted(df["MES"].dropna().unique(), reverse=True)
    meses_out = [_mes_pt(pd.Timestamp(m)) for m in meses_out]

    fornecedores_out = sorted(df["FANTASIA"].unique().tolist())

    df["PRODUTO"] = df["PRODUTO"].fillna("")

    # Envelope por (fantasia, mês, cliente, canal) com os produtos aninhados —
    # evita repetir cliente/cidade/vendedor/etc em cada linha de produto,
    # o que explodia o tamanho do industria_data.js (>100MB, GitHub rejeita).
    envelopes: dict = {}
    ordem = []
    for _, r in df.iterrows():
        key = (r["FANTASIA"], r["MES"], str(r["CODCLI"]), r["CANAL"])
        env = envelopes.get(key)
        if env is None:
            env = {
                "fantasia":         r["FANTASIA"],
                "mes":              _mes_pt(r["MES"]),
                "codcli":           str(r["CODCLI"]),
                "cliente":          r["CLIENTE"],
                "bairro":           r["BAIRRO"] if pd.notna(r["BAIRRO"]) else "",
                "cidade":           r["CIDADE"] if pd.notna(r["CIDADE"]) else "",
                "estado":           r["ESTADO"],
                "vendedor":         r["VENDEDOR"],
                "rca":              r["RCA"],
                "canal":            r["CANAL"],
                "valor":            0.0,
                "qt":               0,
                "_ult_compra_dt":   None,
                "dtultcomp_geral":  r["DTULTCOMP_GERAL"].strftime("%d/%m/%Y") if pd.notna(r["DTULTCOMP_GERAL"]) else None,
                "dias_sem_compra":  r["DIAS_SEM_COMPRA"],
                "produtos":         [],
            }
            envelopes[key] = env
            ordem.append(key)
        env["valor"] += float(r["VALOR"])
        env["qt"]    += int(r["QT"])
        if pd.notna(r["ULT_COMPRA_MES"]):
            if env["_ult_compra_dt"] is None or r["ULT_COMPRA_MES"] > env["_ult_compra_dt"]:
                env["_ult_compra_dt"] = r["ULT_COMPRA_MES"]
        env["produtos"].append({
            "codprod": str(r["CODPROD"]),
            "produto": r["PRODUTO"],
            "valor":   round(float(r["VALOR"]), 2),
            "qt":      int(r["QT"]),
        })

    registros_out = []
    for key in ordem:
        env = envelopes[key]
        ult_dt = env.pop("_ult_compra_dt")
        env["ult_compra_mes"] = ult_dt.strftime("%d/%m/%Y") if ult_dt is not None else None
        env["valor"] = round(env["valor"], 2)
        env["produtos"].sort(key=lambda p: p["valor"], reverse=True)
        registros_out.append(env)

payload = {
    "atualizado_em":         datetime.now().strftime("%d/%m/%Y %H:%M"),
    "meses":                 meses_out,
    "fornecedores":          fornecedores_out,
    "registros":             registros_out,
    "hierarquia":            hierarquia,
    "fontes_indisponiveis":  sorted(set(fontes_indisponiveis)),
}

out = BASE / "industria_data.js"
out.write_text(
    "// Gerado automaticamente\n\n"
    f"const INDUSTRIA_DATA = {json.dumps(payload, ensure_ascii=False, separators=(',', ':'))};\n",
    encoding="utf-8",
)
tamanho_mb = out.stat().st_size / (1024 * 1024)
print(f"\nOK industria_data.js — {len(registros_out)} registros (envelopes), {len(fornecedores_out)} fornecedores, "
      f"{tamanho_mb:.1f}MB -> {out}")
if fontes_indisponiveis:
    print(f"[AVISO] Fontes indisponíveis: {sorted(set(fontes_indisponiveis))} — resultados podem estar incompletos.")

LIMITE_MB = 90  # margem de segurança abaixo do limite de 100MB do GitHub
if tamanho_mb > LIMITE_MB:
    print(f"[ERRO] industria_data.js tem {tamanho_mb:.1f}MB (> {LIMITE_MB}MB) — "
          f"commit/push abortados para não quebrar o pipeline no GitHub. "
          f"Reduza MESES_HISTORICO ou o escopo de dados.")
else:
    repo_dir = str(BASE)
    subprocess.run(["git", "-C", repo_dir, "add", "industria_data.js"], check=True)
    subprocess.run(["git", "-C", repo_dir, "commit", "-m",
                    f"Atualiza industria_data.js - {date.today().strftime('%d/%m/%Y')}"])
    subprocess.run(["git", "-C", repo_dir, "push", "origin", "master"], check=True)
    print("OK GitHub Pages atualizado.")
