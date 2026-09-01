# exportacao_es.py — Gera vendas_es_data.js (CRC filial 2 = ES)
import json, time, re
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
        M.NUMNOTA                       AS NUNOTA,
        M.CODCLI,
        C.CLIENTE,
        M.DESCRICAO                     AS PRODUTO,
        F.FANTASIA,
        M.QT,
        (M.PUNIT * M.QT)               AS VALOR,
        M.CODOPER,
        U.NOME                          AS NOME_ORACLE,
        U.CODUSUR                       AS CODUSUR,
        C.OFFTRADE                      AS OFFTRADE,
        CASE WHEN M.CODOPER = 'ED' OR M.NUMNOTADEV IS NOT NULL THEN 'S' ELSE 'N' END AS DEVOLVIDO
    FROM CRC.PCMOV M
    JOIN CRC.PCUSUARI U  ON M.CODUSUR   = U.CODUSUR
    LEFT JOIN CRC.PCCLIENT C ON M.CODCLI = C.CODCLI
    JOIN CRC.PCPRODUT P  ON M.CODPROD   = P.CODPROD
    JOIN CRC.PCFORNEC F  ON P.CODFORNEC = F.CODFORNEC
    WHERE M.DTMOV >= ADD_MONTHS(TRUNC(SYSDATE, 'MM'), -12)
      AND M.CODOPER IN ('S', 'SB', 'ED')
      AND M.DTCANCEL  IS NULL
      AND M.CODFILIAL = 1
      AND U.NOME LIKE '%OFF TRADE%'
      AND U.ESTADO = 'ES'
"""

_vh = carregar_dados(QUERY_VENDAS_ES, engine_es, "vendas_ES")
_vh['MES']      = pd.to_datetime(_vh['MES'],   errors='coerce')
_vh['QT']       = pd.to_numeric(_vh['QT'],     errors='coerce').fillna(0).astype(int)
_vh['VALOR']    = pd.to_numeric(_vh['VALOR'],  errors='coerce').fillna(0).round(2)
_vh['NUNOTA']   = pd.to_numeric(_vh['NUNOTA'], errors='coerce')
_vh['CLIENTE']  = _vh['CLIENTE'].fillna(_vh['CODCLI'].astype(str))
_vh['FANTASIA'] = _vh['FANTASIA'].fillna('')
_vh['PRODUTO']  = _vh['PRODUTO'].fillna('')
_vh['OFFTRADE'] = _vh['OFFTRADE'].fillna('N')
_vh['DEVOLVIDO'] = _vh['DEVOLVIDO'].fillna('N') == 'S'
_vh['MES_STR']  = _vh['MES'].apply(_mes_pt)
_vh['VENDEDOR'] = (
    _vh['NOME_ORACLE']
    .str.replace(r'\s*-?\s*OFF\s*TRADE\s*', '', regex=True, case=False)
    .str.strip().str.upper()
)
_vh = _vh[_vh['VENDEDOR'].notna() & (_vh['VENDEDOR'] != '')].copy()
print(f"Vendedores ES: {sorted(_vh['VENDEDOR'].unique())}")

# ── Pedidos cancelados/cortados, item a item (mesma lógica de
# exportacao_meta.py/exportacao_sp.py/exportacao_mg.py — status de nota, sem
# status de logística, pedido do usuário em 2026-08-13) ──────────────────────


def _id_str(v):
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return ''
    try:
        return str(int(float(v)))
    except (TypeError, ValueError):
        return str(v).strip()


def _limpar_nome_vendedor(nome):
    s = re.sub(r'\s*-?\s*OFF\s*TRADE\s*', '', str(nome or ''), flags=re.IGNORECASE)
    return s.strip().upper()


_QUERY_ES_CANCEL_POS = """
    SELECT
        PED.DATA AS DATA, PED.CODCLI AS CODCLI, PED.CLIENTE AS CLIENTE,
        PED.DESCRICAO AS PRODUTO, PED.FANTASIA_FORNEC AS FANTASIA,
        PED.QT AS QT, PED.TOTAL AS VALOR, PED.NUMPED AS NUMPED,
        PED.CODUSUR AS CODUSUR, U.NOME AS NOME_ORACLE
    FROM CRC.PBI_PCPEDI PED
    JOIN CRC.PCUSUARI U ON U.CODUSUR = PED.CODUSUR
    WHERE PED.DATA >= ADD_MONTHS(TRUNC(SYSDATE, 'MM'), -12)
      AND PED.STATUS = 'CANCELADA'
      AND PED.CODFILIAL = 1
      AND U.NOME LIKE '%OFF TRADE%'
      AND U.ESTADO = 'ES'
"""

_QUERY_ES_CANCEL_PRE = """
    SELECT
        PC.DATACANC AS DATA, PC.DESCRICAO AS PRODUTO,
        PC.QT AS QT, PC.SUBTOT AS VALOR, PC.NUMPED AS NUMPED,
        U.CODUSUR AS CODUSUR, U.NOME AS NOME_ORACLE
    FROM CRC.PEDIDOS_CANCELADOS PC
    JOIN CRC.PCUSUARI U ON U.CODUSUR = TO_NUMBER(SUBSTR(PC.NUMPED, 1, LENGTH(PC.NUMPED) - 6))
    WHERE PC.DATACANC >= ADD_MONTHS(TRUNC(SYSDATE, 'MM'), -12)
      AND U.NOME LIKE '%OFF TRADE%'
      AND U.ESTADO = 'ES'
"""

_QUERY_ES_CORTE = """
    SELECT
        PED.DATA AS DATA, PED.CODCLI AS CODCLI, PED.CLIENTE AS CLIENTE,
        PED.DESCRICAO AS PRODUTO, PED.FANTASIA_FORNEC AS FANTASIA,
        PED.CODUSUR AS CODUSUR, U.NOME AS NOME_ORACLE,
        PED.NUMPED AS NUMPED, PED.NUMNOTA AS NUMNOTA,
        PED.QTFALTA AS QTFALTA, PED.PVENDA AS PVENDA,
        NVL(CT.QTCORTADA, 0) AS QTCORTADA
    FROM CRC.PBI_PCPEDI PED
    JOIN CRC.PCUSUARI U ON U.CODUSUR = PED.CODUSUR
    LEFT JOIN (
        SELECT NUMPED, CODPROD, CODFILIAL, SUM(QTCORTADA) AS QTCORTADA
        FROM CRC.PCCORTEI
        WHERE DATA >= ADD_MONTHS(TRUNC(SYSDATE, 'MM'), -12)
        GROUP BY NUMPED, CODPROD, CODFILIAL
    ) CT ON CT.NUMPED = PED.NUMPED AND CT.CODPROD = PED.CODPROD
         AND NVL(CT.CODFILIAL, 'x') = NVL(PED.CODFILIAL, 'x')
    WHERE PED.DATA >= ADD_MONTHS(TRUNC(SYSDATE, 'MM'), -12)
      AND PED.NUMNOTA IS NOT NULL
      AND NVL(PED.STATUS, 'X') != 'CANCELADA'
      AND PED.CODFILIAL = 1
      AND U.NOME LIKE '%OFF TRADE%'
      AND U.ESTADO = 'ES'
"""

_itens_extra_por_vendedor: dict = {}


def _registrar_item_extra(nome_oracle, mes_str, item):
    nome_disp = _limpar_nome_vendedor(nome_oracle)
    if not nome_disp:
        return
    _itens_extra_por_vendedor.setdefault(nome_disp, {}).setdefault(mes_str, []).append(item)


try:
    _vc_cancel_pos = carregar_dados(_QUERY_ES_CANCEL_POS, engine_es, "es_cancel_pos")
except Exception as _e:
    print(f"[AVISO] es_cancel_pos falhou ({str(_e)[:100]}) — ignorado")
    _vc_cancel_pos = pd.DataFrame()
try:
    _vc_cancel_pre = carregar_dados(_QUERY_ES_CANCEL_PRE, engine_es, "es_cancel_pre")
except Exception as _e:
    print(f"[AVISO] es_cancel_pre falhou ({str(_e)[:100]}) — ignorado (base pode não ter a view)")
    _vc_cancel_pre = pd.DataFrame()
try:
    _vc_corte = carregar_dados(_QUERY_ES_CORTE, engine_es, "es_corte")
except Exception as _e:
    print(f"[AVISO] es_corte falhou ({str(_e)[:100]}) — ignorado")
    _vc_corte = pd.DataFrame()

if not _vc_cancel_pos.empty:
    _vc_cancel_pos['DATA'] = pd.to_datetime(_vc_cancel_pos['DATA'], errors='coerce')
    _vc_cancel_pos = _vc_cancel_pos[_vc_cancel_pos['DATA'].notna()]
    for _, row in _vc_cancel_pos.iterrows():
        _registrar_item_extra(row['NOME_ORACLE'], _mes_pt(row['DATA']), {
            'data':      row['DATA'].strftime('%d/%m/%Y'),
            'codcli':    str(row.get('CODCLI') or ''),
            'cliente':   str(row.get('CLIENTE') or ''),
            'produto':   str(row.get('PRODUTO') or ''),
            'fantasia':  str(row.get('FANTASIA') or ''),
            'qt':        int(pd.to_numeric(row.get('QT'), errors='coerce') or 0),
            'valor':     round(float(pd.to_numeric(row.get('VALOR'), errors='coerce') or 0), 2),
            'tipo':      'Cancelado',
            'offtrade':  True,
            'devolvido': False,
            'cancelado': True,
            'cancelado_parcial': False,
            'numped':    _id_str(row.get('NUMPED')),
            'nunota':    '',
        })
    print(f"OK vendas ES: {len(_vc_cancel_pos)} item(ns) cancelado(s) pós-NF")

if not _vc_cancel_pre.empty:
    _vc_cancel_pre['DATA'] = pd.to_datetime(_vc_cancel_pre['DATA'], errors='coerce')
    _vc_cancel_pre = _vc_cancel_pre[_vc_cancel_pre['DATA'].notna()]
    for _, row in _vc_cancel_pre.iterrows():
        _registrar_item_extra(row['NOME_ORACLE'], _mes_pt(row['DATA']), {
            'data':      row['DATA'].strftime('%d/%m/%Y'),
            'codcli':    '',
            'cliente':   '',
            'produto':   str(row.get('PRODUTO') or ''),
            'fantasia':  '',
            'qt':        int(pd.to_numeric(row.get('QT'), errors='coerce') or 0),
            'valor':     round(float(pd.to_numeric(row.get('VALOR'), errors='coerce') or 0), 2),
            'tipo':      'Cancelado',
            'offtrade':  True,
            'devolvido': False,
            'cancelado': True,
            'cancelado_parcial': False,
            'numped':    _id_str(row.get('NUMPED')),
            'nunota':    '',
        })
    print(f"OK vendas ES: {len(_vc_cancel_pre)} item(ns) cancelado(s) pré-NF")

if not _vc_corte.empty:
    _vc_corte['DATA']      = pd.to_datetime(_vc_corte['DATA'], errors='coerce')
    _vc_corte['QTFALTA']   = pd.to_numeric(_vc_corte['QTFALTA'], errors='coerce').fillna(0)
    _vc_corte['QTCORTADA'] = pd.to_numeric(_vc_corte['QTCORTADA'], errors='coerce').fillna(0)
    _vc_corte['PVENDA']    = pd.to_numeric(_vc_corte['PVENDA'], errors='coerce').fillna(0)
    _vc_corte['QTD_CORTADA_TOTAL'] = _vc_corte['QTCORTADA'] + _vc_corte['QTFALTA']
    _vc_corte = _vc_corte[(_vc_corte['DATA'].notna()) & (_vc_corte['QTD_CORTADA_TOTAL'] > 0)]
    for _, row in _vc_corte.iterrows():
        _registrar_item_extra(row['NOME_ORACLE'], _mes_pt(row['DATA']), {
            'data':      row['DATA'].strftime('%d/%m/%Y'),
            'codcli':    str(row.get('CODCLI') or ''),
            'cliente':   str(row.get('CLIENTE') or ''),
            'produto':   str(row.get('PRODUTO') or ''),
            'fantasia':  str(row.get('FANTASIA') or ''),
            'qt':        round(float(row['QTD_CORTADA_TOTAL']), 2),
            'valor':     round(float(row['PVENDA']) * float(row['QTD_CORTADA_TOTAL']), 2),
            'tipo':      'Corte parcial',
            'offtrade':  True,
            'devolvido': False,
            'cancelado': False,
            'cancelado_parcial': True,
            'numped':    _id_str(row.get('NUMPED')),
            'nunota':    _id_str(row.get('NUMNOTA')),
        })
    print(f"OK vendas ES: {len(_vc_corte)} item(ns) com corte parcial")

_meses_vh  = sorted(_vh['MES'].dropna().unique(), reverse=True)
_meses_str = [_mes_pt(m) for m in _meses_vh]
_meses_sorted = sorted(_meses_str, key=_mes_sort_key, reverse=True)

_rcas = {
    row['VENDEDOR']: str(int(row['CODUSUR']))
    for _, row in _vh[['VENDEDOR', 'CODUSUR']].drop_duplicates().iterrows()
    if pd.notna(row['CODUSUR'])
}

# ── Papel do vendedor (Representante/Externo/Supervisor/Gerente) ─────────────
# TIPOVEND (Oracle) só distingue Representante/Externo/Interno/Profissional,
# mas PCSUPERV/PCGERENTE têm coluna COD_CADRCA que liga o cadastro de
# supervisor/gerente ao PRÓPRIO cadastro de vendedor dele em PCUSUARI —
# quando isso acontece, o papel real (Supervisor/Gerente) tem prioridade
# sobre o TIPOVEND (mesma lógica de exportacao_meta.py/exportacao_sp.py).
_codusur_es = {
    int(row['CODUSUR']): row['VENDEDOR']
    for _, row in _vh[['VENDEDOR', 'CODUSUR']].drop_duplicates().iterrows()
    if pd.notna(row['CODUSUR'])
}
_tipovenda: dict[str, str] = {}
try:
    _vc_papel = carregar_dados("""
        SELECT U.CODUSUR AS CODUSUR, U.TIPOVEND,
               CASE WHEN G.COD_CADRCA IS NOT NULL THEN 1 ELSE 0 END AS EH_GERENTE,
               CASE WHEN S.COD_CADRCA IS NOT NULL THEN 1 ELSE 0 END AS EH_SUPERVISOR
        FROM CRC.PCUSUARI U
        LEFT JOIN CRC.PCGERENTE G ON G.COD_CADRCA = U.CODUSUR
        LEFT JOIN CRC.PCSUPERV  S ON S.COD_CADRCA = U.CODUSUR
        WHERE U.NOME LIKE '%OFF TRADE%' AND U.ESTADO = 'ES'
    """, engine_es, "es_papel")
    for _, _row in _vc_papel.iterrows():
        if pd.isna(_row.get('CODUSUR')):
            continue
        _nome = _codusur_es.get(int(_row['CODUSUR']))
        if not _nome:
            continue
        _tv = str(_row.get('TIPOVEND') or '').strip().upper()
        _papel = 'Gerente' if _row.get('EH_GERENTE') else 'Supervisor' if _row.get('EH_SUPERVISOR') else \
                 {'R': 'Representante', 'E': 'Externo'}.get(_tv, '')
        if _papel:
            _tipovenda[_nome] = _papel
except Exception as _e:
    print(f"[AVISO] es_papel falhou ({str(_e)[:100]}) — badge de tipo de venda fica sem dado nesta rodada")

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
        'tipo':     {'SB': 'Bonificado', 'ED': 'Devolução'}.get(str(row.get('CODOPER', 'S')).upper(), 'Venda'),
        'offtrade': row['OFFTRADE'] == 'S',
        'devolvido': bool(row['DEVOLVIDO']),
        'cancelado': False,
        'cancelado_parcial': False,
        'numped':   '',
        'nunota':   _id_str(row.get('NUNOTA')),
    })

# Mescla os itens de cancelado/corte parcial — pode incluir mês que _vh não
# tinha nenhuma venda válida, então _meses_str precisa ser recalculado
# incluindo esses meses também.
for v_nome, por_mes in _itens_extra_por_vendedor.items():
    for mes, itens in por_mes.items():
        _por_vendedor.setdefault(v_nome, {}).setdefault(mes, []).extend(itens)

_meses_extra = {mes for por_mes in _itens_extra_por_vendedor.values() for mes in por_mes}
_meses_str = sorted(set(_meses_str) | _meses_extra, key=_mes_sort_key, reverse=True)
_meses_sorted = sorted(_meses_str, key=_mes_sort_key, reverse=True)

# ── Resumo: fat, fat_ant, pos por vendedor/mês ───────────────────────────────
# Só venda válida entra no KPI — cancelado/corte/devolvido ficam visíveis na
# lista (destacados por cor no front-end) mas não inflam o faturamento.

_resumo: dict = {}
for nome, meses_data in _por_vendedor.items():
    for mes, vendas in meses_data.items():
        validas = [v for v in vendas if not v['devolvido'] and not v['cancelado'] and not v['cancelado_parcial']]
        fat = round(sum(v['valor'] for v in validas), 2)
        # Positivação conta qualquer cliente que comprou, sem exigir
        # PCCLIENT.OFFTRADE='S' — mesmo critério aplicado em exportacao_meta.py
        # (RJ) em 2026-08-25, a pedido do usuário.
        pos = len(set(v['codcli'] for v in validas))
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
    'tipovenda':     _tipovenda,
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
