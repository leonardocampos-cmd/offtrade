import json
import os
import re
import pandas as pd
from datetime import datetime
from pathlib import Path
from meta import engine, engine_theking, engine_castas, engine_garrido, engine_spon, carregar_dados, arquivo as _arquivo_meta

DT_INI = "2026-05-25"
DT_FIM = "2026-06-25"
PREMIO_1 = 2000
PREMIO_2 = 1000

# RJ = filiais 2 e 4 na base CRC (filial 1 é ES, mesma base Oracle) — mesma
# convenção usada em exportacao_metas_gerais.py / exportacao_industria.py.
FILIAIS_RJ = "(2, 4)"

# Extrai o multiplicador de pack de descrições tipo "AMARULA 12X50ML",
# "AMARULA CREAM 6X1L" etc. — o QT do ERP conta caixas/packs, não garrafas.
_PACK_RE = re.compile(r'(\d+)\s*[xX]\s*\d')

def _pack_multiplier(descricao) -> int:
    if not descricao:
        return 1
    m = _PACK_RE.search(str(descricao))
    if not m:
        return 1
    try:
        mult = int(m.group(1))
        return mult if mult > 0 else 1
    except ValueError:
        return 1

def _query(schema, filtro_filial=FILIAIS_RJ, filtro_estent=None):
    s = schema.upper()
    extra_filial = f"\n          AND M.CODFILIAL IN {filtro_filial}" if filtro_filial else ""
    join_cli     = f"\n        JOIN {s}.PCCLIENT C ON M.CODCLI = C.CODCLI" if filtro_estent else ""
    extra_estent = f"\n          AND C.ESTENT = '{filtro_estent}'" if filtro_estent else ""
    return f"""
        SELECT
            U.NOME              AS VENDEDOR,
            M.CODCLI,
            M.DESCRICAO         AS DESCRICAO,
            SUM(M.QT)           AS QT,
            SUM(M.PUNIT * M.QT) AS FATURAMENTO
        FROM {s}.PCMOV M
        JOIN {s}.PCUSUARI U ON M.CODUSUR = U.CODUSUR{join_cli}
        WHERE TRUNC(M.DTMOV) >= TO_DATE('{DT_INI}', 'YYYY-MM-DD')
          AND TRUNC(M.DTMOV) <= TO_DATE('{DT_FIM}', 'YYYY-MM-DD')
          AND M.CODOPER = 'S'
          AND M.NUMNOTADEV IS NULL
          AND M.DTCANCEL IS NULL
          AND UPPER(M.DESCRICAO) LIKE '%AMARULA%'{extra_filial}{extra_estent}
        GROUP BY U.NOME, M.CODCLI, M.DESCRICAO
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

df_crc = carregar_dados(_query("CRC"), engine, "amarula_CRC")
df_crc['_CLI'] = "CRC_" + df_crc['CODCLI'].astype(str)

_parts_am = [df_crc]

EXCLUIR_VENDEDORES = {"RC", "VENDEDOR 09", "BEES", "VENDEDOR 02", "KELLY RAMOS - OFF TRADE", "RQ", "LOJA", "BEBIDA IN BOX"}

df = pd.concat(_parts_am, ignore_index=True)
df['QT'] = pd.to_numeric(df['QT'], errors='coerce').fillna(0)
df['FATURAMENTO'] = pd.to_numeric(df['FATURAMENTO'], errors='coerce').fillna(0)
df['MULTIPLICADOR'] = df['DESCRICAO'].apply(_pack_multiplier)
df['VOLUME'] = df['QT'] * df['MULTIPLICADOR']
df = df[df['VENDEDOR'].map(_nome).apply(lambda v: v not in EXCLUIR_VENDEDORES)].copy()

if df.empty:
    ranking_pos        = []
    ranking_vol        = []
    ranking_fat        = []
    total_vendedores   = 0
    total_positivacao  = 0
    total_volume       = 0
    total_faturamento  = 0.0
else:
    rp = (
        df.groupby('VENDEDOR')['_CLI'].nunique()
          .sort_values(ascending=False)
          .reset_index()
          .rename(columns={'_CLI': 'positivacao'})
    )
    rv = (
        df.groupby('VENDEDOR')['VOLUME'].sum()
          .sort_values(ascending=False)
          .reset_index()
          .rename(columns={'VOLUME': 'volume'})
    )
    rf = (
        df.groupby('VENDEDOR')['FATURAMENTO'].sum()
          .sort_values(ascending=False)
          .reset_index()
          .rename(columns={'FATURAMENTO': 'faturamento'})
    )
    ranking_pos = [
        {'vendedor': _nome(r['VENDEDOR']), 'valor': int(r['positivacao'])}
        for _, r in rp.iterrows()
    ]
    ranking_vol = [
        {'vendedor': _nome(r['VENDEDOR']), 'valor': int(r['volume'])}
        for _, r in rv.iterrows()
    ]
    ranking_fat = [
        {'vendedor': _nome(r['VENDEDOR']), 'valor': float(r['faturamento'])}
        for _, r in rf.iterrows()
    ]
    total_vendedores  = int(df['VENDEDOR'].nunique())
    total_positivacao = int(df['_CLI'].nunique())
    total_volume      = int(df['VOLUME'].sum())
    total_faturamento = float(df['FATURAMENTO'].sum())

payload = {
    'atualizado_em':      datetime.now().strftime('%d/%m/%Y %H:%M'),
    'periodo':            {'ini': '25/05/2026', 'fim': '25/06/2026'},
    'premio_1':           PREMIO_1,
    'premio_2':           PREMIO_2,
    'total_vendedores':   total_vendedores,
    'total_positivacao':  total_positivacao,
    'total_volume':       total_volume,
    'total_faturamento':  total_faturamento,
    'ranking_positivacao': ranking_pos,
    'ranking_volume':      ranking_vol,
    'ranking_faturamento': ranking_fat,
}

output_path = Path(__file__).parent / "amarula_data.js"
tmp_path = output_path.with_suffix(".js.tmp")
with open(tmp_path, 'w', encoding='utf-8') as f:
    f.write(f"const AMARULA_DATA = {json.dumps(payload, ensure_ascii=False, indent=2)};\n")
os.replace(tmp_path, output_path)

print(f"OK amarula_data.js — {total_vendedores} vendedores, {len(df)} linhas Amarula")

import subprocess
repo_dir = str(Path(__file__).parent)
subprocess.run(["git", "-C", repo_dir, "add", "amarula_data.js"], check=False)
subprocess.run(["git", "-C", repo_dir, "commit", "-m",
                f"Atualiza amarula_data.js - {datetime.now().strftime('%d/%m/%Y')}"])
subprocess.run(["git", "-C", repo_dir, "push", "origin", "master"], check=False)
print("OK GitHub Pages atualizado.")