# exportacao_sp.py — Gera vendas_sp_data.js a partir do banco SP (spon_oci, schema sp)
import json
import time
import pandas as pd
import oracledb
from sqlalchemy import create_engine
from datetime import date, datetime
from pathlib import Path

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

# ── Helpers ───────────────────────────────────────────────────────────────────

_MESES_PT = {
    'Jan': 'Jan', 'Feb': 'Fev', 'Mar': 'Mar', 'Apr': 'Abr',
    'May': 'Mai', 'Jun': 'Jun', 'Jul': 'Jul', 'Aug': 'Ago',
    'Sep': 'Set', 'Oct': 'Out', 'Nov': 'Nov', 'Dec': 'Dez',
}

def _mes_pt(dt):
    return f"{_MESES_PT.get(dt.strftime('%b'), dt.strftime('%b'))}/{dt.strftime('%y')}"

def _mes_sort_key(mes_str):
    try:
        nome, ano = mes_str.split('/')
        ordem = ['Jan','Fev','Mar','Abr','Mai','Jun','Jul','Ago','Set','Out','Nov','Dez']
        return (int('20' + ano), ordem.index(nome) if nome in ordem else 0)
    except Exception:
        return (0, 0)

def carregar_dados(query, engine, nome='tabela', max_tentativas=5):
    for tentativa in range(1, max_tentativas + 1):
        try:
            print(f"-> Lendo {nome} (Tentativa {tentativa}/{max_tentativas})...")
            with engine.connect() as conn:
                chunks = []
                for chunk in pd.read_sql(query, con=conn, chunksize=5000):
                    chunks.append(chunk)
                df = pd.concat(chunks, ignore_index=True)
                df.columns = df.columns.str.strip().str.upper()
                print(f"OK {nome} carregada!")
                return df
        except Exception as e:
            print(f"Erro na {nome}: {str(e)[:120]}")
            engine.dispose()
            if tentativa < max_tentativas:
                time.sleep(10)
            else:
                raise e

# ── Query: vendas SP (últimos 6 meses) ───────────────────────────────────────
# Ajuste CODFILIAL se necessário para filtrar apenas filiais SP relevantes

QUERY_VENDAS_SP = """
    SELECT
        TRUNC(M.DTMOV, 'MM')            AS MES,
        TO_CHAR(M.DTMOV, 'DD/MM/YYYY') AS DATA,
        M.CODCLI,
        C.CLIENTE,
        M.DESCRICAO                     AS PRODUTO,
        F.FANTASIA,
        M.QT,
        (M.PUNIT * M.QT)               AS VALOR,
        U.NOME                          AS NOME_ORACLE
    FROM SPON.PCMOV M
    JOIN SPON.PCUSUARI U  ON M.CODUSUR  = U.CODUSUR
    LEFT JOIN SPON.PCCLIENT C ON M.CODCLI = C.CODCLI
    JOIN SPON.PCPRODUT P  ON M.CODPROD  = P.CODPROD
    JOIN SPON.PCFORNEC F  ON P.CODFORNEC = F.CODFORNEC
    WHERE M.DTMOV >= ADD_MONTHS(TRUNC(SYSDATE, 'MM'), -5)
      AND M.CODOPER = 'S'
      AND M.NUMNOTADEV IS NULL
      AND M.DTCANCEL  IS NULL
      AND U.NOME LIKE '%OFF TRADE%'
"""

# ── Carrega e limpa dados ─────────────────────────────────────────────────────

_vh = carregar_dados(QUERY_VENDAS_SP, engine_sp, "vendas_SP")

_vh['MES']      = pd.to_datetime(_vh['MES'],   errors='coerce')
_vh['QT']       = pd.to_numeric(_vh['QT'],     errors='coerce').fillna(0).astype(int)
_vh['VALOR']    = pd.to_numeric(_vh['VALOR'],  errors='coerce').fillna(0).round(2)
_vh['CLIENTE']  = _vh['CLIENTE'].fillna(_vh['CODCLI'].astype(str))
_vh['FANTASIA'] = _vh['FANTASIA'].fillna('')
_vh['PRODUTO']  = _vh['PRODUTO'].fillna('')
_vh['MES_STR']  = _vh['MES'].apply(_mes_pt)

# Remove sufixo/prefixo "OFF TRADE" do nome para exibição
import re
_vh['VENDEDOR'] = (
    _vh['NOME_ORACLE']
    .str.replace(r'\s*OFF\s*TRADE\s*(SP)?\s*', ' ', regex=True, case=False)
    .str.strip()
    .str.upper()
)
_vh = _vh[_vh['VENDEDOR'].notna() & (_vh['VENDEDOR'] != '')].copy()

print(f"Vendedores SP encontrados: {sorted(_vh['VENDEDOR'].unique())}")

# ── Monta estrutura por_vendedor ──────────────────────────────────────────────

_meses_vh  = sorted(_vh['MES'].dropna().unique(), reverse=True)
_meses_str = [_mes_pt(m) for m in _meses_vh]

_por_vendedor: dict = {}
for _, row in _vh.iterrows():
    v_nome = row['VENDEDOR']
    mes    = row['MES_STR']
    _por_vendedor.setdefault(v_nome, {}).setdefault(mes, []).append({
        'data':     str(row['DATA']),
        'codcli':   str(row['CODCLI']),
        'cliente':  str(row['CLIENTE']),
        'produto':  str(row['PRODUTO']),
        'fantasia': str(row['FANTASIA']),
        'qt':       int(row['QT']),
        'valor':    float(row['VALOR']),
    })

# ── Escreve vendas_sp_data.js ─────────────────────────────────────────────────

payload = {
    'atualizado_em': datetime.now().strftime('%d/%m/%Y %H:%M'),
    'meses':         _meses_str,
    'por_vendedor':  _por_vendedor,
}

js_out = (
    "// Gerado automaticamente pelo exportacao_sp.py\n\n"
    f"const VENDAS_SP_DATA = {json.dumps(payload, ensure_ascii=False, indent=2)};\n"
)

output_path = Path(__file__).parent / "vendas_sp_data.js"
with open(output_path, 'w', encoding='utf-8') as f:
    f.write(js_out)

print(f"OK vendas_sp_data.js gerado — {len(_por_vendedor)} vendedores, meses: {_meses_str}")

# ── Push para GitHub Pages ────────────────────────────────────────────────────

import subprocess
repo_dir = str(Path(__file__).parent)
subprocess.run(["git", "-C", repo_dir, "add", "vendas_sp_data.js", "sp.html"], check=True)
subprocess.run(["git", "-C", repo_dir, "commit", "-m",
                f"Atualiza vendas_sp_data.js - {date.today().strftime('%d/%m/%Y')}"])
subprocess.run(["git", "-C", repo_dir, "push", "origin", "master"], check=True)
print("OK GitHub Pages atualizado.")
