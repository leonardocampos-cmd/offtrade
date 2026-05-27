import json
import pandas as pd
from datetime import datetime
from pathlib import Path
from meta import engine, engine_theking, carregar_dados

DT_INI = "2026-05-25"
DT_FIM = "2026-06-25"
PREMIO = 3000

def _query(schema):
    s = schema.upper()
    return f"""
        SELECT
            U.NOME           AS VENDEDOR,
            M.CODCLI,
            (M.PUNIT * M.QT) AS VALOR
        FROM {s}.PCMOV M
        JOIN {s}.PCUSUARI U ON M.CODUSUR = U.CODUSUR
        WHERE TRUNC(M.DTMOV) >= TO_DATE('{DT_INI}', 'YYYY-MM-DD')
          AND TRUNC(M.DTMOV) <= TO_DATE('{DT_FIM}', 'YYYY-MM-DD')
          AND M.CODOPER = 'S'
          AND M.CODFILIAL IN (1, 2, 4)
          AND M.NUMNOTADEV IS NULL
          AND M.DTCANCEL IS NULL
          AND UPPER(M.DESCRICAO) LIKE '%AMARULA%'
    """

df_crc      = carregar_dados(_query("CRC"),      engine,         "amarula_CRC")
df_theking  = carregar_dados(_query("thekings"), engine_theking, "amarula_thekings")

# Distingue CODCLI por origem para evitar colisão de IDs entre sistemas
df_crc['_CLI']     = "CRC_"     + df_crc['CODCLI'].astype(str)
df_theking['_CLI'] = "TK_"      + df_theking['CODCLI'].astype(str)

df = pd.concat([df_crc, df_theking], ignore_index=True)
df['VALOR'] = pd.to_numeric(df['VALOR'], errors='coerce').fillna(0)

if df.empty:
    ranking_pos       = []
    ranking_fat       = []
    total_vendedores  = 0
    total_positivacao = 0
    total_faturamento = 0.0
else:
    rp = (
        df.groupby('VENDEDOR')['_CLI'].nunique()
          .sort_values(ascending=False)
          .reset_index()
          .rename(columns={'_CLI': 'positivacao'})
    )
    rf = (
        df.groupby('VENDEDOR')['VALOR'].sum()
          .round(2)
          .sort_values(ascending=False)
          .reset_index()
          .rename(columns={'VALOR': 'faturamento'})
    )
    ranking_pos = [
        {'vendedor': r['VENDEDOR'], 'valor': int(r['positivacao'])}
        for _, r in rp.iterrows()
    ]
    ranking_fat = [
        {'vendedor': r['VENDEDOR'], 'valor': float(r['faturamento'])}
        for _, r in rf.iterrows()
    ]
    total_vendedores  = int(df['VENDEDOR'].nunique())
    total_positivacao = int(df['_CLI'].nunique())
    total_faturamento = round(float(df['VALOR'].sum()), 2)

payload = {
    'atualizado_em':      datetime.now().strftime('%d/%m/%Y %H:%M'),
    'periodo':            {'ini': '25/05/2026', 'fim': '25/06/2026'},
    'premio':             PREMIO,
    'total_vendedores':   total_vendedores,
    'total_positivacao':  total_positivacao,
    'total_faturamento':  total_faturamento,
    'ranking_positivacao': ranking_pos,
    'ranking_faturamento': ranking_fat,
}

output_path = Path(__file__).parent / "amarula_data.js"
with open(output_path, 'w', encoding='utf-8') as f:
    f.write(f"const AMARULA_DATA = {json.dumps(payload, ensure_ascii=False, indent=2)};\n")

print(f"OK amarula_data.js — {total_vendedores} vendedores, {len(df)} linhas Amarula")

import subprocess
repo_dir = str(Path(__file__).parent)
subprocess.run(["git", "-C", repo_dir, "add", "amarula_data.js"], check=False)
subprocess.run(["git", "-C", repo_dir, "commit", "-m",
                f"Atualiza amarula_data.js - {datetime.now().strftime('%d/%m/%Y')}"])
subprocess.run(["git", "-C", repo_dir, "push", "origin", "master"], check=False)
print("OK GitHub Pages atualizado.")
