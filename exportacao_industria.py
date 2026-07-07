"""
Gera industria_data.js com vendas agregadas por (fornecedor/indústria, mês, cliente)
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
oracledb.init_oracle_client(lib_dir=ORACLE_LIB)

USER     = os.getenv("VPN_USER",     "vpn")
PASSWORD = os.getenv("VPN_PASSWORD", "vpn2320vpn")
CRC_USER = os.getenv("CRC_USER",     USER)
CRC_PASS = os.getenv("CRC_PASSWORD", PASSWORD)
_CRC_DSN = os.getenv("DSN_CRC", "crc_oci")

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
            MAX(PCMOV.DTMOV)                            AS ULT_COMPRA_MES,
            SUM(PCMOV.PUNIT * PCMOV.QT)                 AS VALOR,
            SUM(PCMOV.QT)                                AS QT,
            RCA_U.NOME                                   AS VENDEDOR,
            RCA_U.CODUSUR                                AS RCA,
            PCCLIENT.DTULTCOMP                           AS DTULTCOMP_GERAL
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
          AND (PCUSUARI.NOME LIKE '%OFF TRADE%' OR PCUSUARI.NOME LIKE '%W.S%')
          {fil_clause}
        GROUP BY PCFORNEC.FANTASIA, TRUNC(PCMOV.DTMOV, 'MM'), PCMOV.CODCLI, PCCLIENT.CLIENTE,
                 PCCLIENT.BAIRROENT, PCCLIENT.MUNICENT, RCA_U.NOME, RCA_U.CODUSUR,
                 PCCLIENT.DTULTCOMP
    """


def _carregar() -> tuple[pd.DataFrame, list[str]]:
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
                _u = CRC_USER if dsn == _CRC_DSN else USER
                _p = CRC_PASS if dsn == _CRC_DSN else PASSWORD
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

        ok = False
        for tentativa in range(1, 4):
            try:
                print(f"  Consultando {estado} ({schema} filiais={filiais}) — tentativa {tentativa}/3…")
                with engines[dsn].connect() as conn:
                    df = pd.read_sql(_query(schema, filiais), conn)
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

    if not frames:
        return pd.DataFrame(), fontes_indisponiveis
    return pd.concat(frames, ignore_index=True), fontes_indisponiveis


print("\n=== Carregando vendas por indústria (6 meses, RJ/SP/ES/MG) ===")
df, fontes_indisponiveis = _carregar()

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

    registros_out = []
    for _, r in df.iterrows():
        registros_out.append({
            "fantasia":         r["FANTASIA"],
            "mes":              _mes_pt(r["MES"]),
            "codcli":           str(r["CODCLI"]),
            "cliente":          r["CLIENTE"],
            "bairro":           r["BAIRRO"] if pd.notna(r["BAIRRO"]) else "",
            "cidade":           r["CIDADE"] if pd.notna(r["CIDADE"]) else "",
            "estado":           r["ESTADO"],
            "vendedor":         r["VENDEDOR"],
            "rca":              r["RCA"],
            "valor":            float(r["VALOR"]),
            "qt":               int(r["QT"]),
            "ult_compra_mes":   r["ULT_COMPRA_MES"].strftime("%d/%m/%Y") if pd.notna(r["ULT_COMPRA_MES"]) else None,
            "dtultcomp_geral":  r["DTULTCOMP_GERAL"].strftime("%d/%m/%Y") if pd.notna(r["DTULTCOMP_GERAL"]) else None,
            "dias_sem_compra":  r["DIAS_SEM_COMPRA"],
        })

payload = {
    "atualizado_em":         datetime.now().strftime("%d/%m/%Y %H:%M"),
    "meses":                 meses_out,
    "fornecedores":          fornecedores_out,
    "registros":             registros_out,
    "fontes_indisponiveis":  sorted(set(fontes_indisponiveis)),
}

out = BASE / "industria_data.js"
out.write_text(
    "// Gerado automaticamente\n\n"
    f"const INDUSTRIA_DATA = {json.dumps(payload, ensure_ascii=False, indent=2)};\n",
    encoding="utf-8",
)
print(f"\nOK industria_data.js — {len(registros_out)} registros, {len(fornecedores_out)} fornecedores -> {out}")
if fontes_indisponiveis:
    print(f"[AVISO] Fontes indisponíveis: {sorted(set(fontes_indisponiveis))} — resultados podem estar incompletos.")

repo_dir = str(BASE)
subprocess.run(["git", "-C", repo_dir, "add", "industria_data.js"], check=True)
subprocess.run(["git", "-C", repo_dir, "commit", "-m",
                f"Atualiza industria_data.js - {date.today().strftime('%d/%m/%Y')}"])
subprocess.run(["git", "-C", repo_dir, "push", "origin", "master"], check=True)
print("OK GitHub Pages atualizado.")
