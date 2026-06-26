import json
import pandas as pd
from datetime import datetime
from pathlib import Path
from meta import engine, engine_theking, engine_castas, engine_garrido, engine_spon, carregar_dados, arquivo as _arquivo_meta

DT_INI = "2026-05-25"
DT_FIM = "2026-06-25"
PREMIO_1 = 2000
PREMIO_2 = 1000

def _query(schema, filtro_filial="(1, 2, 4)", filtro_estent=None):
    s = schema.upper()
    extra_filial = f"\n          AND M.CODFILIAL IN {filtro_filial}" if filtro_filial else ""
    join_cli     = f"\n        JOIN {s}.PCCLIENT C ON M.CODCLI = C.CODCLI" if filtro_estent else ""
    extra_estent = f"\n          AND C.ESTENT = '{filtro_estent}'" if filtro_estent else ""
    return f"""
        SELECT
            U.NOME           AS VENDEDOR,
            M.CODCLI,
            (M.PUNIT * M.QT) AS VALOR
        FROM {s}.PCMOV M
        JOIN {s}.PCUSUARI U ON M.CODUSUR = U.CODUSUR{join_cli}
        WHERE TRUNC(M.DTMOV) >= TO_DATE('{DT_INI}', 'YYYY-MM-DD')
          AND TRUNC(M.DTMOV) <= TO_DATE('{DT_FIM}', 'YYYY-MM-DD')
          AND M.CODOPER = 'S'
          AND M.NUMNOTADEV IS NULL
          AND M.DTCANCEL IS NULL
          AND UPPER(M.DESCRICAO) LIKE '%AMARULA%'{extra_filial}{extra_estent}
    """

# Monta mapeamento nome Oracle → nome display (igual metas_data.js)
_parts_map_rca_am = [
    carregar_dados("SELECT CODUSUR AS RCA, NOME FROM CRC.PCUSUARI WHERE NOME LIKE '%OFF TRADE%'",      engine,         "map_rca_CRC"),
    carregar_dados("SELECT CODUSUR AS RCA, NOME FROM thekings.PCUSUARI WHERE NOME LIKE '%OFF TRADE%'", engine_theking, "map_rca_TK"),
]
for _s, _e, _n in [("CASTAS", engine_castas, "map_rca_CASTAS"), ("GARRIDO", engine_garrido, "map_rca_GARRIDO"), ("SPON", engine_spon, "map_rca_SPON")]:
    try:
        _parts_map_rca_am.append(carregar_dados(f"SELECT CODUSUR AS RCA, NOME FROM {_s}.PCUSUARI WHERE NOME LIKE '%OFF TRADE%'", _e, _n))
    except Exception as _ex:
        print(f"[AVISO] {_n} falhou — ignorado")
_map_rca = pd.concat(_parts_map_rca_am, ignore_index=True)
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

df_crc['_CLI']     = "CRC_" + df_crc['CODCLI'].astype(str)
df_theking['_CLI'] = "TK_"  + df_theking['CODCLI'].astype(str)

_parts_am = [df_crc, df_theking]
for _s, _e, _pfx, _fe, _ff in [
    ("CASTAS", engine_castas,  "CASTAS_", None, None),
    ("GARRIDO", engine_garrido, "GARRIDO_", None, None),
    ("SPON",    engine_spon,    "SPON_",    None, None),
]:
    try:
        _df_tmp = carregar_dados(_query(_s, filtro_filial=_ff, filtro_estent=_fe), _e, f"amarula_{_s}")
        _df_tmp['_CLI'] = _pfx + _df_tmp['CODCLI'].astype(str)
        _parts_am.append(_df_tmp)
    except Exception as _ex:
        print(f"[AVISO] amarula_{_s} falhou ({str(_ex)[:80]}) — ignorado")

EXCLUIR_VENDEDORES = {"RC", "VENDEDOR 09", "BEES", "VENDEDOR 02", "KELLY RAMOS - OFF TRADE", "RQ", "LOJA", "BEBIDA IN BOX"}

df = pd.concat(_parts_am, ignore_index=True)
df['VALOR'] = pd.to_numeric(df['VALOR'], errors='coerce').fillna(0)
df = df[df['VENDEDOR'].map(_nome).apply(lambda v: v not in EXCLUIR_VENDEDORES)].copy()

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
    'premio_1':           PREMIO_1,
    'premio_2':           PREMIO_2,
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
