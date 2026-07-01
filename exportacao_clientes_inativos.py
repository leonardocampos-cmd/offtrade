"""
Gera clientes_inativos_data.js com:
  - inativos:        BLOQUEIO='S', CODUSUR1=10, CODUSUR2=RCA
  - sem_compra:      BLOQUEIO='N', >= 30 dias sem compra
  - novos_sem_compra: cadastrados nos últimos 90 dias sem compra
"""
import json, os, time, subprocess
import pandas as pd
from datetime import datetime
from pathlib import Path
from urllib.parse import quote_plus

from dotenv import load_dotenv
load_dotenv(Path(__file__).parent / ".env")

import oracledb
oracledb.init_oracle_client(lib_dir=r"C:\instantclient")

from sqlalchemy import create_engine, text

_crc_user = os.getenv("CRC_USER", os.getenv("VPN_USER", "vpn"))
_crc_pass = os.getenv("CRC_PASSWORD", os.getenv("VPN_PASSWORD", "vpn2320vpn"))
_vpn_user = os.getenv("VPN_USER", "vpn")
_vpn_pass = os.getenv("VPN_PASSWORD", "vpn2320vpn")

def _eng(dsn, user=None, pwd=None):
    u = user or _vpn_user
    p = pwd  or _vpn_pass
    return create_engine(
        f'oracle+oracledb://{u}:{quote_plus(p)}@{dsn}',
        pool_pre_ping=True, pool_recycle=3600,
        connect_args={"expire_time": 2}
    )

SCHEMAS = [
    # (schema_prefix, dsn, nome_filter, estado, user, pwd)
    ("CRC",     "crc_oci",     "U.NOME LIKE '%OFF TRADE%'",                             "RJ", _crc_user, _crc_pass),
    ("thekings","theking_oci", "U.NOME LIKE '%OFF TRADE%'",                             "RJ", None, None),
    ("SPON",    "spon_oci",    "(U.NOME LIKE '%OFF TRADE%' OR U.NOME LIKE '%W.S%')",    "SP", None, None),
    ("MGON",    "mgon_oci",    "U.NOME LIKE '%OFF TRADE%'",                             "MG", None, None),
]


def _q_inativos(s, nf):
    return f"""
    SELECT C.CODCLI, C.CLIENTE, C.BAIRROENT AS BAIRRO, C.MUNICENT AS CIDADE,
           TO_CHAR(C.DTULTCOMP,  'DD/MM/YYYY') AS DTULTCOMP,
           TO_CHAR(C.DTCADASTRO, 'DD/MM/YYYY') AS DTCADASTRO,
           U.NOME AS NOME_RCA, U.CODUSUR AS RCA
    FROM {s}.PCCLIENT C
    JOIN {s}.PCUSUARI U ON C.CODUSUR2 = U.CODUSUR
    WHERE C.BLOQUEIO = 'S' AND C.CODUSUR1 = 10
      AND {nf}
    ORDER BY U.NOME, C.CLIENTE
    """


def _q_sem_compra(s, nf):
    return f"""
    SELECT C.CODCLI, C.CLIENTE, C.BAIRROENT AS BAIRRO, C.MUNICENT AS CIDADE,
           TO_CHAR(C.DTULTCOMP, 'DD/MM/YYYY') AS DTULTCOMP,
           TRUNC(SYSDATE) - TRUNC(C.DTULTCOMP) AS DIAS,
           U.NOME AS NOME_RCA, U.CODUSUR AS RCA
    FROM {s}.PCCLIENT C
    JOIN {s}.PCUSUARI U ON C.CODUSUR1 = U.CODUSUR
    WHERE C.BLOQUEIO = 'N' AND {nf}
      AND C.DTULTCOMP IS NOT NULL
      AND TRUNC(SYSDATE) - TRUNC(C.DTULTCOMP) >= 30
    ORDER BY U.NOME, TRUNC(SYSDATE) - TRUNC(C.DTULTCOMP) DESC
    """


def _q_novos(s, nf):
    return f"""
    SELECT C.CODCLI, C.CLIENTE, C.BAIRROENT AS BAIRRO, C.MUNICENT AS CIDADE,
           TO_CHAR(C.DTCADASTRO, 'DD/MM/YYYY') AS DTCADASTRO,
           TO_CHAR(C.DTULTCOMP,  'DD/MM/YYYY') AS DTULTCOMP,
           U.NOME AS NOME_RCA, U.CODUSUR AS RCA
    FROM {s}.PCCLIENT C
    JOIN {s}.PCUSUARI U ON C.CODUSUR1 = U.CODUSUR
    WHERE C.BLOQUEIO = 'N' AND {nf}
      AND C.DTCADASTRO >= TRUNC(SYSDATE) - 90
      AND (C.DTULTCOMP IS NULL OR TRUNC(C.DTULTCOMP) < TRUNC(C.DTCADASTRO))
    ORDER BY U.NOME, C.DTCADASTRO DESC
    """


def _load(query, engine, label, max_try=3):
    for t in range(1, max_try + 1):
        try:
            print(f"-> {label} (tentativa {t})...")
            with engine.connect() as conn:
                df = pd.read_sql(query, conn)
            df.columns = df.columns.str.upper()
            print(f"   OK {len(df)} linhas")
            return df
        except Exception as e:
            print(f"   Erro: {str(e)[:120]}")
            if t < max_try:
                time.sleep(2)
    return pd.DataFrame()


def _row(r, cols):
    out = {}
    for c in cols:
        val = r.get(c)
        out[c.lower()] = str(val) if pd.notna(val) else None
    return out


por_vendedor = {}

for schema, dsn, nome_filter, estado, usr, pwd in SCHEMAS:
    eng = _eng(dsn, usr, pwd)

    for label, query, cat, cols in [
        (f"inativos_{schema}",   _q_inativos(schema, nome_filter),   "inativos",
         ["CODCLI","CLIENTE","BAIRRO","CIDADE","DTULTCOMP","DTCADASTRO"]),
        (f"sem_compra_{schema}", _q_sem_compra(schema, nome_filter), "sem_compra",
         ["CODCLI","CLIENTE","BAIRRO","CIDADE","DTULTCOMP","DIAS"]),
        (f"novos_{schema}",      _q_novos(schema, nome_filter),      "novos",
         ["CODCLI","CLIENTE","BAIRRO","CIDADE","DTCADASTRO","DTULTCOMP"]),
    ]:
        df = _load(query, eng, label)
        if df.empty:
            continue
        for _, r in df.iterrows():
            key = str(r.get("NOME_RCA") or "").strip()
            rca = str(int(r["RCA"])) if pd.notna(r.get("RCA")) else ""
            if not key:
                continue
            v = por_vendedor.setdefault(key, {
                "rca": rca, "estado": estado,
                "inativos": [], "sem_compra": [], "novos": []
            })
            v[cat].append(_row(r, cols))

payload = {
    "atualizado_em": datetime.now().strftime("%d/%m/%Y %H:%M"),
    "por_vendedor":  por_vendedor,
}

out_path = Path(__file__).parent / "clientes_inativos_data.js"
with open(out_path, "w", encoding="utf-8") as f:
    f.write("// Gerado automaticamente\n\n")
    f.write(f"const INATIVOS_DATA = {json.dumps(payload, ensure_ascii=False, indent=2)};\n")

ti = sum(len(v["inativos"])   for v in por_vendedor.values())
ts = sum(len(v["sem_compra"]) for v in por_vendedor.values())
tn = sum(len(v["novos"])      for v in por_vendedor.values())
print(f"\nOK clientes_inativos_data.js — {len(por_vendedor)} vendedores | "
      f"{ti} inativos | {ts} sem compra | {tn} novos")

repo_dir = str(Path(__file__).parent)
subprocess.run(["git", "-C", repo_dir, "add", "clientes_inativos_data.js"], check=True)
result = subprocess.run(["git", "-C", repo_dir, "commit", "-m",
                         f"Atualiza clientes_inativos_data.js - {datetime.now().strftime('%d/%m/%Y')}"])
if result.returncode == 0:
    subprocess.run(["git", "-C", repo_dir, "push", "origin", "master"], check=True)
    print("OK GitHub Pages atualizado.")
else:
    print("OK clientes_inativos_data.js sem alterações — push ignorado.")
