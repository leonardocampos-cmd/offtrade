import json
import pandas as pd
from datetime import datetime
from pathlib import Path
from meta import engine, engine_theking, carregar_dados, arquivo as _arquivo_meta

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

# Monta mapeamento nome Oracle → nome display (igual metas_data.js)
_map_rca = pd.concat([
    carregar_dados("SELECT CODUSUR AS RCA, NOME FROM CRC.PCUSUARI WHERE NOME LIKE '%OFF TRADE%'",      engine,         "map_rca_CRC"),
    carregar_dados("SELECT CODUSUR AS RCA, NOME FROM thekings.PCUSUARI WHERE NOME LIKE '%OFF TRADE%'", engine_theking, "map_rca_TK"),
], ignore_index=True)
_map_rca['RCA'] = pd.to_numeric(_map_rca['RCA'], errors='coerce')
_arq = _arquivo_meta[['RCA', 'VENDEDOR']].dropna(subset=['RCA', 'VENDEDOR']).drop_duplicates('RCA').copy()
_arq['RCA'] = pd.to_numeric(_arq['RCA'], errors='coerce')
_merged = _map_rca.merge(_arq, on='RCA', how='left')
_oracle_to_display = {
    str(r['NOME']): str(r['VENDEDOR'])
    for _, r in _merged.iterrows()
    if pd.notna(r.get('VENDEDOR')) and str(r.get('VENDEDOR')) not in ('nan', '')
}

def _nome(oracle):
    return _oracle_to_display.get(str(oracle), str(oracle))

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
        {'vendedor': _nome(r['VENDEDOR']), 'valor': int(r['positivacao'])}
        for _, r in rp.iterrows()
    ]
    ranking_fat = [
        {'vendedor': _nome(r['VENDEDOR']), 'valor': float(r['faturamento'])}
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
