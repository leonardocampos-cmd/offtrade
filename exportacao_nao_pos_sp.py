# exportacao_nao_pos_sp.py — gera nao_pos_sp_data.js com clientes que não compraram no mês
import json, re
import pandas as pd
import oracledb
from sqlalchemy import create_engine
from datetime import datetime
from pathlib import Path
import subprocess, sys

oracledb.init_oracle_client(lib_dir=r"C:\instantclient")

user     = "vpn"
password = "vpn2320vpn"
dsn_sp   = "spon_oci"

engine_sp = create_engine(
    f'oracle+oracledb://{user}:{password}@{dsn_sp}',
    pool_pre_ping=True,
    pool_recycle=3600,
    connect_args={"expire_time": 2}
)

QUERY = """
SELECT
    C.CODCLI,
    C.CLIENTE,
    C.BAIRROENT,
    TO_CHAR(LV.DTULTCOMP, 'DD/MM/YYYY') AS DTULTCOMP,
    U.NOME AS NOME_RCA
FROM SPON.PBI_PCCLIENT C
LEFT JOIN (
    SELECT CODCLI, MAX(DTMOV) AS DTULTCOMP
    FROM SPON.PCMOV
    WHERE CODOPER = 'S' AND NUMNOTADEV IS NULL AND DTCANCEL IS NULL
    GROUP BY CODCLI
) LV ON C.CODCLI = LV.CODCLI
LEFT JOIN SPON.PCUSUARI U ON C.RCA = U.CODUSUR
WHERE (U.NOME LIKE '%OFF TRADE%' OR U.NOME = 'W.S')
  AND C.CODCLI NOT IN (
    SELECT DISTINCT CODCLI FROM SPON.PCMOV
    WHERE TRUNC(DTMOV, 'MM') = TRUNC(SYSDATE, 'MM')
      AND CODOPER = 'S' AND NUMNOTADEV IS NULL AND DTCANCEL IS NULL
  )
ORDER BY U.NOME, C.CLIENTE
"""

print("-> Consultando não positivados SP (SPON)...")
with engine_sp.connect() as conn:
    df = pd.read_sql(QUERY, conn)
df.columns = df.columns.str.upper()
print(f"OK {len(df)} clientes não positivados SP")

_RE_LIMPAR = re.compile(r'\s*OFF\s*TRADE\s*(SP)?\s*', re.IGNORECASE)

def _limpar_nome(nome: str) -> str:
    return _RE_LIMPAR.sub(' ', nome or '').strip().upper()

por_vendedor: dict = {}
for _, row in df.iterrows():
    v = _limpar_nome(str(row['NOME_RCA']))
    if not v:
        continue
    por_vendedor.setdefault(v, []).append({
        'codcli':    str(row['CODCLI']),
        'cliente':   str(row['CLIENTE']   or ''),
        'bairro':    str(row['BAIRROENT'] or ''),
        'dtultcomp': str(row['DTULTCOMP'] or ''),
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
print(f"OK nao_pos_sp_data.js — {len(por_vendedor)} vendedores, {len(df)} clientes")

repo_dir = str(Path(__file__).parent)
subprocess.run(["git", "-C", repo_dir, "add", "nao_pos_sp_data.js"], check=True)
subprocess.run(["git", "-C", repo_dir, "commit", "-m",
                f"Atualiza nao_pos_sp_data.js - {datetime.now().strftime('%d/%m/%Y')}"])
subprocess.run(["git", "-C", repo_dir, "push", "origin", "master"], check=True)
print("OK GitHub Pages atualizado.")
