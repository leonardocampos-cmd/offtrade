"""
Gera clientes_inativos_data.js com:
  - inativos:        BLOQUEIO='S', CODUSUR1=10, CODUSUR2=RCA
  - sem_compra:      BLOQUEIO='N', >= 30 dias sem compra
  - novos_sem_compra: cadastrados nos últimos 90 dias sem compra
"""
import json
import pandas as pd
from datetime import datetime
from pathlib import Path
from urllib.parse import quote_plus
import os
import subprocess

from dotenv import load_dotenv
load_dotenv(Path(__file__).parent / ".env")

from sqlalchemy import create_engine
import oracledb
oracledb.init_oracle_client(lib_dir=r"C:\instantclient")

from meta import engine, engine_theking, engine_spon, carregar_dados

_vpn_user = os.getenv("VPN_USER", "vpn")
_vpn_pass = os.getenv("VPN_PASSWORD", "vpn2320vpn")
engine_mg = create_engine(
    f'oracle+oracledb://{_vpn_user}:{quote_plus(_vpn_pass)}@mgon_oci',
    pool_pre_ping=True, pool_recycle=3600,
    connect_args={"expire_time": 2}
)

SCHEMAS = [
    # (schema, engine, filiais, nome_filter, estado)
    ("CRC",     engine,         "(1,2,4)", "U.NOME LIKE '%OFF TRADE%'",                               "RJ"),
    ("thekings",engine_theking, "(1,2,4)", "U.NOME LIKE '%OFF TRADE%'",                               "RJ"),
    ("SPON",    engine_spon,    None,      "(U.NOME LIKE '%OFF TRADE%' OR U.NOME LIKE '%W.S%')",       "SP"),
    ("MGON",    engine_mg,      None,      "U.NOME LIKE '%OFF TRADE%'",                               "MG"),
]


def _filial_clause(filiais):
    return f"AND C.CODFILIAL IN {filiais}" if filiais else ""


def _q_inativos(schema, filiais, nome_filter):
    s = schema.upper()
    return f"""
    SELECT C.CODCLI, C.CLIENTE, C.BAIRROENT AS BAIRRO, C.CIDADE,
           TO_CHAR(C.DTULTCOMP,  'DD/MM/YYYY') AS DTULTCOMP,
           TO_CHAR(C.DTCADASTRO, 'DD/MM/YYYY') AS DTCADASTRO,
           U.NOME AS NOME_RCA, U.CODUSUR AS RCA
    FROM {s}.PCCLIENT C
    JOIN {s}.PCUSUARI U ON C.CODUSUR2 = U.CODUSUR
    WHERE C.BLOQUEIO = 'S'
      AND C.CODUSUR1 = 10
      AND {nome_filter}
      {_filial_clause(filiais)}
    ORDER BY U.NOME, C.CLIENTE
    """


def _q_sem_compra(schema, filiais, nome_filter):
    s = schema.upper()
    return f"""
    SELECT C.CODCLI, C.CLIENTE, C.BAIRROENT AS BAIRRO, C.CIDADE,
           TO_CHAR(C.DTULTCOMP, 'DD/MM/YYYY') AS DTULTCOMP,
           TRUNC(SYSDATE) - TRUNC(C.DTULTCOMP) AS DIAS,
           U.NOME AS NOME_RCA, U.CODUSUR AS RCA
    FROM {s}.PCCLIENT C
    JOIN {s}.PCUSUARI U ON C.CODUSUR1 = U.CODUSUR
    WHERE C.BLOQUEIO = 'N'
      AND {nome_filter}
      AND C.DTULTCOMP IS NOT NULL
      AND TRUNC(SYSDATE) - TRUNC(C.DTULTCOMP) >= 30
      {_filial_clause(filiais)}
    ORDER BY U.NOME, TRUNC(SYSDATE) - TRUNC(C.DTULTCOMP) DESC
    """


def _q_novos(schema, filiais, nome_filter):
    s = schema.upper()
    return f"""
    SELECT C.CODCLI, C.CLIENTE, C.BAIRROENT AS BAIRRO, C.CIDADE,
           TO_CHAR(C.DTCADASTRO, 'DD/MM/YYYY') AS DTCADASTRO,
           TO_CHAR(C.DTULTCOMP,  'DD/MM/YYYY') AS DTULTCOMP,
           U.NOME AS NOME_RCA, U.CODUSUR AS RCA
    FROM {s}.PCCLIENT C
    JOIN {s}.PCUSUARI U ON C.CODUSUR1 = U.CODUSUR
    WHERE C.BLOQUEIO = 'N'
      AND {nome_filter}
      AND C.DTCADASTRO >= TRUNC(SYSDATE) - 90
      AND (C.DTULTCOMP IS NULL OR TRUNC(C.DTULTCOMP) < TRUNC(C.DTCADASTRO))
      {_filial_clause(filiais)}
    ORDER BY U.NOME, C.DTCADASTRO DESC
    """


def _rows(df, cols):
    if df is None or df.empty:
        return []
    df.columns = df.columns.str.upper()
    out = []
    for _, r in df.iterrows():
        out.append({c: (str(r[c]) if pd.notna(r.get(c)) else None) for c in cols})
    return out


por_vendedor = {}

for schema, eng, filiais, nome_filter, estado in SCHEMAS:
    # ── Inativos ──
    try:
        df = carregar_dados(_q_inativos(schema, filiais, nome_filter), eng, f"inativos_{schema}")
        df.columns = df.columns.str.upper()
        for _, r in df.iterrows():
            key = str(r.get("NOME_RCA", "") or "")
            rca = str(int(r["RCA"])) if pd.notna(r.get("RCA")) else ""
            v = por_vendedor.setdefault(key, {"rca": rca, "estado": estado,
                                               "inativos": [], "sem_compra": [], "novos": []})
            v["inativos"].append({
                "codcli": str(r.get("CODCLI") or ""),
                "cliente": str(r.get("CLIENTE") or ""),
                "bairro":  str(r.get("BAIRRO") or ""),
                "cidade":  str(r.get("CIDADE") or ""),
                "dtultcomp":  str(r.get("DTULTCOMP") or "") if pd.notna(r.get("DTULTCOMP")) else None,
                "dtcadastro": str(r.get("DTCADASTRO") or "") if pd.notna(r.get("DTCADASTRO")) else None,
            })
    except Exception as ex:
        print(f"[AVISO] inativos_{schema}: {str(ex)[:80]}")

    # ── Sem compra ──
    try:
        df = carregar_dados(_q_sem_compra(schema, filiais, nome_filter), eng, f"sem_compra_{schema}")
        df.columns = df.columns.str.upper()
        for _, r in df.iterrows():
            key = str(r.get("NOME_RCA", "") or "")
            rca = str(int(r["RCA"])) if pd.notna(r.get("RCA")) else ""
            v = por_vendedor.setdefault(key, {"rca": rca, "estado": estado,
                                               "inativos": [], "sem_compra": [], "novos": []})
            v["sem_compra"].append({
                "codcli":   str(r.get("CODCLI") or ""),
                "cliente":  str(r.get("CLIENTE") or ""),
                "bairro":   str(r.get("BAIRRO") or ""),
                "cidade":   str(r.get("CIDADE") or ""),
                "dtultcomp": str(r.get("DTULTCOMP") or "") if pd.notna(r.get("DTULTCOMP")) else None,
                "dias":     int(r["DIAS"]) if pd.notna(r.get("DIAS")) else 0,
            })
    except Exception as ex:
        print(f"[AVISO] sem_compra_{schema}: {str(ex)[:80]}")

    # ── Novos sem compra ──
    try:
        df = carregar_dados(_q_novos(schema, filiais, nome_filter), eng, f"novos_{schema}")
        df.columns = df.columns.str.upper()
        for _, r in df.iterrows():
            key = str(r.get("NOME_RCA", "") or "")
            rca = str(int(r["RCA"])) if pd.notna(r.get("RCA")) else ""
            v = por_vendedor.setdefault(key, {"rca": rca, "estado": estado,
                                               "inativos": [], "sem_compra": [], "novos": []})
            v["novos"].append({
                "codcli":    str(r.get("CODCLI") or ""),
                "cliente":   str(r.get("CLIENTE") or ""),
                "bairro":    str(r.get("BAIRRO") or ""),
                "cidade":    str(r.get("CIDADE") or ""),
                "dtcadastro": str(r.get("DTCADASTRO") or "") if pd.notna(r.get("DTCADASTRO")) else None,
                "dtultcomp":  str(r.get("DTULTCOMP") or "") if pd.notna(r.get("DTULTCOMP")) else None,
            })
    except Exception as ex:
        print(f"[AVISO] novos_{schema}: {str(ex)[:80]}")

payload = {
    "atualizado_em": datetime.now().strftime("%d/%m/%Y %H:%M"),
    "por_vendedor":  por_vendedor,
}

out_path = Path(__file__).parent / "clientes_inativos_data.js"
with open(out_path, "w", encoding="utf-8") as f:
    f.write("// Gerado automaticamente\n\n")
    f.write(f"const INATIVOS_DATA = {json.dumps(payload, ensure_ascii=False, indent=2)};\n")

total_vend  = len(por_vendedor)
total_inat  = sum(len(v["inativos"])   for v in por_vendedor.values())
total_sc    = sum(len(v["sem_compra"]) for v in por_vendedor.values())
total_novos = sum(len(v["novos"])      for v in por_vendedor.values())
print(f"OK clientes_inativos_data.js — {total_vend} vendedores | "
      f"{total_inat} inativos | {total_sc} sem compra | {total_novos} novos")

repo_dir = str(Path(__file__).parent)
subprocess.run(["git", "-C", repo_dir, "add", "clientes_inativos_data.js"], check=True)
subprocess.run(["git", "-C", repo_dir, "commit", "-m",
                f"Atualiza clientes_inativos_data.js - {datetime.now().strftime('%d/%m/%Y')}"], check=True)
subprocess.run(["git", "-C", repo_dir, "push", "origin", "master"], check=True)
print("OK GitHub Pages atualizado.")
