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
from utils import ORACLE_LIB
oracledb.init_oracle_client(lib_dir=ORACLE_LIB)

from sqlalchemy import create_engine, text

_crc_user = os.environ["CRC_USER"]
_crc_pass = os.environ["CRC_PASSWORD"]
_vpn_user = os.environ["VPN_USER"]
_vpn_pass = os.environ["VPN_PASSWORD"]

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


def _q_media(s, nf):
    return f"""
    SELECT M.CODCLI,
           ROUND(SUM(M.PUNIT * M.QT) / 3, 2) AS MEDIA_MENSAL
    FROM {s}.PCMOV M
    JOIN {s}.PCUSUARI U ON M.CODUSUR = U.CODUSUR
    WHERE TRUNC(M.DTMOV, 'MM') >= ADD_MONTHS(TRUNC(SYSDATE, 'MM'), -3)
      AND TRUNC(M.DTMOV, 'MM') <  TRUNC(SYSDATE, 'MM')
      AND M.CODOPER IN ('S', 'SB')
      AND M.NUMNOTADEV IS NULL
      AND M.DTCANCEL  IS NULL
      AND {nf}
    GROUP BY M.CODCLI
    """


def _q_inativos(s, nf):
    return f"""
    SELECT C.CODCLI, C.CLIENTE, C.BAIRROENT AS BAIRRO, C.MUNICENT AS CIDADE,
           TO_CHAR(C.DTULTCOMP,  'DD/MM/YYYY') AS DTULTCOMP,
           TRUNC(SYSDATE) - TRUNC(C.DTULTCOMP) AS DIAS,
           U.NOME AS NOME_RCA, U.CODUSUR AS RCA
    FROM {s}.PCCLIENT C
    JOIN {s}.PCUSUARI U ON C.CODUSUR2 = U.CODUSUR
    WHERE C.BLOQUEIO = 'S' AND C.CODUSUR1 = 10
      AND {nf}
    ORDER BY U.NOME, C.CLIENTE
    """


def _q_media_inativos(s, nf):
    """Média mensal dos INATIVOS não pode ser relativa a hoje (por definição
    eles não compraram nos últimos meses, daria sempre 0) — usa os 3 meses
    que terminam no mês da última compra de cada cliente, ou seja, quanto
    ele gastava por mês quando ainda estava ativo."""
    return f"""
    SELECT M.CODCLI, ROUND(SUM(M.PUNIT * M.QT) / 3, 2) AS MEDIA_MENSAL
    FROM {s}.PCMOV M
    JOIN {s}.PCUSUARI U ON M.CODUSUR = U.CODUSUR
    JOIN {s}.PCCLIENT C ON M.CODCLI = C.CODCLI
    WHERE M.DTMOV >= ADD_MONTHS(TRUNC(C.DTULTCOMP, 'MM'), -2)
      AND M.DTMOV <  ADD_MONTHS(TRUNC(C.DTULTCOMP, 'MM'), 1)
      AND M.CODOPER IN ('S', 'SB')
      AND M.NUMNOTADEV IS NULL
      AND M.DTCANCEL  IS NULL
      AND C.BLOQUEIO = 'S' AND C.CODUSUR1 = 10
      AND {nf}
    GROUP BY M.CODCLI
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
        if not pd.notna(val):
            out[c.lower()] = None
        elif c == "DIAS":
            out[c.lower()] = int(val)
        else:
            out[c.lower()] = str(val)
    return out


por_vendedor = {}
_media_map          = {}  # {schema: {codcli_str: media_mensal}} — 3 meses relativos a hoje
_media_map_inativos = {}  # {schema: {codcli_str: media_mensal}} — 3 meses relativos à última compra

for schema, dsn, nome_filter, estado, usr, pwd in SCHEMAS:
    eng = _eng(dsn, usr, pwd)

    # Média 3 meses por cliente (relativa a hoje — usada em sem_compra/novos)
    df_med = _load(_q_media(schema, nome_filter), eng, f"media_{schema}")
    if not df_med.empty:
        _media_map[schema] = {
            str(int(float(r["CODCLI"]))): round(float(r["MEDIA_MENSAL"]), 2)
            for _, r in df_med.iterrows()
            if pd.notna(r.get("MEDIA_MENSAL")) and pd.notna(r.get("CODCLI"))
        }
    else:
        _media_map[schema] = {}

    # Média 3 meses dos inativos (relativa à última compra de cada um — ver
    # docstring de _q_media_inativos)
    df_med_inat = _load(_q_media_inativos(schema, nome_filter), eng, f"media_inativos_{schema}")
    if not df_med_inat.empty:
        _media_map_inativos[schema] = {
            str(int(float(r["CODCLI"]))): round(float(r["MEDIA_MENSAL"]), 2)
            for _, r in df_med_inat.iterrows()
            if pd.notna(r.get("MEDIA_MENSAL")) and pd.notna(r.get("CODCLI"))
        }
    else:
        _media_map_inativos[schema] = {}

    for label, query, cat, cols, mapa_media in [
        (f"inativos_{schema}",   _q_inativos(schema, nome_filter),   "inativos",
         ["CODCLI","CLIENTE","BAIRRO","CIDADE","DTULTCOMP","DIAS"], _media_map_inativos),
        (f"sem_compra_{schema}", _q_sem_compra(schema, nome_filter), "sem_compra",
         ["CODCLI","CLIENTE","BAIRRO","CIDADE","DTULTCOMP","DIAS"], _media_map),
        (f"novos_{schema}",      _q_novos(schema, nome_filter),      "novos",
         ["CODCLI","CLIENTE","BAIRRO","CIDADE","DTCADASTRO","DTULTCOMP"], _media_map),
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
            entry = _row(r, cols)
            codcli_raw = r.get("CODCLI")
            codcli_key = str(int(float(codcli_raw))) if pd.notna(codcli_raw) else ""
            entry["media"] = mapa_media.get(schema, {}).get(codcli_key, 0.0)
            v[cat].append(entry)

# ── Hierarquia (estado → gerente → supervisor → vendedor) ────────────────────
def _q_hier(s, nf):
    return f"""
    SELECT U.CODUSUR, U.NOME AS NOME_VENDEDOR, U.ESTADO AS ESTADO_VENDEDOR,
           S.CODSUPERVISOR, S.NOME AS NOME_SUPERVISOR,
           G.CODGERENTE, G.NOMEGERENTE
    FROM {s}.PCUSUARI U
    LEFT JOIN {s}.PCSUPERV  S ON U.CODSUPERVISOR = S.CODSUPERVISOR
    LEFT JOIN {s}.PCGERENTE G ON S.CODGERENTE     = G.CODGERENTE
    WHERE {nf} AND U.BLOQUEIO = 'N'
    """

_hier_emp = {}
for schema, dsn, nome_filter, estado, usr, pwd in SCHEMAS:
    eng = _eng(dsn, usr, pwd)
    df  = _load(_q_hier(schema, nome_filter), eng, f"hier_{schema}")
    if df.empty:
        continue
    for _, r in df.iterrows():
        codusur = str(r.get("CODUSUR") or "").strip()
        nome_v  = str(r.get("NOME_VENDEDOR") or "").strip()
        est_v   = str(r.get("ESTADO_VENDEDOR") or estado or "").strip()
        ger     = str(r.get("NOMEGERENTE")    or "").strip() or "Sem Gerente"
        sup     = str(r.get("NOME_SUPERVISOR") or "").strip() or "Sem Supervisor"
        if not nome_v:
            continue
        est_key = est_v or estado or "Sem Estado"
        e_ = _hier_emp.setdefault(est_key, {"nome": est_key, "gerentes": {}})
        g_ = e_["gerentes"].setdefault(ger, {"nome": ger, "supervisores": {}})
        s_ = g_["supervisores"].setdefault(sup, {"nome": sup, "vendedores": {}})
        s_["vendedores"][nome_v] = {"nome": nome_v, "rca": codusur}

hierarquia = []
for est_data in sorted(_hier_emp.values(), key=lambda x: x["nome"]):
    gers_out = []
    for g_data in sorted(est_data["gerentes"].values(), key=lambda x: x["nome"]):
        sups_out = []
        for s_data in sorted(g_data["supervisores"].values(), key=lambda x: x["nome"]):
            vends_out = sorted(s_data["vendedores"].values(), key=lambda x: x["nome"])
            sups_out.append({"nome": s_data["nome"], "vendedores": vends_out})
        gers_out.append({"nome": g_data["nome"], "supervisores": sups_out})
    hierarquia.append({"nome": est_data["nome"], "gerentes": gers_out})

total_hier = sum(len(s["vendedores"]) for e in hierarquia for g in e["gerentes"] for s in g["supervisores"])
print(f"Hierarquia: {len(hierarquia)} estados, {total_hier} vendedores")

payload = {
    "atualizado_em": datetime.now().strftime("%d/%m/%Y %H:%M"),
    "por_vendedor":  por_vendedor,
    "hierarquia":    hierarquia,
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
