# exportacao_nao_pos_es.py — Clientes nao positivados ES (CRC filial 2)
import json, re
import pandas as pd
import oracledb
from sqlalchemy import create_engine
from datetime import datetime
from pathlib import Path

import os
from urllib.parse import quote_plus
from dotenv import load_dotenv
load_dotenv(Path(__file__).parent / ".env")

from utils import ORACLE_LIB, git_commit_push
# meta.py já chama oracledb.init_oracle_client() — importar antes evita o erro
# "Oracle Client library has already been initialized".
from meta import _com_timeout_forcado

_crc_user = os.environ["CRC_USER"]
_crc_pass = os.environ["CRC_PASSWORD"]

engine_es = create_engine(
    f'oracle+oracledb://{_crc_user}:{quote_plus(_crc_pass)}@crc_oci',
    pool_pre_ping=True, pool_recycle=3600,
    connect_args={"expire_time": 2}
)

QUERY = """
SELECT
    C.CODCLI,
    C.CLIENTE,
    C.BAIRROENT,
    TO_CHAR(LV.DTULTCOMP, 'DD/MM/YYYY') AS DTULTCOMP,
    P.DESCRICAO  AS PRODUTO,
    F.FANTASIA,
    M.QT,
    (M.PUNIT * M.QT) AS VALOR,
    U.NOME AS NOME_RCA
FROM CRC.PBI_PCCLIENT C
LEFT JOIN (
    SELECT CODCLI,
           MAX(DTMOV)   AS DTULTCOMP,
           MAX(NUMNOTA) KEEP (DENSE_RANK LAST ORDER BY DTMOV) AS NUMNOTA
    FROM CRC.PCMOV
    WHERE CODOPER = 'S' AND NUMNOTADEV IS NULL AND DTCANCEL IS NULL
      AND CODFILIAL = 2
    GROUP BY CODCLI
) LV ON C.CODCLI = LV.CODCLI
LEFT JOIN CRC.PCMOV M
       ON M.CODCLI    = LV.CODCLI
      AND M.NUMNOTA   = LV.NUMNOTA
      AND M.CODOPER   = 'S'
      AND M.NUMNOTADEV IS NULL
      AND M.DTCANCEL  IS NULL
      AND M.CODFILIAL = 2
LEFT JOIN CRC.PCPRODUT P  ON M.CODPROD   = P.CODPROD
LEFT JOIN CRC.PCFORNEC F  ON P.CODFORNEC = F.CODFORNEC
LEFT JOIN CRC.PCUSUARI U  ON C.RCA       = U.CODUSUR
WHERE U.NOME LIKE '%OFF TRADE%'
  AND C.CODCLI NOT IN (
      SELECT DISTINCT CODCLI FROM CRC.PCMOV
      WHERE TRUNC(DTMOV, 'MM') = TRUNC(SYSDATE, 'MM')
        AND CODOPER = 'S' AND NUMNOTADEV IS NULL AND DTCANCEL IS NULL
        AND CODFILIAL = 2
  )
ORDER BY U.NOME, LV.DTULTCOMP ASC, C.CLIENTE, P.DESCRICAO
"""

print("-> Consultando nao positivados ES (CRC filial 2)...")
def _fazer_query():
    with engine_es.connect() as conn:
        return pd.read_sql(QUERY, conn)
df = _com_timeout_forcado(_fazer_query, 90)
df.columns  = df.columns.str.upper()
df['QT']    = pd.to_numeric(df['QT'],    errors='coerce').fillna(0).astype(int)
df['VALOR'] = pd.to_numeric(df['VALOR'], errors='coerce').fillna(0).round(2)
df['FANTASIA'] = df['FANTASIA'].fillna('').str.upper()
df['PRODUTO']  = df['PRODUTO'].fillna('')

print(f"OK {df['CODCLI'].nunique()} clientes nao positivados ES")

_RE = re.compile(r'\s*-?\s*OFF\s*TRADE\s*', re.IGNORECASE)

def _limpar_nome(nome):
    return _RE.sub(' ', nome or '').strip().upper()

por_vendedor: dict = {}
_cli_idx: dict = {}

for _, row in df.iterrows():
    v = _limpar_nome(str(row['NOME_RCA']))
    if not v:
        continue
    codcli = str(row['CODCLI'])
    chave  = (v, codcli)
    if v not in por_vendedor:
        por_vendedor[v] = []
    if chave not in _cli_idx:
        _cli_idx[chave] = len(por_vendedor[v])
        por_vendedor[v].append({
            'codcli':    codcli,
            'cliente':   str(row['CLIENTE']   or ''),
            'bairro':    str(row['BAIRROENT'] or ''),
            'dtultcomp': str(row['DTULTCOMP'] or ''),
            'produtos':  [],
        })
    if row['PRODUTO']:
        idx = _cli_idx[chave]
        por_vendedor[v][idx]['produtos'].append({
            'produto':  str(row['PRODUTO']),
            'fantasia': str(row['FANTASIA']),
            'qt':       int(row['QT']),
            'valor':    float(row['VALOR']),
        })

payload = {
    'atualizado_em': datetime.now().strftime('%d/%m/%Y %H:%M'),
    'por_vendedor':  por_vendedor,
}

out_path = Path(__file__).parent / "nao_pos_es_data.js"
out_path.write_text(
    f"// Gerado automaticamente pelo exportacao_nao_pos_es.py\n\n"
    f"const NAO_POS_ES_DATA = {json.dumps(payload, ensure_ascii=False, indent=2)};\n",
    encoding='utf-8'
)

n_cli = sum(len(v) for v in por_vendedor.values())
print(f"OK nao_pos_es_data.js — {len(por_vendedor)} vendedores, {n_cli} clientes")

git_commit_push(["nao_pos_es_data.js"],
                f"Atualiza nao_pos_es_data.js - {datetime.now().strftime('%d/%m/%Y')}")
