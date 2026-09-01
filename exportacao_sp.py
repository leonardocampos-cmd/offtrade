# exportacao_sp.py — Gera vendas_sp_data.js pros vendedores de SP.
# Vendas de SP não moram só no schema SPON (spon_oci) — existem vendedores
# ESTADO='SP' "escondidos" em CRC/CASTAS também (confirmado em 2026-08-13:
# 1 RCA em CRC, 1 em CASTAS; thekings/GARRIDO/MGON não têm nenhum hoje, mas
# ficam configurados por segurança) — pedido do usuário: "as vendas de SP
# devem ser buscadas em todos os sistemas". GARRIDO fica de fora: não tem
# PCUSUARI.ESTADO preenchido em nenhuma linha, não dá pra distinguir SP ali
# sem outro sinal. BLENDED é uma fonte adicional nova (banco BLENDED_OCI,
# schema BLENDED) — lá TODOS os OFF TRADE têm ESTADO nulo (confirmado
# 2026-08-13), então é tratada como schema inteiro = SP (mesma lógica que
# CASTAS/GARRIDO já usam em exportacao_meta.py: schema inteiro = uma
# empresa/região, sem precisar de filtro de estado).
import json
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
import pandas as pd
import oracledb
from sqlalchemy import create_engine
from datetime import date, datetime
from pathlib import Path
from urllib.parse import quote_plus
from dotenv import load_dotenv
load_dotenv(Path(__file__).parent / ".env")

from utils import ORACLE_LIB, git_commit_push
from meta import _com_timeout_forcado, engine, engine_theking, engine_castas, engine_mgon, engine_blended
oracledb.init_oracle_client(lib_dir=ORACLE_LIB)

user     = os.environ["SPON_USER"]
password = os.environ["SPON_PASSWORD"]
dsn_sp   = "spon_oci"

engine_sp = create_engine(
    f'oracle+oracledb://{user}:{quote_plus(password)}@{dsn_sp}',
    pool_pre_ping=True,
    pool_recycle=3600,
    connect_args={"expire_time": 2}
)

# (nome_fonte, engine, schema, filtra_por_estado)
_SP_SOURCES = [
    ("SPON",      engine_sp,       "SPON",     True),
    ("CRC",       engine,          "CRC",      True),
    ("thekings",  engine_theking,  "THEKINGS", True),
    ("CASTAS",    engine_castas,   "CASTAS",   True),
    ("MGON",      engine_mgon,     "MGON",     True),
    ("BLENDED",   engine_blended,  "BLENDED",  False),
]

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

def carregar_dados(query, engine, nome='tabela', max_tentativas=3, timeout_por_tentativa=90):
    def _fazer_query():
        with engine.connect() as conn:
            chunks = []
            for chunk in pd.read_sql(query, con=conn, chunksize=5000):
                chunks.append(chunk)
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

# ── Query: vendas SP (últimos 12 meses), uma por fonte ───────────────────────
# filtra_por_estado=True: schema tem múltiplos estados, restringe a SP.
# filtra_por_estado=False: schema inteiro já é considerado SP (BLENDED, sem
# ESTADO preenchido) — mesmo padrão de CASTAS/GARRIDO em exportacao_meta.py.

def _query_vendas_sp(schema, filtra_por_estado):
    # 'W.S' é um nome especial (já usado como extra_nomes em SPON/MGON via
    # meta.py::_SPON_EXTRA) que designa conta de SP independente do que
    # ESTADO diz — confirmado em 2026-08-13: CODUSUR 588 na CRC tem
    # NOME='W.S' mas ESTADO nulo (pedido 588000124). Antes o AND aplicava
    # ESTADO='SP' em cima do OR inteiro e excluía esse caso; agora W.S
    # sempre conta, e só '%OFF TRADE%' precisa do ESTADO='SP'.
    nome_cond = (
        "(U.NOME = 'W.S' OR (U.NOME LIKE '%OFF TRADE%' AND U.ESTADO = 'SP'))"
        if filtra_por_estado else
        "(U.NOME LIKE '%OFF TRADE%' OR U.NOME = 'W.S')"
    )
    return f"""
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
        FROM {schema}.PCMOV M
        JOIN {schema}.PCUSUARI U  ON M.CODUSUR  = U.CODUSUR
        LEFT JOIN {schema}.PCCLIENT C ON M.CODCLI = C.CODCLI
        JOIN {schema}.PCPRODUT P  ON M.CODPROD  = P.CODPROD
        JOIN {schema}.PCFORNEC F  ON P.CODFORNEC = F.CODFORNEC
        WHERE M.DTMOV >= ADD_MONTHS(TRUNC(SYSDATE, 'MM'), -12)
          AND M.CODOPER IN ('S', 'SB', 'ED')
          AND M.DTCANCEL  IS NULL
          AND {nome_cond}
    """


def _id_str(v):
    """NUMPED/NUMNOTA vêm do Oracle como NUMBER (viram float no pandas) —
    formata sem casas decimais, ou '' se vazio/não numérico."""
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return ''
    try:
        return str(int(float(v)))
    except (TypeError, ValueError):
        return str(v).strip()


import re as _re_top

def _limpar_nome_vendedor(nome):
    """Mesma limpeza aplicada em _vh['VENDEDOR'] (regex sobre Series) —
    versão escalar pra usar nos itens extra de cancelado/corte, que vêm de
    PBI_PCPEDI/PEDIDOS_CANCELADOS, não de PCMOV."""
    s = _re_top.sub(r'\s*OFF\s*TRADE\s*(SP)?\s*', '', str(nome or ''), flags=_re_top.IGNORECASE)
    s = _re_top.sub(r'\s*-\s*$', '', s)
    return s.strip().upper()


# ── Pedidos cancelados/cortados, item a item (mesma lógica de
# exportacao_meta.py — GARRIDO não faz parte de _SP_SOURCES, então nem entra
# aqui; thekings não tem PEDIDOS_CANCELADOS, ver _SOURCES_SEM_THEKINGS) ──────

def _query_sp_cancelados_pos_nf(schema, filtra_por_estado):
    nome_cond = (
        "(U.NOME = 'W.S' OR (U.NOME LIKE '%OFF TRADE%' AND U.ESTADO = 'SP'))"
        if filtra_por_estado else
        "(U.NOME LIKE '%OFF TRADE%' OR U.NOME = 'W.S')"
    )
    return f"""
        SELECT
            PED.DATA AS DATA, PED.CODCLI AS CODCLI, PED.CLIENTE AS CLIENTE,
            PED.DESCRICAO AS PRODUTO, PED.FANTASIA_FORNEC AS FANTASIA,
            PED.QT AS QT, PED.TOTAL AS VALOR, PED.NUMPED AS NUMPED,
            PED.CODUSUR AS CODUSUR, U.NOME AS NOME_ORACLE
        FROM {schema}.PBI_PCPEDI PED
        JOIN {schema}.PCUSUARI U ON U.CODUSUR = PED.CODUSUR
        WHERE PED.DATA >= ADD_MONTHS(TRUNC(SYSDATE, 'MM'), -12)
          AND PED.STATUS = 'CANCELADA'
          AND {nome_cond}
    """


def _query_sp_cancelados_pre_nf(schema, filtra_por_estado):
    nome_cond = (
        "(U.NOME = 'W.S' OR (U.NOME LIKE '%OFF TRADE%' AND U.ESTADO = 'SP'))"
        if filtra_por_estado else
        "(U.NOME LIKE '%OFF TRADE%' OR U.NOME = 'W.S')"
    )
    return f"""
        SELECT
            PC.DATACANC AS DATA, PC.DESCRICAO AS PRODUTO,
            PC.QT AS QT, PC.SUBTOT AS VALOR, PC.NUMPED AS NUMPED,
            U.CODUSUR AS CODUSUR, U.NOME AS NOME_ORACLE
        FROM {schema}.PEDIDOS_CANCELADOS PC
        JOIN {schema}.PCUSUARI U ON U.CODUSUR = TO_NUMBER(SUBSTR(PC.NUMPED, 1, LENGTH(PC.NUMPED) - 6))
        WHERE PC.DATACANC >= ADD_MONTHS(TRUNC(SYSDATE, 'MM'), -12)
          AND {nome_cond}
    """


def _query_sp_corte_parcial(schema, filtra_por_estado):
    nome_cond = (
        "(U.NOME = 'W.S' OR (U.NOME LIKE '%OFF TRADE%' AND U.ESTADO = 'SP'))"
        if filtra_por_estado else
        "(U.NOME LIKE '%OFF TRADE%' OR U.NOME = 'W.S')"
    )
    return f"""
        SELECT
            PED.DATA AS DATA, PED.CODCLI AS CODCLI, PED.CLIENTE AS CLIENTE,
            PED.DESCRICAO AS PRODUTO, PED.FANTASIA_FORNEC AS FANTASIA,
            PED.CODUSUR AS CODUSUR, U.NOME AS NOME_ORACLE,
            PED.NUMPED AS NUMPED, PED.NUMNOTA AS NUMNOTA,
            PED.QTFALTA AS QTFALTA, PED.PVENDA AS PVENDA,
            NVL(CT.QTCORTADA, 0) AS QTCORTADA
        FROM {schema}.PBI_PCPEDI PED
        JOIN {schema}.PCUSUARI U ON U.CODUSUR = PED.CODUSUR
        LEFT JOIN (
            SELECT NUMPED, CODPROD, CODFILIAL, SUM(QTCORTADA) AS QTCORTADA
            FROM {schema}.PCCORTEI
            WHERE DATA >= ADD_MONTHS(TRUNC(SYSDATE, 'MM'), -12)
            GROUP BY NUMPED, CODPROD, CODFILIAL
        ) CT ON CT.NUMPED = PED.NUMPED AND CT.CODPROD = PED.CODPROD
             AND NVL(CT.CODFILIAL, 'x') = NVL(PED.CODFILIAL, 'x')
        WHERE PED.DATA >= ADD_MONTHS(TRUNC(SYSDATE, 'MM'), -12)
          AND PED.NUMNOTA IS NOT NULL
          AND NVL(PED.STATUS, 'X') != 'CANCELADA'
          AND {nome_cond}
    """


# PAPEL do vendedor (Representante/Externo/Supervisor/Gerente) pro badge em
# sp.html — mesma lógica de exportacao_meta.py: TIPOVEND (Oracle) só
# distingue Representante/Externo/Interno/Profissional, mas PCSUPERV/
# PCGERENTE têm coluna COD_CADRCA que liga o cadastro de supervisor/gerente
# ao PRÓPRIO cadastro de vendedor dele em PCUSUARI — quando isso acontece, o
# papel real (Supervisor/Gerente) tem prioridade sobre o TIPOVEND.
def _papel_de(tipovend, eh_gerente, eh_supervisor):
    if eh_gerente:
        return 'Gerente'
    if eh_supervisor:
        return 'Supervisor'
    return {'R': 'Representante', 'E': 'Externo'}.get(str(tipovend or '').strip().upper(), '')


def _query_sp_papel(schema, filtra_por_estado):
    nome_cond = (
        "(U.NOME = 'W.S' OR (U.NOME LIKE '%OFF TRADE%' AND U.ESTADO = 'SP'))"
        if filtra_por_estado else
        "(U.NOME LIKE '%OFF TRADE%' OR U.NOME = 'W.S')"
    )
    return f"""
        SELECT U.CODUSUR AS CODUSUR, U.TIPOVEND,
               CASE WHEN G.COD_CADRCA IS NOT NULL THEN 1 ELSE 0 END AS EH_GERENTE,
               CASE WHEN S.COD_CADRCA IS NOT NULL THEN 1 ELSE 0 END AS EH_SUPERVISOR
        FROM {schema}.PCUSUARI U
        LEFT JOIN {schema}.PCGERENTE G ON G.COD_CADRCA = U.CODUSUR
        LEFT JOIN {schema}.PCSUPERV  S ON S.COD_CADRCA = U.CODUSUR
        WHERE {nome_cond}
    """


_SOURCES_SEM_THEKINGS = [s for s in _SP_SOURCES if s[0] != 'thekings']


def _carregar_extra(query_fn, sources, sufixo):
    partes = []
    with ThreadPoolExecutor(max_workers=max(len(sources), 1)) as _ex:
        _futuros = {
            _ex.submit(carregar_dados, query_fn(_schema, _fe), _eng, f"{sufixo}_{_nome}"): (_nome, _schema)
            for _nome, _eng, _schema, _fe in sources
        }
        for _fut in as_completed(_futuros):
            _nome, _schema = _futuros[_fut]
            try:
                _df = _fut.result()
                if not _df.empty:
                    _df['SISTEMA'] = _nome
                    partes.append(_df)
            except Exception as _ex_err:
                print(f"[AVISO] {sufixo}_{_nome} falhou ({str(_ex_err)[:100]}) — ignorada")
    if not partes:
        return pd.DataFrame()
    return pd.concat(partes, ignore_index=True)


_itens_extra_por_vendedor: dict = {}


def _registrar_item_extra(nome_oracle, mes_str, item):
    nome_disp = _limpar_nome_vendedor(nome_oracle)
    if not nome_disp:
        return
    _itens_extra_por_vendedor.setdefault(nome_disp, {}).setdefault(mes_str, []).append(item)


_vc_cancel_pos = _carregar_extra(_query_sp_cancelados_pos_nf, _SP_SOURCES, "sp_cancel_pos")
_vc_cancel_pre = _carregar_extra(_query_sp_cancelados_pre_nf, _SOURCES_SEM_THEKINGS, "sp_cancel_pre")
_vc_corte      = _carregar_extra(_query_sp_corte_parcial, _SP_SOURCES, "sp_corte")

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
            'sistema':   str(row.get('SISTEMA') or ''),
        })
    print(f"OK vendas SP: {len(_vc_cancel_pos)} item(ns) cancelado(s) pós-NF")

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
            'sistema':   str(row.get('SISTEMA') or ''),
        })
    print(f"OK vendas SP: {len(_vc_cancel_pre)} item(ns) cancelado(s) pré-NF")

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
            'sistema':   str(row.get('SISTEMA') or ''),
        })
    print(f"OK vendas SP: {len(_vc_corte)} item(ns) com corte parcial")

# ── Carrega e limpa dados (todas as fontes em paralelo) ──────────────────────

_partes_vh = []
with ThreadPoolExecutor(max_workers=len(_SP_SOURCES)) as _ex:
    _futuros = {
        _ex.submit(carregar_dados, _query_vendas_sp(_schema, _fe), _eng, f"vendas_SP_{_nome}"): _nome
        for _nome, _eng, _schema, _fe in _SP_SOURCES
    }
    for _fut in as_completed(_futuros):
        _nome = _futuros[_fut]
        try:
            _df = _fut.result()
            if not _df.empty:
                # CODUSUR é atribuído independentemente por sistema — dois
                # vendedores diferentes em bases diferentes podem ter o
                # mesmo número (mesmo motivo de meta.py::nome_display_por_oracle).
                # Marca a origem pra nunca agrupar/mapear só por CODUSUR cru.
                _df['SISTEMA'] = _nome
                _partes_vh.append(_df)
        except Exception as _ex_err:
            print(f"[AVISO] vendas_SP_{_nome} falhou ({str(_ex_err)[:100]}) — ignorada")

if not _partes_vh:
    raise RuntimeError("Nenhuma fonte de vendas SP disponível — todas as bases Oracle estão fora do ar.")
_vh = pd.concat(_partes_vh, ignore_index=True)

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

# Remove sufixo/prefixo "OFF TRADE" do nome para exibição
import re
_vh['VENDEDOR'] = (
    _vh['NOME_ORACLE']
    .str.replace(r'\s*OFF\s*TRADE\s*(SP)?\s*', '', regex=True, case=False)
    .str.replace(r'\s*-\s*$', '', regex=True)
    .str.strip()
    .str.upper()
)
_vh = _vh[_vh['VENDEDOR'].notna() & (_vh['VENDEDOR'] != '')].copy()

print(f"Vendedores SP encontrados: {sorted(_vh['VENDEDOR'].unique())}")

# ── Monta mapa nome → RCA ─────────────────────────────────────────────────────
# Fica com o CODUSUR cru (não "SISTEMA/CODUSUR"): sp.html compara isso contra
# sessionStorage.rg_vendedor.rca no login (VENDEDORES_AUTH, também por
# CODUSUR cru — ver exportacao_vendedores_auth.py e o caso Jeter) — prefixar
# aqui quebraria esse match e derrubaria o acesso do vendedor à própria
# página. Colisão de CODUSUR entre sistemas não é problema aqui porque a
# chave do dict já é o NOME (uma pessoa só), diferente de _codusur_nome
# abaixo (que resolve o sentido contrário e precisa do par SISTEMA+CODUSUR).
_rcas: dict[str, str] = {}
for _, row in _vh[['VENDEDOR', 'CODUSUR']].drop_duplicates(subset=['VENDEDOR']).iterrows():
    _rcas[row['VENDEDOR']] = str(int(row['CODUSUR']))

# ── Metas SP ──────────────────────────────────────────────────────────────────

import unicodedata
import baixar_planilhas_drive as _bpd

def _norm_col(s):
    return unicodedata.normalize('NFKD', str(s)).encode('ascii', 'ignore').decode().upper().strip()

def _limpar_nome_sp(nome):
    return (
        re.sub(r'\s*-?\s*OFF\s*TRADE\s*(SP)?\s*', ' ', str(nome), flags=re.IGNORECASE)
        .strip()
        .upper()
    )

_metas_sp: dict = {}
try:
    _df_m = pd.read_excel(_bpd.com_fallback(
        _bpd.caminho_metas_sp,
        r"G:\Drives compartilhados\Off Trade\Campanhas e Metas\METAS\METAS SP.xlsx"
    ))
    _df_m.columns = _df_m.columns.str.strip()
    _cn = {_norm_col(c): c for c in _df_m.columns}  # mapa normalizado → nome real

    def _meta_col(key):
        return _cn.get(_norm_col(key))

    _df_m['_VEND'] = _df_m[_meta_col('VENDEDOR')].apply(_limpar_nome_sp)

    _col_mes = _meta_col('MES') or _meta_col('MÊS')
    _df_m['_MES'] = pd.to_datetime(_df_m[_col_mes], errors='coerce').apply(
        lambda d: _mes_pt(d) if pd.notna(d) else None
    )

    for _, row in _df_m.iterrows():
        nome = row['_VEND']
        mes  = row['_MES']
        if not nome or not mes:
            continue

        def _v(col_key):
            c = _meta_col(col_key)
            if c is None:
                return None
            val = row.get(c)
            return float(val) if pd.notna(val) and val != 0 else None

        _metas_sp.setdefault(nome, {})[mes] = {
            'fat':         _v('META FATURAMENTO'),
            'pos':         _v('META POSITIVACAO'),
            'fat_pernod':  _v('META FATURAMENTO PERNOD'),
            'fat_crs':     _v('META FATURAMENTO CRS'),
            'fat_essenza': _v('META FATURAMENTO ESSENZA'),
        }

    print(f"Metas SP: {len(_metas_sp)} vendedores")
except FileNotFoundError:
    print("AVISO: METAS SP.xlsx não encontrado — metas ignoradas")
except Exception as _ex:
    print(f"AVISO: Erro ao carregar metas SP: {_ex}")

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
        'tipo':     {'SB': 'Bonificado', 'ED': 'Devolução'}.get(str(row.get('CODOPER', 'S')).upper(), 'Venda'),
        'offtrade': row['OFFTRADE'] == 'S',
        'devolvido': bool(row['DEVOLVIDO']),
        'cancelado': False,
        'cancelado_parcial': False,
        'numped':   '',
        'nunota':   _id_str(row.get('NUNOTA')),
        'sistema':  str(row.get('SISTEMA') or ''),
    })

# Mescla os itens de cancelado/corte parcial (não vêm de _vh/PCMOV) — pode
# incluir mês que _vh não tinha nenhuma venda válida, então _meses_str
# precisa ser recalculado incluindo esses meses também (mesmo padrão de
# exportacao_meta.py).
for v_nome, por_mes in _itens_extra_por_vendedor.items():
    for mes, itens in por_mes.items():
        _por_vendedor.setdefault(v_nome, {}).setdefault(mes, []).extend(itens)

_meses_extra = {mes for por_mes in _itens_extra_por_vendedor.values() for mes in por_mes}
_meses_str = sorted(set(_meses_str) | _meses_extra, key=_mes_sort_key, reverse=True)

# ── Escreve vendas_sp_data.js ─────────────────────────────────────────────────

# ── Realizado SP (pré-calculado com mesma lógica do CALCULOS_META) ────────────

# Agrupa por (SISTEMA, CODUSUR), não só CODUSUR — número colide entre bases
# (ver aviso sobre SISTEMA acima).
_CALCULOS_SP = {
    'fat':         lambda df: df.groupby(['SISTEMA', 'CODUSUR'])['VALOR'].sum(),
    # Positivação conta qualquer cliente que comprou, sem exigir
    # PCCLIENT.OFFTRADE='S' — mesmo critério aplicado em exportacao_meta.py
    # (RJ) em 2026-08-25, a pedido do usuário.
    'pos':         lambda df: df.groupby(['SISTEMA', 'CODUSUR'])['CODCLI'].nunique(),
    'fat_pernod':  lambda df: df[df['FANTASIA'].str.contains('PERNOD',     na=False, case=False)].groupby(['SISTEMA', 'CODUSUR'])['VALOR'].sum(),
    'fat_crs':     lambda df: df[df['FANTASIA'].str.contains('CRS BRANDS', na=False, case=False)].groupby(['SISTEMA', 'CODUSUR'])['VALOR'].sum(),
    'fat_essenza': lambda df: df[df['PRODUTO'].str.contains('ESSENZA',     na=False, case=False)].groupby(['SISTEMA', 'CODUSUR'])['VALOR'].sum(),
}

_codusur_nome = {
    (row['SISTEMA'], int(row['CODUSUR'])): row['VENDEDOR']
    for _, row in _vh[['SISTEMA', 'CODUSUR', 'VENDEDOR']].drop_duplicates().iterrows()
    if pd.notna(row['CODUSUR'])
}

# ── Papel do vendedor (Representante/Externo/Supervisor/Gerente) ─────────────
# Um mesmo CODUSUR pode existir em mais de um schema com cadastros
# diferentes (ex: RCA 588/W.S: TIPOVEND='R' na SPON, mas 'E' na CRC/CASTAS/
# BLENDED — confirmado com o usuário em 2026-09-01, badge mostrando
# "Externo" quando o sistema mostra "Representante"). _carregar_extra roda
# as fontes em paralelo (ThreadPoolExecutor), então a ordem de chegada em
# _vc_papel não é determinística — iterar "quem chegar por último vence"
# fazia o badge variar de execução pra execução dependendo de qual thread
# terminava primeiro. Prioriza a ordem de _SP_SOURCES (SPON primeiro — é a
# base dona de SP, mesma convenção de _BASE_DONA_DO_ESTADO em
# exportacao_meta.py) com "primeiro que aparece vence".
_prioridade_sistema = {nome: i for i, (nome, *_resto) in enumerate(_SP_SOURCES)}
_vc_papel = _carregar_extra(_query_sp_papel, _SP_SOURCES, "sp_papel")
_tipovenda: dict[str, str] = {}
if not _vc_papel.empty:
    _vc_papel['_PRIORIDADE'] = _vc_papel['SISTEMA'].map(_prioridade_sistema).fillna(999)
    for _, _row in _vc_papel.sort_values('_PRIORIDADE').iterrows():
        if pd.isna(_row.get('CODUSUR')):
            continue
        _nome = _codusur_nome.get((_row['SISTEMA'], int(_row['CODUSUR'])))
        if not _nome or _nome in _tipovenda:
            continue
        _papel = _papel_de(_row.get('TIPOVEND'), _row.get('EH_GERENTE'), _row.get('EH_SUPERVISOR'))
        if _papel:
            _tipovenda[_nome] = _papel

_realizado_sp: dict = {}
for _mes_ts in _meses_vh:
    _mes_str = _mes_pt(_mes_ts)
    # Só venda de verdade (CODOPER='S') entra no faturamento/positivação —
    # bonificado (SB) e devolução (ED) agora fazem parte de _vh (pra
    # aparecer com o status certo em vendas_sp_data.js), mas não podem
    # inflar o KPI de faturamento realizado. CODOPER='S' sozinho não basta:
    # uma venda devolvida depois (NUMNOTADEV) continua com CODOPER='S' na
    # própria linha de saída — só a coluna DEVOLVIDO pega esse caso (mesmo
    # bug encontrado e corrigido em exportacao_meta.py::_query_historico
    # em 2026-08-25).
    _df_mes  = _vh[(_vh['MES'] == _mes_ts) & (_vh['CODOPER'] == 'S') & (~_vh['DEVOLVIDO'])]
    for _key, _fn in _CALCULOS_SP.items():
        try:
            for (_sistema, _codusur), _val in _fn(_df_mes).items():
                _nome = _codusur_nome.get((_sistema, int(_codusur)), f"{_sistema}/{_codusur}")
                _realizado_sp.setdefault(_nome, {}).setdefault(_mes_str, {})[_key] = round(float(_val), 2)
        except Exception:
            pass

# fat_ant (mês anterior) e fat_ano_ant (mesmo mês do ano anterior) — mesma
# lógica de exportacao_mg.py/exportacao_es.py, calculada em cima do 'fat' já
# populado em _realizado_sp acima.
_meses_sorted = sorted(_meses_str, key=_mes_sort_key, reverse=True)
for _nome in _realizado_sp:
    for _i, _mes in enumerate(_meses_sorted):
        if _mes not in _realizado_sp[_nome]:
            continue
        if _i + 1 < len(_meses_sorted):
            _ant = _meses_sorted[_i + 1]
            _realizado_sp[_nome][_mes]['fat_ant'] = _realizado_sp[_nome].get(_ant, {}).get('fat', 0.0)
        if _i + 12 < len(_meses_sorted):
            _ano_ant = _meses_sorted[_i + 12]
            _realizado_sp[_nome][_mes]['fat_ano_ant'] = _realizado_sp[_nome].get(_ano_ant, {}).get('fat', 0.0)

print(f"Realizado SP: {len(_realizado_sp)} vendedores")

payload = {
    'atualizado_em': datetime.now().strftime('%d/%m/%Y %H:%M'),
    'meses':         _meses_str,
    'rcas':          _rcas,
    'tipovenda':     _tipovenda,
    'por_vendedor':  _por_vendedor,
    'metas':         _metas_sp,
    'realizado':     _realizado_sp,
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

git_commit_push(["vendas_sp_data.js", "sp.html"],
                f"Atualiza vendas_sp_data.js - {date.today().strftime('%d/%m/%Y')}")
