# exportacao_nao_pos_sp.py — gera nao_pos_sp_data.js com clientes e produtos da última compra
import json, re, os
import pandas as pd
import oracledb
from sqlalchemy import create_engine
from datetime import datetime
from pathlib import Path
import subprocess, sys

from utils import ORACLE_LIB
oracledb.init_oracle_client(lib_dir=ORACLE_LIB)

user     = os.environ["SPON_USER"]
password = os.environ["SPON_PASSWORD"]
dsn_sp   = "spon_oci"

engine_sp = create_engine(
    f'oracle+oracledb://{user}:{password}@{dsn_sp}',
    pool_pre_ping=True,
    pool_recycle=3600,
    connect_args={"expire_time": 2}
)

# Busca clientes não positivados + produtos da última nota
QUERY = """
SELECT
    C.CODCLI,
    C.CLIENTE,
    C.BAIRROENT,
    TO_CHAR(LV.DTULTCOMP, 'DD/MM/YYYY') AS DTULTCOMP,
    P.DESCRICAO AS PRODUTO,
    F.FANTASIA,
    M.QT,
    (M.PUNIT * M.QT) AS VALOR,
    U.NOME AS NOME_RCA
FROM SPON.PBI_PCCLIENT C
LEFT JOIN (
    SELECT CODCLI,
           MAX(DTMOV)   AS DTULTCOMP,
           MAX(NUMNOTA) KEEP (DENSE_RANK LAST ORDER BY DTMOV) AS NUMNOTA
    FROM SPON.PCMOV
    WHERE CODOPER = 'S' AND NUMNOTADEV IS NULL AND DTCANCEL IS NULL
    GROUP BY CODCLI
) LV ON C.CODCLI = LV.CODCLI
LEFT JOIN SPON.PCMOV M
       ON M.CODCLI = LV.CODCLI AND M.NUMNOTA = LV.NUMNOTA
      AND M.CODOPER = 'S' AND M.NUMNOTADEV IS NULL AND M.DTCANCEL IS NULL
LEFT JOIN SPON.PCPRODUT P  ON M.CODPROD   = P.CODPROD
LEFT JOIN SPON.PCFORNEC F  ON P.CODFORNEC = F.CODFORNEC
LEFT JOIN SPON.PCUSUARI U  ON C.RCA       = U.CODUSUR
WHERE (U.NOME LIKE '%OFF TRADE%' OR U.NOME = 'W.S')
  AND C.CODCLI NOT IN (
      SELECT DISTINCT CODCLI FROM SPON.PCMOV
      WHERE TRUNC(DTMOV, 'MM') = TRUNC(SYSDATE, 'MM')
        AND CODOPER = 'S' AND NUMNOTADEV IS NULL AND DTCANCEL IS NULL
  )
ORDER BY U.NOME, LV.DTULTCOMP ASC, C.CLIENTE, P.DESCRICAO
"""

print("-> Consultando não positivados SP (SPON)...")
with engine_sp.connect() as conn:
    df = pd.read_sql(QUERY, conn)
df.columns = df.columns.str.upper()
df['QT']    = pd.to_numeric(df['QT'],    errors='coerce').fillna(0).astype(int)
df['VALOR'] = pd.to_numeric(df['VALOR'], errors='coerce').fillna(0).round(2)
df['FANTASIA'] = df['FANTASIA'].fillna('').str.upper()
df['PRODUTO']  = df['PRODUTO'].fillna('')

print(f"OK {df['CODCLI'].nunique()} clientes não positivados SP")

_RE_LIMPAR = re.compile(r'\s*-?\s*OFF\s*TRADE\s*(SP)?\s*', re.IGNORECASE)

def _limpar_nome(nome: str) -> str:
    return _RE_LIMPAR.sub(' ', nome or '').strip().upper()

# Agrupa: por_vendedor → lista de clientes → cada cliente tem lista de produtos
por_vendedor: dict = {}
_cli_idx: dict = {}  # (vendedor, codcli) → índice na lista

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

output = (
    "// Gerado automaticamente pelo exportacao_nao_pos_sp.py\n\n"
    f"const NAO_POS_SP_DATA = {json.dumps(payload, ensure_ascii=False, indent=2)};\n"
)

out_path = Path(__file__).parent / "nao_pos_sp_data.js"
out_path.write_text(output, encoding='utf-8')

n_cli = sum(len(v) for v in por_vendedor.values())
print(f"OK nao_pos_sp_data.js — {len(por_vendedor)} vendedores, {n_cli} clientes")

repo_dir = str(Path(__file__).parent)
subprocess.run(["git", "-C", repo_dir, "add", "nao_pos_sp_data.js"], check=True)
subprocess.run(["git", "-C", repo_dir, "commit", "-m",
                f"Atualiza nao_pos_sp_data.js - {datetime.now().strftime('%d/%m/%Y')}"])
subprocess.run(["git", "-C", repo_dir, "push", "origin", "master"], check=True)
print("OK GitHub Pages atualizado.")
