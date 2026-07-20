# exportacao_588.py — Clientes do RCA 588 que migraram para outro vendedor em Maio/26
import json
import os
import time
import pandas as pd
import oracledb
from sqlalchemy import create_engine
from datetime import datetime, date
from pathlib import Path
from urllib.parse import quote_plus
from dotenv import load_dotenv
load_dotenv(Path(__file__).parent / ".env")

from utils import ORACLE_LIB
oracledb.init_oracle_client(lib_dir=ORACLE_LIB)

user     = os.environ["SPON_USER"]
password = os.environ["SPON_PASSWORD"]

engine_spon = create_engine(
    f'oracle+oracledb://{user}:{quote_plus(password)}@spon_oci',
    pool_pre_ping=True, pool_recycle=3600,
    connect_args={"expire_time": 2}
)

RCA_REF = 588

def carregar(query, nome, max_tent=5):
    for t in range(1, max_tent + 1):
        try:
            print(f"-> Lendo {nome} (Tentativa {t}/{max_tent})...")
            with engine_spon.connect() as conn:
                chunks = [c for c in pd.read_sql(query, con=conn, chunksize=5000)]
                df = pd.concat(chunks, ignore_index=True)
                df.columns = df.columns.str.strip().str.upper()
                print(f"OK {nome} carregada!")
                return df
        except Exception as e:
            print(f"Erro na {nome}: {str(e)[:100]}")
            engine_spon.dispose()
            if t < max_tent:
                time.sleep(10)
            else:
                raise e

# ── Clientes ativos com RCA 588 de jan a abr ─────────────────────────────────

Q_FIDELIDADE = f"""
    SELECT
        M.CODCLI,
        C.CLIENTE,
        MAX(M.DTMOV)                        AS ULTIMA_COMPRA_RCA,
        ROUND(SUM(M.PUNIT * M.QT), 2)      AS VALOR_JAN_ABR,
        COUNT(DISTINCT TRUNC(M.DTMOV,'MM')) AS MESES_ATIVOS
    FROM SPON.PCMOV M
    LEFT JOIN SPON.PCCLIENT C ON M.CODCLI = C.CODCLI
    WHERE M.CODUSUR = {RCA_REF}
      AND M.DTMOV >= DATE '2026-01-01'
      AND M.DTMOV <  DATE '2026-05-01'
      AND M.CODOPER IN ('S','SB')
      AND M.NUMNOTADEV IS NULL
      AND M.DTCANCEL  IS NULL
    GROUP BY M.CODCLI, C.CLIENTE
"""

# ── Vendas de maio de outros RCAs ────────────────────────────────────────────

Q_MAIO_OUTROS = f"""
    SELECT
        M.CODCLI,
        TO_CHAR(M.DTMOV, 'DD/MM/YYYY') AS DATA,
        M.DESCRICAO                      AS PRODUTO,
        NVL(F.FANTASIA, '')              AS FANTASIA,
        M.CODUSUR,
        U.NOME                           AS VENDEDOR,
        ROUND(M.PUNIT * M.QT, 2)        AS VALOR
    FROM SPON.PCMOV M
    JOIN SPON.PCUSUARI U  ON M.CODUSUR   = U.CODUSUR
    JOIN SPON.PCPRODUT P  ON M.CODPROD   = P.CODPROD
    JOIN SPON.PCFORNEC F  ON P.CODFORNEC = F.CODFORNEC
    WHERE M.DTMOV >= DATE '2026-05-01'
      AND M.DTMOV <  DATE '2026-06-01'
      AND M.CODUSUR != {RCA_REF}
      AND M.CODOPER IN ('S','SB')
      AND M.NUMNOTADEV IS NULL
      AND M.DTCANCEL  IS NULL
"""

# ── Vendas de maio do próprio RCA 588 (para marcar clientes híbridos) ─────────

Q_MAIO_588 = f"""
    SELECT DISTINCT M.CODCLI
    FROM SPON.PCMOV M
    WHERE M.DTMOV >= DATE '2026-05-01'
      AND M.DTMOV <  DATE '2026-06-01'
      AND M.CODUSUR = {RCA_REF}
      AND M.CODOPER IN ('S','SB')
      AND M.NUMNOTADEV IS NULL
      AND M.DTCANCEL  IS NULL
"""

df_fid        = carregar(Q_FIDELIDADE,   "clientes_588_jan_abr")
df_maio_out   = carregar(Q_MAIO_OUTROS,  "vendas_maio_outros")
df_maio_588   = carregar(Q_MAIO_588,     "vendas_maio_588")

# ── Cruza: apenas clientes que compraram de outros E estavam com 588 ─────────

codclis_fid  = set(df_fid['CODCLI'].dropna().unique())
codclis_588_mai = set(df_maio_588['CODCLI'].dropna().unique())

EXCLUIR_VENDEDORES = {"RC", "VENDEDOR 09", "BEES"}

df_migrados = df_maio_out[
    df_maio_out['CODCLI'].isin(codclis_fid) &
    ~df_maio_out['VENDEDOR'].isin(EXCLUIR_VENDEDORES)
].copy()
df_migrados['VALOR'] = pd.to_numeric(df_migrados['VALOR'], errors='coerce').fillna(0).round(2)

df_fid['VALOR_JAN_ABR']     = pd.to_numeric(df_fid['VALOR_JAN_ABR'],     errors='coerce').fillna(0).round(2)
df_fid['MESES_ATIVOS']      = pd.to_numeric(df_fid['MESES_ATIVOS'],      errors='coerce').fillna(0).astype(int)
df_fid['ULTIMA_COMPRA_RCA'] = pd.to_datetime(df_fid['ULTIMA_COMPRA_RCA'], errors='coerce')
df_fid_idx = df_fid.set_index('CODCLI')

# ── Monta estrutura por cliente ───────────────────────────────────────────────

clientes_out = []
for codcli, grp in df_migrados.groupby('CODCLI'):
    fid = df_fid_idx.loc[codcli]

    # Agrupa compras de maio por vendedor
    por_vendedor = []
    for vend, vgrp in grp.groupby('VENDEDOR'):
        itens = vgrp[['DATA','PRODUTO','FANTASIA','VALOR']].sort_values('DATA').to_dict('records')
        for i in itens:
            i['VALOR'] = float(i['VALOR'])
        por_vendedor.append({
            'vendedor':   str(vend),
            'codusur':    str(vgrp['CODUSUR'].iloc[0]),
            'total':      round(float(vgrp['VALOR'].sum()), 2),
            'itens':      itens,
        })
    por_vendedor.sort(key=lambda x: x['total'], reverse=True)

    clientes_out.append({
        'codcli':            str(codcli),
        'cliente':           str(fid['CLIENTE']) if pd.notna(fid['CLIENTE']) else str(codcli),
        'ultima_compra_rca': fid['ULTIMA_COMPRA_RCA'].strftime('%d/%m/%Y') if pd.notna(fid['ULTIMA_COMPRA_RCA']) else '',
        'valor_jan_abr':     float(fid['VALOR_JAN_ABR']),
        'meses_ativos':      int(fid['MESES_ATIVOS']),
        'tambem_comprou_588': codcli in codclis_588_mai,
        'total_mai_outros':  round(float(grp['VALOR'].sum()), 2),
        'por_vendedor':      por_vendedor,
    })

clientes_out.sort(key=lambda x: x['total_mai_outros'], reverse=True)

payload = {
    'atualizado_em':      datetime.now().strftime('%d/%m/%Y %H:%M'),
    'rca_ref':            RCA_REF,
    'periodo_fid':        'Jan/26 – Abr/26',
    'mes_migracao':       'Mai/26',
    'total_clientes':     len(clientes_out),
    'valor_total_outros': round(sum(c['total_mai_outros'] for c in clientes_out), 2),
    'clientes':           clientes_out,
}

out_path = Path(__file__).parent / "clientes_588_data.js"
with open(out_path, 'w', encoding='utf-8') as f:
    f.write(f"const CLIENTES_588_DATA = {json.dumps(payload, ensure_ascii=False, indent=2)};\n")

print(f"OK clientes_588_data.js — {len(clientes_out)} clientes migraram")

import subprocess
repo_dir = str(Path(__file__).parent)
subprocess.run(["git", "-C", repo_dir, "add", "clientes_588_data.js", "clientes_588.html"], check=False)
subprocess.run(["git", "-C", repo_dir, "commit", "-m",
                f"Atualiza clientes_588_data.js - {date.today().strftime('%d/%m/%Y')}"])
subprocess.run(["git", "-C", repo_dir, "push", "origin", "master"], check=False)
print("OK GitHub Pages atualizado.")
