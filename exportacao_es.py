# exportacao_es.py — Gera vendas_es_data.js (CRC filial 2 = ES)
import json, time
import pandas as pd
import oracledb
from sqlalchemy import create_engine
from datetime import date, datetime
from pathlib import Path

import os
from urllib.parse import quote_plus
from dotenv import load_dotenv
load_dotenv(Path(__file__).parent / ".env")

from utils import ORACLE_LIB, git_commit_push
from meta import _com_timeout_forcado
oracledb.init_oracle_client(lib_dir=ORACLE_LIB)

_crc_user = os.environ["CRC_USER"]
_crc_pass = os.environ["CRC_PASSWORD"]

engine_es = create_engine(
    f'oracle+oracledb://{_crc_user}:{quote_plus(_crc_pass)}@crc_oci',
    pool_pre_ping=True, pool_recycle=3600,
    connect_args={"expire_time": 2}
)

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

def carregar_dados(query, engine, nome='tabela', max_tentativas=3, timeout_por_tentativa=90):
    def _fazer_query():
        with engine.connect() as conn:
            chunks = [chunk for chunk in pd.read_sql(query, con=conn, chunksize=5000)]
            df = pd.concat(chunks, ignore_index=True)
            df.columns = df.columns.str.strip().str.upper()
            return df

    for tentativa in range(1, max_tentativas + 1):
        try:
            print(f"-> Lendo {nome} (Tentativa {tentativa}/{max_tentativas})...")
            df = _com_timeout_forcado(_fazer_query, timeout_por_tentativa)
            print(f"OK {nome} carregada!")
            return df
        except Exception as e:
            print(f"Erro na {nome}: {str(e)[:120]}")
            engine.dispose()
            if tentativa < max_tentativas:
                time.sleep(10)
            else:
                raise e

QUERY_VENDAS_ES = """
    SELECT
        TRUNC(M.DTMOV, 'MM')            AS MES,
        TO_CHAR(M.DTMOV, 'DD/MM/YYYY') AS DATA,
        M.CODCLI,
        C.CLIENTE,
        M.DESCRICAO                     AS PRODUTO,
        F.FANTASIA,
        M.QT,
        (M.PUNIT * M.QT)               AS VALOR,
        U.NOME                          AS NOME_ORACLE,
        U.CODUSUR                       AS CODUSUR,
        C.OFFTRADE                      AS OFFTRADE
    FROM CRC.PCMOV M
    JOIN CRC.PCUSUARI U  ON M.CODUSUR   = U.CODUSUR
    LEFT JOIN CRC.PCCLIENT C ON M.CODCLI = C.CODCLI
    JOIN CRC.PCPRODUT P  ON M.CODPROD   = P.CODPROD
    JOIN CRC.PCFORNEC F  ON P.CODFORNEC = F.CODFORNEC
    WHERE M.DTMOV >= ADD_MONTHS(TRUNC(SYSDATE, 'MM'), -12)
      AND M.CODOPER = 'S'
      AND M.NUMNOTADEV IS NULL
      AND M.DTCANCEL  IS NULL
      AND M.CODFILIAL = 1
      AND U.NOME LIKE '%OFF TRADE%'
      AND U.ESTADO = 'ES'
"""

_vh = carregar_dados(QUERY_VENDAS_ES, engine_es, "vendas_ES")
_vh['MES']      = pd.to_datetime(_vh['MES'],   errors='coerce')
_vh['QT']       = pd.to_numeric(_vh['QT'],     errors='coerce').fillna(0).astype(int)
_vh['VALOR']    = pd.to_numeric(_vh['VALOR'],  errors='coerce').fillna(0).round(2)
_vh['CLIENTE']  = _vh['CLIENTE'].fillna(_vh['CODCLI'].astype(str))
_vh['FANTASIA'] = _vh['FANTASIA'].fillna('')
_vh['PRODUTO']  = _vh['PRODUTO'].fillna('')
_vh['OFFTRADE'] = _vh['OFFTRADE'].fillna('N')
_vh['MES_STR']  = _vh['MES'].apply(_mes_pt)
_vh['VENDEDOR'] = (
    _vh['NOME_ORACLE']
    .str.replace(r'\s*-?\s*OFF\s*TRADE\s*', '', regex=True, case=False)
    .str.strip().str.upper()
)
_vh = _vh[_vh['VENDEDOR'].notna() & (_vh['VENDEDOR'] != '')].copy()
print(f"Vendedores ES: {sorted(_vh['VENDEDOR'].unique())}")

_meses_vh  = sorted(_vh['MES'].dropna().unique(), reverse=True)
_meses_str = [_mes_pt(m) for m in _meses_vh]
_meses_sorted = sorted(_meses_str, key=_mes_sort_key, reverse=True)

_rcas = {
    row['VENDEDOR']: str(int(row['CODUSUR']))
    for _, row in _vh[['VENDEDOR', 'CODUSUR']].drop_duplicates().iterrows()
    if pd.notna(row['CODUSUR'])
}

# ── Detalhe de vendas por vendedor/mês ───────────────────────────────────────

_por_vendedor: dict = {}
for _, row in _vh.iterrows():
    _por_vendedor.setdefault(row['VENDEDOR'], {}).setdefault(row['MES_STR'], []).append({
        'data':     str(row['DATA']),
        'codcli':   str(row['CODCLI']),
        'cliente':  str(row['CLIENTE']),
        'produto':  str(row['PRODUTO']),
        'fantasia': str(row['FANTASIA']),
        'qt':       int(row['QT']),
        'valor':    float(row['VALOR']),
        'offtrade': row['OFFTRADE'] == 'S',
    })

# ── Resumo: fat, fat_ant, pos por vendedor/mês ───────────────────────────────

_resumo: dict = {}
for nome, meses_data in _por_vendedor.items():
    for mes, vendas in meses_data.items():
        fat = round(sum(v['valor'] for v in vendas), 2)
        pos = len(set(v['codcli'] for v in vendas if v['offtrade']))
        _resumo.setdefault(nome, {})[mes] = {'fat': fat, 'pos': pos, 'fat_ant': 0.0, 'fat_ano_ant': 0.0}

for nome in _resumo:
    for i, mes in enumerate(_meses_sorted):
        if mes not in _resumo[nome]:
            continue
        if i + 1 < len(_meses_sorted):
            ant = _meses_sorted[i + 1]
            _resumo[nome][mes]['fat_ant'] = _resumo[nome].get(ant, {}).get('fat', 0.0)
        if i + 12 < len(_meses_sorted):
            ano_ant = _meses_sorted[i + 12]
            _resumo[nome][mes]['fat_ano_ant'] = _resumo[nome].get(ano_ant, {}).get('fat', 0.0)

print(f"Realizado ES: {len(_resumo)} vendedores")

# ── Gera vendas_es_data.js ────────────────────────────────────────────────────

payload = {
    'atualizado_em': datetime.now().strftime('%d/%m/%Y %H:%M'),
    'meses':         _meses_str,
    'rcas':          _rcas,
    'resumo':        _resumo,
    'por_vendedor':  _por_vendedor,
}

output_path = Path(__file__).parent / "vendas_es_data.js"
output_path.write_text(
    f"// Gerado automaticamente pelo exportacao_es.py\n\n"
    f"const VENDAS_ES_DATA = {json.dumps(payload, ensure_ascii=False, indent=2)};\n",
    encoding='utf-8'
)
print(f"OK vendas_es_data.js — {len(_por_vendedor)} vendedores, meses: {_meses_str}")

git_commit_push(["vendas_es_data.js", "es.html"],
                f"Atualiza vendas_es_data.js - {date.today().strftime('%d/%m/%Y')}")
