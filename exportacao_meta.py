# EXPORTAÇÃO PARA metas.html
import json
import os
import pandas as pd
from datetime import date, datetime
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
import re as _re
from meta import engine, engine_theking, engine_castas, engine_garrido, engine_spon, engine_mgon, arquivo, carregar_dados, carregar_paralelo, FONTES_INDISPONIVEIS, nome_display_por_oracle
import nao_positivados as _np_mod

_df_nao_pos = _np_mod.nao_positivados_full

def _write_js_atomic(path, content):
    """Grava em arquivo temporário e renomeia, para que páginas nunca leiam um JS truncado a meio-caminho de uma gravação."""
    tmp_path = f"{path}.tmp"
    with open(tmp_path, 'w', encoding='utf-8') as f:
        f.write(content)
    os.replace(tmp_path, path)

# ── Helpers de mês (PT) ───────────────────────────────────────────────────────

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

# ── Mapeamento RCA → Nome Oracle ─────────────────────────────────────────────

_MAP_RCA_CONFIGS = [
    ("CRC",      engine,         "map_rca_CRC",      "NOME LIKE '%OFF TRADE%'"),
    ("thekings", engine_theking, "map_rca_thekings", "NOME LIKE '%OFF TRADE%'"),
    ("CASTAS",   engine_castas,  "map_rca_CASTAS",   "NOME LIKE '%OFF TRADE%'"),
    ("GARRIDO",  engine_garrido, "map_rca_GARRIDO",  "NOME LIKE '%OFF TRADE%'"),
    ("SPON",     engine_spon,    "map_rca_SPON",     "NOME LIKE '%OFF TRADE%' OR NOME LIKE '%W.S%'"),
    ("MGON",     engine_mgon,    "map_rca_MGON",     "NOME LIKE '%OFF TRADE%' OR NOME LIKE '%W.S%'"),
]

_parts_map_rca = []
_chamadas_map_rca = [
    (f"SELECT CODUSUR AS RCA, NOME, TIPOVEND FROM {_s}.PCUSUARI WHERE {_extra_f}", _e, _n)
    for _s, _e, _n, _extra_f in _MAP_RCA_CONFIGS
]
for (_s, _e, _n, _extra_f), _res in zip(_MAP_RCA_CONFIGS, carregar_paralelo(_chamadas_map_rca)):
    if isinstance(_res, Exception):
        print(f"[AVISO] {_n} falhou — ignorado")
        FONTES_INDISPONIVEIS.append(_n)
    else:
        _parts_map_rca.append(_res)
if not _parts_map_rca:
    raise RuntimeError("Nenhuma fonte de RCA disponível — todas as bases Oracle estão fora do ar.")
map_rca = pd.concat(_parts_map_rca, ignore_index=True)
map_rca = map_rca.drop_duplicates(subset=['RCA'])
map_rca['RCA'] = pd.to_numeric(map_rca['RCA'], errors='coerce')
arquivo['RCA'] = pd.to_numeric(arquivo['RCA'],  errors='coerce')

# Normaliza coluna MÊS do arquivo → "Mar/26"
mes_col = 'MÊS' if 'MÊS' in arquivo.columns else 'MES'
arquivo['MES_STR'] = pd.to_datetime(arquivo[mes_col], errors='coerce').apply(
    lambda d: _mes_pt(d) if pd.notna(d) else None
)

metas_com_nome = arquivo.merge(map_rca, on='RCA', how='left')

# ── Vendedores OFF TRADE ativos sem meta na planilha ─────────────────────────
# RCA OFF TRADE (RJ, BLOQUEIO != 'S') que não têm linha na METAS RJ.xlsx ainda
# precisam aparecer no metas.html (ex.: RCA 283/Kessya) — sem meta, com zeros.
# Consulta separada da `map_rca` acima: ESTADO/BLOQUEIO podem não existir em
# todas as bases, e o objetivo aqui é só ACRESCENTAR vendedores, nunca alterar
# o join principal (que já funciona pros vendedores com meta cadastrada).
def _carregar_status_rca(_s, _e, _n, _extra_f):
    try:
        return carregar_dados(
            f"SELECT CODUSUR AS RCA, NOME, TIPOVEND, ESTADO, BLOQUEIO FROM {_s}.PCUSUARI WHERE {_extra_f}",
            _e, f"{_n}_status",
        )
    except Exception as _ex:
        print(f"[AVISO] status_rca {_n} indisponível ({str(_ex)[:80]}) — sem detecção de vendedor sem meta nesta base")
        return None

_parts_status_rca = []
with ThreadPoolExecutor(max_workers=len(_MAP_RCA_CONFIGS)) as _status_ex:
    _status_futuros = {
        _status_ex.submit(_carregar_status_rca, _s, _e, _n, _extra_f): _n
        for _s, _e, _n, _extra_f in _MAP_RCA_CONFIGS
    }
    for _fut in as_completed(_status_futuros):
        _df_status = _fut.result()
        if _df_status is not None:
            _parts_status_rca.append(_df_status)

if _parts_status_rca:
    _status_rca = pd.concat(_parts_status_rca, ignore_index=True)
    _status_rca = _status_rca.drop_duplicates(subset=['RCA'])
    _status_rca['RCA'] = pd.to_numeric(_status_rca['RCA'], errors='coerce')
    _status_rca['ESTADO']   = _status_rca['ESTADO'].astype(str).str.strip().str.upper()
    _status_rca['BLOQUEIO'] = _status_rca['BLOQUEIO'].astype(str).str.strip().str.upper()
    _status_rca_ativos = _status_rca[
        (_status_rca['ESTADO'] == 'RJ') & (_status_rca['BLOQUEIO'] != 'S')
    ]

    # Compara por NOME, não por RCA: o mesmo CODUSUR pode ser gente diferente
    # em bases diferentes, e a mesma pessoa pode ter RCA diferente em cada
    # base (mesmo motivo documentado em meta.nome_display_por_oracle) — RCA
    # 91/419/417 (Viviani, Nátali, Dirlei) já têm meta cadastrada mas também
    # existem sob outro RCA em outra base; comparar por RCA os tratava como
    # "sem meta" e zerava a meta real deles (bug confirmado em 2026-08-04).
    _nomes_com_meta = set(metas_com_nome['NOME'].dropna().unique())
    _rca_sem_meta = _status_rca_ativos[
        ~_status_rca_ativos['NOME'].isin(_nomes_com_meta)
    ].drop_duplicates(subset=['NOME'])
    if not _rca_sem_meta.empty:
        _meses_disponiveis = arquivo['MES_STR'].dropna().unique().tolist()
        _extras = [
            {'RCA': _r['RCA'], 'NOME': _r['NOME'], 'TIPOVEND': _r.get('TIPOVEND'),
             'VENDEDOR': _r['NOME'], 'MES_STR': _mes}
            for _, _r in _rca_sem_meta.iterrows()
            for _mes in _meses_disponiveis
        ]
        metas_com_nome = pd.concat([metas_com_nome, pd.DataFrame(_extras)], ignore_index=True)
        print(f"OK {len(_rca_sem_meta)} vendedor(es) OFF TRADE ativo(s) sem meta cadastrada adicionados: "
              f"{', '.join(_rca_sem_meta['NOME'].tolist())}")

# ── Vendas históricas (6 meses) com FANTASIA ─────────────────────────────────

def _nome_filter(extra_nomes=None):
    """Retorna cláusula SQL de filtro por nome, incluindo padrões extras."""
    base = "U.NOME LIKE '%OFF TRADE%'"
    if extra_nomes:
        extras = " OR ".join(f"U.NOME LIKE '{p}'" for p in extra_nomes)
        return f"({base} OR {extras})"
    return base

def _query_vendas_historico(schema, filtro_filial="(1, 2, 4)", filtro_estent=None, extra_nomes=None, tem_offtrade=True):
    s = schema.upper()
    extra_filial = f"\n          AND M.CODFILIAL IN {filtro_filial}" if filtro_filial else ""
    extra_estent = f"\n          AND U.ESTADO = '{filtro_estent}'" if filtro_estent else ""
    nome_f = _nome_filter(extra_nomes)
    # PCCLIENT.OFFTRADE não existe em todas as bases (ex.: GARRIDO) — nesses
    # casos usa 'S' literal pra não filtrar indevidamente a positivação.
    offtrade_col = "C.OFFTRADE" if tem_offtrade else "'S'"
    return f"""
        SELECT
            TRUNC(M.DTMOV, 'MM')            AS MES,
            TO_CHAR(M.DTMOV, 'DD/MM/YYYY') AS DATA,
            M.CODCLI,
            C.CLIENTE,
            M.DESCRICAO                     AS PRODUTO,
            F.FANTASIA,
            M.QT,
            (M.PUNIT * M.QT)               AS VALOR,
            M.CODOPER,
            U.CODUSUR                       AS RCA,
            U.NOME                          AS NOME_ORACLE,
            {offtrade_col}                  AS OFFTRADE,
            CASE WHEN M.CODOPER = 'ED' OR M.NUMNOTADEV IS NOT NULL THEN 'S' ELSE 'N' END AS DEVOLVIDO
        FROM {s}.PCMOV M
        JOIN {s}.PCUSUARI U ON M.CODUSUR = U.CODUSUR
        LEFT JOIN {s}.PCCLIENT C ON M.CODCLI = C.CODCLI
        JOIN {s}.PCPRODUT P ON M.CODPROD = P.CODPROD
        JOIN {s}.PCFORNEC F ON P.CODFORNEC = F.CODFORNEC
        WHERE M.DTMOV >= ADD_MONTHS(TRUNC(SYSDATE, 'MM'), -5)
          AND M.CODOPER IN ('S', 'SB', 'ED')
          AND M.DTCANCEL IS NULL
          AND {nome_f}{extra_filial}{extra_estent}
    """

_SPON_EXTRA = ['%W.S%']

_VH_CONFIGS = [
    ("CRC",     engine,         "vendas_hist_CRC",     "(1, 2, 4)", None,  "CRC",       None,       True),
    ("thekings",engine_theking, "vendas_hist_thekings","(1, 2, 4)", None,  "The Kings", None,       True),
    ("CASTAS",  engine_castas,  "vendas_hist_CASTAS",  None,        None,  "Castas",    None,       True),
    ("GARRIDO", engine_garrido, "vendas_hist_GARRIDO", None,        None,  "Garrido",   None,       False),
    ("SPON",    engine_spon,    "vendas_hist_SPON",    None,        None,  "SPON",      _SPON_EXTRA, True),
    ("MGON",    engine_mgon,    "vendas_hist_MGON",    "(1, 2)",    None,  "MGON",      _SPON_EXTRA, True),
]
_vh_parts = []
_chamadas_vh = [
    (_query_vendas_historico(_s, filtro_filial=_ff, filtro_estent=_fe, extra_nomes=_en, tem_offtrade=_to), _e, _n)
    for _s, _e, _n, _ff, _fe, _emp, _en, _to in _VH_CONFIGS
]
for (_s, _e, _n, _ff, _fe, _emp, _en, _to), _res in zip(_VH_CONFIGS, carregar_paralelo(_chamadas_vh)):
    if isinstance(_res, Exception):
        print(f"[AVISO] {_n} falhou — ignorado")
        FONTES_INDISPONIVEIS.append(_n)
    else:
        _res['EMPRESA'] = _emp
        _vh_parts.append(_res)
_vh = pd.concat(_vh_parts, ignore_index=True)
_vh['MES']    = pd.to_datetime(_vh['MES'],   errors='coerce')
_vh['QT']     = pd.to_numeric(_vh['QT'],     errors='coerce').fillna(0).astype(int)
_vh['VALOR']  = pd.to_numeric(_vh['VALOR'],  errors='coerce').fillna(0).round(2)
_vh['RCA']    = pd.to_numeric(_vh['RCA'],    errors='coerce')
_vh['CLIENTE']  = _vh['CLIENTE'].fillna(_vh['CODCLI'])
_vh['FANTASIA'] = _vh['FANTASIA'].fillna('')
_vh['PRODUTO']  = _vh['PRODUTO'].fillna('')
_vh['OFFTRADE'] = _vh['OFFTRADE'].fillna('N')
_vh['DEVOLVIDO'] = _vh['DEVOLVIDO'].fillna('N') == 'S'

# Oracle name → display name (ver meta.nome_display_por_oracle)
_oracle_to_display = nome_display_por_oracle(metas_com_nome)
# NOME_ORACLE já vem correto por linha (JOIN feito dentro do schema de origem
# da venda) — usa ele direto em vez de reidentificar o vendedor por RCA global.
_vh['VENDEDOR'] = _vh['NOME_ORACLE'].map(_oracle_to_display).fillna(_vh['NOME_ORACLE'])
_vh = _vh[_vh['VENDEDOR'].notna()].copy()
_vh['MES_STR'] = _vh['MES'].apply(_mes_pt)

_vh_grouped = _vh.groupby(['VENDEDOR', 'MES_STR'])

# ── Clientes cadastrados no mês ──────────────────────────────────────────────
# Usa o mês mais recente do arquivo de metas para evitar zero no início do mês

_MESES_NUM = {'Jan': 1, 'Fev': 2, 'Mar': 3, 'Abr': 4, 'Mai': 5, 'Jun': 6,
              'Jul': 7, 'Ago': 8, 'Set': 9, 'Out': 10, 'Nov': 11, 'Dez': 12}

_meses_arq_sorted = sorted(
    arquivo['MES_STR'].dropna().unique().tolist(),
    key=_mes_sort_key, reverse=True,
)
_ref_date_cad = None
if _meses_arq_sorted:
    _m_nome, _m_ano = _meses_arq_sorted[0].split('/')
    _ref_date_cad = f'20{_m_ano}-{_MESES_NUM.get(_m_nome, 1):02d}-01'

def _cnpj_chave(cgc):
    d = _re.sub(r'\D', '', str(cgc) if cgc else '')
    return d[:8] if len(d) == 14 else (d if d else None)

def _query_cadastros(schema, col_usur="CODUSUR1", tem_offtrade=True):
    s = schema.upper()
    filtro = (
        f"TRUNC(C.DTCADASTRO, 'MM') = DATE '{_ref_date_cad}'"
        if _ref_date_cad
        else "TRUNC(C.DTCADASTRO, 'MM') = TRUNC(SYSDATE, 'MM')"
    )
    # PCCLIENT.OFFTRADE não existe em todas as bases (ex.: GARRIDO) — nesses
    # casos não filtra por essa flag, mantém comportamento anterior.
    offtrade_clausula = "\n          AND C.OFFTRADE = 'S'" if tem_offtrade else ""
    return f"""
        SELECT C.CODCLI, C.CGCENT AS CGC, U.NOME AS NOME_RCA
        FROM {s}.PCCLIENT C
        JOIN {s}.PCUSUARI U ON C.{col_usur} = U.CODUSUR
        WHERE U.NOME LIKE '%OFF TRADE%'
          AND {filtro}{offtrade_clausula}
    """

_cadastros_por_display: dict = {}
try:
    _CADASTROS_CONFIGS = [("CRC", engine, "CODUSUR1", True), ("thekings", engine_theking, "CODUSUR1", True), ("spon", engine_spon, "CODUSUR1", True), ("CASTAS", engine_castas, "CODUSUR1", True), ("GARRIDO", engine_garrido, "CODUSUR1", False)]
    _frames_cad = []
    _chamadas_cad = [
        (_query_cadastros(_schema, _col, tem_offtrade=_to), _eng, f"cadastros_{_schema}")
        for _schema, _eng, _col, _to in _CADASTROS_CONFIGS
    ]
    for (_schema, _eng, _col, _to), _res in zip(_CADASTROS_CONFIGS, carregar_paralelo(_chamadas_cad)):
        if isinstance(_res, Exception):
            print(f"[AVISO] cadastros {_schema} falhou ({str(_res)[:80]}) — ignorado")
            FONTES_INDISPONIVEIS.append(f"cadastros_{_schema}")
        else:
            _res.columns = _res.columns.str.upper()
            _res['_SRC'] = _schema
            _frames_cad.append(_res)
    if _frames_cad:
        _df_cad = pd.concat(_frames_cad, ignore_index=True)
        _df_cad['_ID'] = _df_cad.apply(
            lambda r: _cnpj_chave(r['CGC']) or f"{r['_SRC']}_{r['CODCLI']}", axis=1
        )
        for _nome_oracle, _grp in _df_cad.groupby('NOME_RCA'):
            _display = _oracle_to_display.get(str(_nome_oracle))
            if _display:
                _cadastros_por_display[_display] = int(_grp['_ID'].nunique())
        print(f"OK cadastros: {len(_df_cad)} clientes, {len(_cadastros_por_display)} vendedores")
except Exception as _e:
    print(f"[AVISO] cadastros_mes falhou ({str(_e)[:100]}) — ignorado")

# ── Helpers de cálculo ────────────────────────────────────────────────────────

def safe_int(v):
    try: return int(v) if pd.notna(v) else 0
    except: return 0

def safe_float(v):
    try: return float(v) if pd.notna(v) else 0.0
    except: return 0.0

def coalesce(row, *keys):
    for k in keys:
        v = row.get(k)
        if v is not None and pd.notna(v):
            return v
    return None

def _realizado_mes(df):
    """Calcula todas as métricas realizadas a partir de um subset de _vh."""
    _zero = dict(fat_tt=0.0, fat_castas=0.0, fat_domecq_passport=0.0, fat_hob_azeite=0.0, fat_pinatti=0.0, fat_moving=0.0,
                 fat_pernod=0.0,
                 pos_tt=0, pos_hob_azeite=0, pos_reckit=0, pos_crusoe=0,
                 pos_tatuzinho=0, pos_tial=0, pos_redbull=0, pos_pinatti=0, pos_essenza_hob=0,
                 bonus_pernod=0.0, pos_pernod=0, industrias=0)
    if df.empty:
        return _zero

    # Vendas devolvidas continuam aparecendo na listagem "Faturamento do mês"
    # (destacadas em vermelho — pedido em 2026-08-03), mas não contam pra
    # nenhum KPI/meta aqui, senão faturamento/positivação realizados subiriam
    # com vendas que já foram estornadas.
    df = df[~df['DEVOLVIDO']]
    if df.empty:
        return _zero

    def fat(mask=None):
        sub = df[mask] if mask is not None else df
        return float(sub['VALOR'].sum().round(2))

    # Positivação (contagem de clientes) só considera clientes com PCCLIENT.OFFTRADE = 'S'
    # — faturamento (fat) continua somando todas as vendas do vendedor OFF TRADE, sem esse filtro.
    _off_mask = df['OFFTRADE'] == 'S'

    def pos(mask=None):
        sub = df[_off_mask & mask] if mask is not None else df[_off_mask]
        return int(sub['CODCLI'].nunique())

    # Campanha PERNOD: pares únicos (cliente, produto) com FANTASIA=PERNOD; JAMERSON=10, demais=5
    mask_pernod   = df['FANTASIA'].str.contains('PERNOD', case=False, na=False)
    pernod_pairs  = df[mask_pernod & _off_mask].drop_duplicates(subset=['CODCLI', 'PRODUTO'])
    mask_jamerson = pernod_pairs['PRODUTO'].str.contains('JAMERSON', case=False, na=False)
    n_jamerson    = int(mask_jamerson.sum())
    n_outros      = int((~mask_jamerson).sum())
    bonus_pernod  = n_jamerson * 10 + n_outros * 5
    pos_pernod    = n_jamerson + n_outros

    mask_essenza_hob = df['PRODUTO'].str.contains('ESSENZA', case=False, na=False) | df['FANTASIA'].str.contains('HOB', case=False, na=False)

    return {
        'fat_tt':              fat(),
        'fat_castas':          fat(df['FANTASIA'].str.contains('castas', case=False, na=False)),
        'fat_domecq_passport': fat(df['PRODUTO'].str.contains('DOMECQ|PASSPORT', case=False, na=False)),
        'fat_hob_azeite':      fat(df['PRODUTO'].str.contains('AZEITE', case=False, na=False) | df['FANTASIA'].str.contains('HOB', case=False, na=False)),
        'fat_pinatti':         fat(df['FANTASIA'].str.contains('PINATI', case=False, na=False)),
        'fat_moving':          fat(df['PRODUTO'].str.contains('MOVING', case=False, na=False)),
        'fat_pernod':          fat(mask_pernod),
        'pos_tt':              pos(),
        'pos_hob_azeite':      pos(df['PRODUTO'].str.contains('AZEITE', case=False, na=False) | df['FANTASIA'].str.contains('HOB', case=False, na=False)),
        'pos_reckit':          pos(df['FANTASIA'].str.contains('RECKIT', case=False, na=False)),
        'pos_crusoe':          pos(df['FANTASIA'].str.contains('ROBINSON CRUSOE', case=False, na=False)),
        'pos_tatuzinho':       pos(df['FANTASIA'].str.contains('TATUZINHO', case=False, na=False)),
        'pos_tial':            pos(df['FANTASIA'].str.contains('TIAL', case=False, na=False)),
        'pos_redbull':         pos(df['FANTASIA'].str.contains('RED BULL', case=False, na=False)),
        'pos_pinatti':         pos(df['FANTASIA'].str.contains('PINATI', case=False, na=False)),
        'pos_essenza_hob':     pos(mask_essenza_hob),
        'bonus_pernod':        bonus_pernod,
        'pos_pernod':          pos_pernod,
        'industrias':          int(df[df['FANTASIA'] != '']['FANTASIA'].nunique()),
    }

# ── Histórico mensal agregado (para gráficos) ─────────────────────────────────

def _query_historico(schema, filtro_filial="(1, 2, 4)", filtro_estent=None, extra_nomes=None, tem_offtrade=True):
    s = schema.upper()
    extra_filial = f"\n          AND M.CODFILIAL IN {filtro_filial}" if filtro_filial else ""
    extra_estent = f"\n          AND U.ESTADO = '{filtro_estent}'" if filtro_estent else ""
    nome_f = _nome_filter(extra_nomes)
    # Positivação só conta clientes com PCCLIENT.OFFTRADE = 'S'; onde a coluna
    # não existe (ex.: GARRIDO), conta todo mundo (não há como filtrar).
    positivacao_expr = "COUNT(DISTINCT CASE WHEN C.OFFTRADE = 'S' THEN M.CODCLI END)" if tem_offtrade else "COUNT(DISTINCT M.CODCLI)"
    return f"""
        SELECT
            TRUNC(M.DTMOV, 'MM')         AS MES,
            M.CODUSUR                     AS CODUSUR,
            U.NOME                         AS NOME_ORACLE,
            SUM(M.PUNIT * M.QT)          AS FATURAMENTO,
            {positivacao_expr}           AS POSITIVACAO
        FROM {s}.PCMOV M
        JOIN {s}.PCUSUARI U ON M.CODUSUR = U.CODUSUR
        LEFT JOIN {s}.PCCLIENT C ON M.CODCLI = C.CODCLI
        WHERE M.DTMOV >= ADD_MONTHS(TRUNC(SYSDATE, 'MM'), -12)
          AND M.CODOPER IN ('S', 'SB')
          AND M.NUMNOTADEV IS NULL
          AND M.DTCANCEL IS NULL
          AND {nome_f}{extra_filial}{extra_estent}
        GROUP BY TRUNC(M.DTMOV, 'MM'), M.CODUSUR, U.NOME
    """

_HIST_CONFIGS = [
    ("CRC",      engine,         "historico_CRC",      "(1, 2, 4)", None, None,        True),
    ("thekings", engine_theking, "historico_thekings",  "(1, 2, 4)", None, None,        True),
    ("CASTAS",   engine_castas,  "historico_CASTAS",    None,        None, None,        True),
    ("GARRIDO",  engine_garrido, "historico_GARRIDO",   None,        None, None,        False),
    ("SPON",     engine_spon,    "historico_SPON",      None,        None, _SPON_EXTRA, True),
]
_hist_parts = []
_chamadas_hist = [
    (_query_historico(_s, filtro_filial=_ff, filtro_estent=_fe, extra_nomes=_en, tem_offtrade=_to), _e, _n)
    for _s, _e, _n, _ff, _fe, _en, _to in _HIST_CONFIGS
]
for (_s, _e, _n, _ff, _fe, _en, _to), _res in zip(_HIST_CONFIGS, carregar_paralelo(_chamadas_hist)):
    if isinstance(_res, Exception):
        print(f"[AVISO] {_n} falhou — ignorado")
        FONTES_INDISPONIVEIS.append(_n)
    else:
        _hist_parts.append(_res)
if not _hist_parts:
    raise RuntimeError("Nenhuma fonte de histórico disponível — todas as bases Oracle estão fora do ar.")
_hist_raw = pd.concat(_hist_parts, ignore_index=True)
_hist_raw['MES']         = pd.to_datetime(_hist_raw['MES'], errors='coerce')
_hist_raw['FATURAMENTO'] = pd.to_numeric(_hist_raw['FATURAMENTO'], errors='coerce').fillna(0)
_hist_raw['POSITIVACAO'] = pd.to_numeric(_hist_raw['POSITIVACAO'], errors='coerce').fillna(0).astype(int)
_hist_raw['CODUSUR']     = pd.to_numeric(_hist_raw['CODUSUR'], errors='coerce')

def monthly_series(nome_oracle):
    df = _hist_raw[_hist_raw['NOME_ORACLE'] == nome_oracle].copy()
    if df.empty:
        return []
    agg = df.groupby('MES', as_index=False).agg(
        fat=('FATURAMENTO', 'sum'), pos=('POSITIVACAO', 'sum')
    ).sort_values('MES')
    return [
        {'mes': row['MES'].strftime('%b/%y'), 'fat': round(float(row['fat']), 2), 'pos': int(row['pos'])}
        for _, row in agg.iterrows()
    ]

# ── Previsão ──────────────────────────────────────────────────────────────────

_hoje        = date.today()
_inicio_mes  = _hoje.replace(day=1)
_fim_mes     = (_hoje.replace(day=28) + pd.offsets.MonthEnd(1)).date()
_du_passados = max(len(pd.bdate_range(_inicio_mes, _hoje)), 1)
_du_total    = len(pd.bdate_range(_inicio_mes, _fim_mes))

def previsao(nome_oracle, fat_realizado, pos_realizado):
    fat_proj = round(fat_realizado / _du_passados * _du_total, 2)
    pos_proj = round(pos_realizado / _du_passados * _du_total, 1)
    mes_atual = pd.Timestamp(_inicio_mes)
    df_hist = _hist_raw[
        (_hist_raw['NOME_ORACLE'] == nome_oracle) & (_hist_raw['MES'] < mes_atual)
    ].groupby('MES').agg(fat=('FATURAMENTO', 'sum'), pos=('POSITIVACAO', 'sum')).sort_values('MES').tail(3)
    fat_media = round(float(df_hist['fat'].mean()), 2) if len(df_hist) > 0 else 0.0
    pos_media = round(float(df_hist['pos'].mean()), 1) if len(df_hist) > 0 else 0.0
    return {
        'fat_proj': fat_proj, 'fat_media_hist': fat_media,
        'pos_proj': pos_proj, 'pos_media_hist': pos_media,
        'du_passados': _du_passados, 'du_total': _du_total,
    }

# ── Não positivados ───────────────────────────────────────────────────────────

def _build_nao_pos(nome_oracle):
    df = _df_nao_pos[_df_nao_pos['NOME_RCA'] == nome_oracle][
        ['CODCLI', 'CLIENTE', 'BAIRROENT', 'DTULTCOMP', 'FANTASIA', 'DESCRICAO']
    ].copy()
    df['FANTASIA']  = df['FANTASIA'].fillna('')
    df['DESCRICAO'] = df['DESCRICAO'].fillna('')
    df['BAIRROENT'] = df['BAIRROENT'].fillna('')
    result = []
    for cliente, grp in df.groupby('CLIENTE', sort=False):
        dt = grp['DTULTCOMP'].dropna().max()
        prods = grp[grp['DTULTCOMP'] == dt][['FANTASIA', 'DESCRICAO']].drop_duplicates().to_dict('records')
        row = grp.iloc[0]
        result.append({
            '_dt':     dt,
            'CODCLI':    str(row['CODCLI']) if pd.notna(row['CODCLI']) else '',
            'CLIENTE':   cliente,
            'BAIRROENT': str(row['BAIRROENT']),
            'DTULTCOMP': dt.strftime('%d/%m/%Y') if pd.notna(dt) else '',
            'produtos':  prods,
        })
    result.sort(key=lambda x: x['_dt'] if pd.notna(x['_dt']) else pd.Timestamp.min, reverse=True)
    for r in result:
        del r['_dt']
    return result

# ── Loop principal: por vendedor × mês ───────────────────────────────────────

_meses_arquivo = sorted(
    metas_com_nome['MES_STR'].dropna().unique().tolist(),
    key=_mes_sort_key, reverse=True,
)

mes_atual_str = _mes_pt(pd.Timestamp(_inicio_mes))

vendedores_dict: dict = {}

for _, m in metas_com_nome.iterrows():
    mes_str      = m.get('MES_STR')
    nome_display = str(m['VENDEDOR'])
    nome_oracle  = m.get('NOME')

    if not mes_str or not nome_display:
        continue

    # Realizado do mês a partir de _vh
    key_vh   = (nome_display, mes_str)
    grupo_vh = _vh_grouped.get_group(key_vh) if key_vh in _vh_grouped.groups else _vh.iloc[0:0]
    real     = _realizado_mes(grupo_vh)

    # Dados fixos por vendedor (calculados uma única vez)
    if nome_display not in vendedores_dict:
        key_atual   = (nome_display, mes_atual_str)
        grupo_atual = _vh_grouped.get_group(key_atual) if key_atual in _vh_grouped.groups else _vh.iloc[0:0]
        real_atual  = _realizado_mes(grupo_atual)

        vendedores_dict[nome_display] = {
            'nome': nome_display,
            'rca':  str(int(m['RCA'])) if pd.notna(m['RCA']) else '',
            'tipovend': str(m.get('TIPOVEND') or '').strip().upper(),
            'por_mes': {},
            'clientes_cadastrados': _cadastros_por_display.get(nome_display, 0),
            'nao_positivados': _build_nao_pos(nome_oracle),
            'historico':       monthly_series(nome_oracle),
            'previsao':        previsao(nome_oracle, real_atual['fat_tt'], real_atual['pos_tt']),
        }

    vendedores_dict[nome_display]['por_mes'][mes_str] = {
        'fat_tt':              {'meta': safe_float(m.get('FATURAMENTO TT')),           'realizado': real['fat_tt']},
        'fat_castas':          {'meta': safe_float(m.get('FAT CASTAS')),                'realizado': real['fat_castas']},
        'fat_domecq_passport': {'meta': safe_float(m.get('FAT. DOMEQ + PASSPORT')),                                                          'realizado': real['fat_domecq_passport']},
        'fat_hob_azeite':      {'meta': safe_float(coalesce(m, 'FATURAMENTO AZEITE + ZE TONA', 'FATURAMENTO HOB + AZEITE')), 'realizado': real['fat_hob_azeite']},
        'fat_pinatti':         {'meta': 0,                                              'realizado': real['fat_pinatti']},
        'fat_moving':          {'meta': 0,                                              'realizado': real['fat_moving']},
        'fat_pernod':          {'meta': safe_float(m.get('FATURAMENTO PERNOD')),         'realizado': real['fat_pernod']},
        'pos_tt':              {'meta': safe_int(m.get('POSITIVAÇÃO TT')),              'realizado': real['pos_tt']},
        'pos_hob_azeite':      {'meta': safe_int(m.get('POSITIVAÇÃO HOB + AZEITE')),    'realizado': real['pos_hob_azeite']},
        'pos_reckit':          {'meta': safe_int(m.get('POSITIVAÇÃO RECKIT')),          'realizado': real['pos_reckit']},
        'pos_crusoe':          {'meta': safe_int(m.get('POSITIVAÇÃO CRUSOÉ')),          'realizado': real['pos_crusoe']},
        'pos_tatuzinho':       {'meta': safe_int(m.get('POSITIVAÇÃO TATUZINHO')),       'realizado': real['pos_tatuzinho']},
        'pos_tial':            {'meta': safe_int(m.get('POSITIVAÇÃO TIAL')),            'realizado': real['pos_tial']},
        'pos_redbull':         {'meta': safe_int(m.get('POSITIVAÇÃO RED BULL')),        'realizado': real['pos_redbull']},
        'pos_pinatti':         {'meta': safe_int(m.get('POSITIVAÇÃO PINATTI')),         'realizado': real['pos_pinatti']},
        'pos_essenza_hob':     {'meta': safe_int(m.get('POSITIVAÇÃO ESSENZA+HOB')),     'realizado': real['pos_essenza_hob']},
        'bonus_pernod':        {'meta': 0, 'realizado': real['bonus_pernod']},
        'pos_pernod':          {'meta': 0, 'realizado': real['pos_pernod']},
        'industrias':          {'meta': 0, 'realizado': real['industrias']},
    }

vendedores_out = list(vendedores_dict.values())

# ── Gera metas_data.js ────────────────────────────────────────────────────────

payload = {
    'atualizado_em':         datetime.now().strftime('%d/%m/%Y %H:%M'),
    'meses':                 _meses_arquivo,
    'vendedores':            vendedores_out,
    'fontes_indisponiveis':  sorted(set(FONTES_INDISPONIVEIS)),
}

js_out = (
    "// Gerado automaticamente pelo notebook analisedados.ipynb\n\n"
    f"const METAS_DATA = {json.dumps(payload, ensure_ascii=False, indent=2)};\n"
)

output_path = str(Path(__file__).parent / "metas_data.js")
_write_js_atomic(output_path, js_out)

print(f"OK metas_data.js gerado — {len(vendedores_out)} vendedores, meses: {_meses_arquivo}")
if FONTES_INDISPONIVEIS:
    print(f"[AVISO] Fontes indisponíveis nesta execução: {sorted(set(FONTES_INDISPONIVEIS))} — resultados podem estar incompletos.")

# ── Gera fontes_status_data.js ──────────────────────────────────────────────
# Arquivo leve (independente do metas_data.js, que tem ~750KB) para que
# qualquer página do site possa mostrar o popup de aviso sem carregar o
# dashboard inteiro. Normaliza nomes tipo "vendas_CASTAS"/"cadastros_CASTAS"
# para só "CASTAS".
_BASES_CONHECIDAS = ["CRC", "thekings", "CASTAS", "GARRIDO", "SPON", "MGON"]
def _normalizar_fonte(nome):
    for _b in _BASES_CONHECIDAS:
        if _b.lower() in nome.lower():
            return _b
    return nome

_fontes_normalizadas = sorted(set(_normalizar_fonte(f) for f in FONTES_INDISPONIVEIS))
_status_payload = {
    'atualizado_em': datetime.now().strftime('%d/%m/%Y %H:%M'),
    'fontes_indisponiveis': _fontes_normalizadas,
}
_write_js_atomic(
    str(Path(__file__).parent / "fontes_status_data.js"),
    f"const FONTES_STATUS_DATA = {json.dumps(_status_payload, ensure_ascii=False, indent=2)};\n"
)
print(f"OK fontes_status_data.js gerado — {_fontes_normalizadas or 'todas as bases OK'}")

# ── Gera vendas_data.js ───────────────────────────────────────────────────────

_meses_vh  = sorted(_vh['MES'].dropna().unique(), reverse=True)
_meses_str = [_mes_pt(m) for m in _meses_vh]

_por_vendedor_hist: dict = {}
for _, row in _vh.iterrows():
    v_nome = row['VENDEDOR']
    mes    = row['MES_STR']
    _por_vendedor_hist.setdefault(v_nome, {}).setdefault(mes, []).append({
        'data':    str(row['DATA']),
        'codcli':  str(row['CODCLI']),
        'cliente': str(row['CLIENTE']),
        'produto': str(row['PRODUTO']),
        'fantasia': str(row['FANTASIA']),
        'qt':      int(row['QT']),
        'valor':   float(row['VALOR']),
        'tipo':    {'SB': 'Bonificado', 'ED': 'Devolução'}.get(str(row.get('CODOPER', 'S')).upper(), 'Venda'),
        'offtrade': row['OFFTRADE'] == 'S',
        'devolvido': bool(row['DEVOLVIDO']),
    })

_rcas_map = {v['nome']: v['rca'] for v in vendedores_out}

vendas_payload = {
    'atualizado_em': datetime.now().strftime('%d/%m/%Y %H:%M'),
    'meses':         _meses_str,
    'por_vendedor':  _por_vendedor_hist,
    'rcas':          _rcas_map,
}

js_vendas = (
    "// Gerado automaticamente pelo notebook analisedados.ipynb\n\n"
    f"const VENDAS_DATA = {json.dumps(vendas_payload, ensure_ascii=False, indent=2)};\n"
)

vendas_path = str(Path(__file__).parent / "vendas_data.js")
_write_js_atomic(vendas_path, js_vendas)

print(f"OK vendas_data.js gerado -> {vendas_path}")

# ── Gera gerentes_data.js ─────────────────────────────────────────────────────

def _query_hierarquia(schema, extra_nomes=None):
    s = schema.upper()
    nome_f = _nome_filter(extra_nomes)
    return f"""
        SELECT
            U.CODUSUR,
            U.NOME           AS NOME_VENDEDOR,
            U.ESTADO         AS ESTADO_VENDEDOR,
            S.CODSUPERVISOR,
            S.NOME           AS NOME_SUPERVISOR,
            G.CODGERENTE,
            G.NOMEGERENTE
        FROM {s}.PCUSUARI U
        LEFT JOIN {s}.PCSUPERV  S ON U.CODSUPERVISOR = S.CODSUPERVISOR
        LEFT JOIN {s}.PCGERENTE G ON S.CODGERENTE     = G.CODGERENTE
        WHERE {nome_f} AND U.BLOQUEIO = 'N'
    """

def _query_hierarquia_basic(schema, extra_nomes=None):
    s = schema.upper()
    nome_f = _nome_filter(extra_nomes).replace('U.NOME', 'NOME')
    return f"""
        SELECT CODUSUR, NOME AS NOME_VENDEDOR,
               NULL AS ESTADO_VENDEDOR,
               NULL AS CODSUPERVISOR,
               NULL AS NOME_SUPERVISOR,
               NULL AS CODGERENTE,
               NULL AS NOMEGERENTE
        FROM {s}.PCUSUARI
        WHERE {nome_f}
    """

_HIER_CONFIGS = [
    ("CRC",     engine,         "hier_CRC",     "CRC",       None),
    ("thekings",engine_theking, "hier_thekings","The Kings", None),
    ("CASTAS",  engine_castas,  "hier_CASTAS",  "Castas",    None),
    ("GARRIDO", engine_garrido, "hier_GARRIDO", "Garrido",   None),
    ("SPON",    engine_spon,    "hier_SPON",    "SPON",      _SPON_EXTRA),
    ("MGON",    engine_mgon,    "hier_MGON",    "MGON",      _SPON_EXTRA),
]

def _carregar_hierarquia_fonte(_s, _e, _n, _en):
    """Tenta a consulta completa (com supervisor/gerente); se a base não tiver
    essas colunas/JOINs, cai pra versão básica (só CODUSUR/NOME). As duas
    tentativas rodam dentro da MESMA thread do pool — o paralelismo é entre
    fontes, não entre as 2 tentativas de uma mesma fonte."""
    try:
        return carregar_dados(_query_hierarquia(_s, extra_nomes=_en), _e, _n)
    except Exception:
        return carregar_dados(_query_hierarquia_basic(_s, extra_nomes=_en), _e, f"{_n}_basic")

_hier_parts = []
with ThreadPoolExecutor(max_workers=len(_HIER_CONFIGS)) as _hier_ex:
    _hier_futuros = {
        _hier_ex.submit(_carregar_hierarquia_fonte, _s, _e, _n, _en): (_s, _e, _n, _emp, _en)
        for _s, _e, _n, _emp, _en in _HIER_CONFIGS
    }
    for _fut in as_completed(_hier_futuros):
        _s, _e, _n, _emp, _en = _hier_futuros[_fut]
        try:
            _df = _fut.result()
            _df['EMPRESA'] = _emp
            _hier_parts.append(_df)
        except Exception as _ex2:
            print(f"[AVISO] {_n} falhou ({str(_ex2)[:80]}) — ignorado")
            FONTES_INDISPONIVEIS.append(_n)

if _hier_parts:
    _hier = pd.concat(_hier_parts, ignore_index=True)
    _hier['CODUSUR'] = pd.to_numeric(_hier['CODUSUR'], errors='coerce')
    _hier = _hier.dropna(subset=['CODUSUR'])
    if 'ESTADO_VENDEDOR' not in _hier.columns:
        _hier['ESTADO_VENDEDOR'] = ''
    _hier['ESTADO_VENDEDOR'] = _hier['ESTADO_VENDEDOR'].fillna('').astype(str).str.strip()
    # Um RCA por empresa (sem ambiguidade de nome)
    _hier = _hier.drop_duplicates(subset=['CODUSUR', 'EMPRESA'])

    # Itera pelos vendedores OFF TRADE e busca as vendas de cada um
    _emp_dict: dict = {}

    for _, hier_row in _hier.iterrows():
        rca_num = hier_row['CODUSUR']
        if pd.isna(rca_num):
            continue
        rca_int         = int(rca_num)
        nome_oracle_hier = str(hier_row.get('NOME_VENDEDOR', '') or '')
        nome_display     = _oracle_to_display.get(nome_oracle_hier) or nome_oracle_hier or None
        if not nome_display:
            continue

        empresa = str(hier_row.get('EMPRESA', 'Desconhecido') or 'Desconhecido')
        ger     = str(hier_row.get('NOMEGERENTE',    '') or '') or 'Sem Gerente'
        sup     = str(hier_row.get('NOME_SUPERVISOR','') or '') or 'Sem Supervisor'
        estado  = str(hier_row.get('ESTADO_VENDEDOR','') or '').strip()

        # Vendas deste RCA nesta empresa
        mask        = (_vh['RCA'] == rca_num) & (_vh['EMPRESA'] == empresa)
        vend_sales  = _vh[mask]
        por_mes_vend = {}
        for mes_str, grp in vend_sales.groupby('MES_STR'):
            por_mes_vend[str(mes_str)] = {
                'fat': round(float(grp['VALOR'].sum()), 2),
                'qt':  int(grp['QT'].sum()),
            }

        # Nao pular vendedor sem historico de vendas (por_mes_vend vazio) —
        # ele ainda tem estado/gerente/supervisor validos no Oracle e precisa
        # aparecer na arvore (com zeros), senao some dos filtros por
        # gerente/supervisor mesmo tendo meta ativa (caso IVANILDO MAIA, RCA
        # 460, supervisor Danielle Moura — zero vendas, mas RJ ativo).
        estado_top = estado or 'Sem Estado'

        e  = _emp_dict.setdefault(estado_top, {'nome': estado_top, 'por_mes': {}, 'gerentes': {}})
        g  = e['gerentes'].setdefault(ger, {'nome': ger, 'por_mes': {}, 'supervisores': {}})
        s_ = g['supervisores'].setdefault(sup, {'nome': sup, 'por_mes': {}, 'vendedores': {}})
        if nome_display in s_['vendedores']:
            for mes, val in por_mes_vend.items():
                ex = s_['vendedores'][nome_display]['por_mes']
                if mes in ex:
                    ex[mes]['fat'] = round(ex[mes]['fat'] + val['fat'], 2)
                    ex[mes]['qt'] += val['qt']
                else:
                    ex[mes] = val
        else:
            s_['vendedores'][nome_display] = {
                'nome':    nome_display,
                'rca':     str(rca_int),
                'estado':  estado,
                'por_mes': por_mes_vend,
            }

        for mes_str, val in por_mes_vend.items():
            for d in (e['por_mes'], g['por_mes'], s_['por_mes']):
                m = d.setdefault(mes_str, {'fat': 0.0, 'qt': 0})
                m['fat'] = round(m['fat'] + val['fat'], 2)
                m['qt'] += val['qt']

    estados_out = []
    for est_data in sorted(_emp_dict.values(), key=lambda x: x['nome']):
        gerentes_list = []
        for ger_data in sorted(est_data['gerentes'].values(), key=lambda x: x['nome']):
            sups_out = []
            for sup_data in sorted(ger_data['supervisores'].values(), key=lambda x: x['nome']):
                vends_out = sorted(sup_data['vendedores'].values(), key=lambda x: x['nome'])
                estados_sup = sorted(set(v['estado'] for v in vends_out if v.get('estado')))
                sups_out.append({'nome': sup_data['nome'], 'estados': estados_sup, 'por_mes': sup_data['por_mes'], 'vendedores': list(vends_out)})
            estados_ger = sorted(set(e for s in sups_out for e in s.get('estados', [])))
            gerentes_list.append({'nome': ger_data['nome'], 'estados': estados_ger, 'por_mes': ger_data['por_mes'], 'supervisores': sups_out})
        estados_out.append({'nome': est_data['nome'], 'por_mes': est_data['por_mes'], 'gerentes': gerentes_list})

    gerentes_payload = {
        'atualizado_em': datetime.now().strftime('%d/%m/%Y %H:%M'),
        'meses':         _meses_str,
        'estados':       estados_out,
    }
    js_ger = (
        "// Gerado automaticamente\n\n"
        f"const GERENTES_DATA = {json.dumps(gerentes_payload, ensure_ascii=False, indent=2)};\n"
    )
    ger_path = str(Path(__file__).parent / "gerentes_data.js")
    _write_js_atomic(ger_path, js_ger)
    print(f"OK gerentes_data.js gerado — {len(estados_out)} estados")
else:
    print("[AVISO] hierarquia não disponível — gerentes_data.js não gerado")

# ── Push para GitHub Pages ────────────────────────────────────────────────────

import subprocess
repo_dir = str(Path(__file__).parent)
try:
    subprocess.run(["git", "-C", repo_dir, "add", "metas_data.js", "vendas_data.js", "gerentes_data.js", "fontes_status_data.js"], check=True)
    subprocess.run(["git", "-C", repo_dir, "commit", "-m",
                    f"Atualiza metas_data.js + vendas_data.js - {date.today().strftime('%d/%m/%Y')}"])
    subprocess.run(["git", "-C", repo_dir, "push", "origin", "master"], check=True)
    print("OK GitHub Pages atualizado.")
except subprocess.CalledProcessError:
    print("[AVISO] git push falhou — ignorado, pipeline continua.")
