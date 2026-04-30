# EXPORTAÇÃO PARA metas.html
import json
import pandas as pd
from datetime import date, datetime
from pathlib import Path
from meta import engine, engine_theking, arquivo
import nao_positivados as _np_mod

_df_nao_pos = _np_mod.nao_positivados_full

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

map_rca = pd.concat([
    pd.read_sql("SELECT CODUSUR AS RCA, NOME FROM CRC.PCUSUARI WHERE NOME LIKE '%OFF TRADE%'", con=engine),
    pd.read_sql("SELECT CODUSUR AS RCA, NOME FROM thekings.PCUSUARI WHERE NOME LIKE '%OFF TRADE%'", con=engine_theking),
], ignore_index=True)
map_rca.columns = map_rca.columns.str.upper()
map_rca = map_rca.drop_duplicates(subset=['RCA'])
map_rca['RCA'] = pd.to_numeric(map_rca['RCA'], errors='coerce')
arquivo['RCA'] = pd.to_numeric(arquivo['RCA'],  errors='coerce')

# Normaliza coluna MÊS do arquivo → "Mar/26"
mes_col = 'MÊS' if 'MÊS' in arquivo.columns else 'MES'
arquivo['MES_STR'] = pd.to_datetime(arquivo[mes_col], errors='coerce').apply(
    lambda d: _mes_pt(d) if pd.notna(d) else None
)

metas_com_nome = arquivo.merge(map_rca, on='RCA', how='left')

# ── Vendas históricas (6 meses) com FANTASIA ─────────────────────────────────

def _query_vendas_historico(schema):
    s = schema.upper()
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
            U.NOME                          AS NOME_ORACLE
        FROM {s}.PCMOV M
        JOIN {s}.PCUSUARI U ON M.CODUSUR = U.CODUSUR
        LEFT JOIN {s}.PCCLIENT C ON M.CODCLI = C.CODCLI
        JOIN {s}.PCPRODUT P ON M.CODPROD = P.CODPROD
        JOIN {s}.PCFORNEC F ON P.CODFORNEC = F.CODFORNEC
        WHERE M.DTMOV >= ADD_MONTHS(TRUNC(SYSDATE, 'MM'), -5)
          AND M.CODOPER = 'S'
          AND M.CODFILIAL IN (1, 2, 4)
          AND M.NUMNOTADEV IS NULL
          AND M.DTCANCEL IS NULL
          AND U.NOME LIKE '%OFF TRADE%'
    """

_vh = pd.concat([
    pd.read_sql(_query_vendas_historico("CRC"),      con=engine,         dtype=str),
    pd.read_sql(_query_vendas_historico("thekings"), con=engine_theking, dtype=str),
], ignore_index=True)
_vh.columns   = _vh.columns.str.upper()
_vh['MES']    = pd.to_datetime(_vh['MES'],   errors='coerce')
_vh['QT']     = pd.to_numeric(_vh['QT'],     errors='coerce').fillna(0).astype(int)
_vh['VALOR']  = pd.to_numeric(_vh['VALOR'],  errors='coerce').fillna(0).round(2)
_vh['CLIENTE']  = _vh['CLIENTE'].fillna(_vh['CODCLI'])
_vh['FANTASIA'] = _vh['FANTASIA'].fillna('')
_vh['PRODUTO']  = _vh['PRODUTO'].fillna('')

# Oracle name → display name
_oracle_to_display = {
    str(r['NOME']): str(r['VENDEDOR'])
    for _, r in metas_com_nome.drop_duplicates(subset=['NOME']).iterrows()
    if pd.notna(r.get('NOME'))
}
_vh['VENDEDOR'] = _vh['NOME_ORACLE'].map(_oracle_to_display)
_vh = _vh[_vh['VENDEDOR'].notna()].copy()
_vh['MES_STR'] = _vh['MES'].apply(_mes_pt)

_vh_grouped = _vh.groupby(['VENDEDOR', 'MES_STR'])

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
                 pos_tt=0, pos_hob_azeite=0, pos_reckit=0, pos_crusoe=0,
                 pos_tatuzinho=0, pos_redbull=0, pos_pinatti=0,
                 bonus_pernod=0.0, pos_pernod=0)
    if df.empty:
        return _zero

    def fat(mask=None):
        sub = df[mask] if mask is not None else df
        return float(sub['VALOR'].sum().round(2))

    def pos(mask=None):
        sub = df[mask] if mask is not None else df
        return int(sub['CODCLI'].nunique())

    # Campanha PERNOD: pares únicos (cliente, produto) com FANTASIA=PERNOD; JAMERSON=10, demais=5
    mask_pernod   = df['FANTASIA'].str.contains('PERNOD', case=False, na=False)
    pernod_pairs  = df[mask_pernod].drop_duplicates(subset=['CODCLI', 'PRODUTO'])
    mask_jamerson = pernod_pairs['PRODUTO'].str.contains('JAMERSON', case=False, na=False)
    n_jamerson    = int(mask_jamerson.sum())
    n_outros      = int((~mask_jamerson).sum())
    bonus_pernod  = n_jamerson * 10 + n_outros * 5
    pos_pernod    = n_jamerson + n_outros

    return {
        'fat_tt':              fat(),
        'fat_castas':          fat(df['FANTASIA'].str.contains('castas', case=False, na=False)),
        'fat_domecq_passport': fat(df['PRODUTO'].str.contains('DOMECQ|PASSPORT', case=False, na=False)),
        'fat_hob_azeite':      fat(df['PRODUTO'].str.contains('AZEITE', case=False, na=False) | df['FANTASIA'].str.contains('HOB', case=False, na=False)),
        'fat_pinatti':         fat(df['FANTASIA'].str.contains('PINATI', case=False, na=False)),
        'fat_moving':          fat(df['PRODUTO'].str.contains('MOVING', case=False, na=False)),
        'pos_tt':              pos(),
        'pos_hob_azeite':      pos(df['PRODUTO'].str.contains('AZEITE', case=False, na=False) | df['FANTASIA'].str.contains('HOB', case=False, na=False)),
        'pos_reckit':          pos(df['FANTASIA'].str.contains('RECKIT', case=False, na=False)),
        'pos_crusoe':          pos(df['FANTASIA'].str.contains('ROBINSON CRUSOE', case=False, na=False)),
        'pos_tatuzinho':       pos(df['FANTASIA'].str.contains('TATUZINHO', case=False, na=False)),
        'pos_redbull':         pos(df['FANTASIA'].str.contains('RED BULL', case=False, na=False)),
        'pos_pinatti':         pos(df['FANTASIA'].str.contains('PINATI', case=False, na=False)),
        'bonus_pernod':        bonus_pernod,
        'pos_pernod':          pos_pernod,
    }

# ── Histórico mensal agregado (para gráficos) ─────────────────────────────────

def _query_historico(schema):
    s = schema.upper()
    return f"""
        SELECT
            TRUNC(M.DTMOV, 'MM')         AS MES,
            M.CODUSUR                     AS CODUSUR,
            SUM(M.PUNIT * M.QT)          AS FATURAMENTO,
            COUNT(DISTINCT M.CODCLI)     AS POSITIVACAO
        FROM {s}.PCMOV M
        JOIN {s}.PCUSUARI U ON M.CODUSUR = U.CODUSUR
        WHERE M.DTMOV >= ADD_MONTHS(TRUNC(SYSDATE, 'MM'), -11)
          AND M.CODOPER = 'S'
          AND M.CODFILIAL IN (1, 2, 4)
          AND M.NUMNOTADEV IS NULL
          AND M.DTCANCEL IS NULL
          AND U.NOME LIKE '%OFF TRADE%'
        GROUP BY TRUNC(M.DTMOV, 'MM'), M.CODUSUR
    """

_hist_raw = pd.concat([
    pd.read_sql(_query_historico("CRC"),      con=engine,         dtype=str),
    pd.read_sql(_query_historico("thekings"), con=engine_theking, dtype=str),
], ignore_index=True)
_hist_raw.columns    = _hist_raw.columns.str.upper()
_hist_raw['MES']         = pd.to_datetime(_hist_raw['MES'], errors='coerce')
_hist_raw['FATURAMENTO'] = pd.to_numeric(_hist_raw['FATURAMENTO'], errors='coerce').fillna(0)
_hist_raw['POSITIVACAO'] = pd.to_numeric(_hist_raw['POSITIVACAO'], errors='coerce').fillna(0).astype(int)
_hist_raw['CODUSUR']     = pd.to_numeric(_hist_raw['CODUSUR'], errors='coerce')
_hist_raw = _hist_raw.merge(map_rca.rename(columns={'NOME': 'NOME_ORACLE'}), left_on='CODUSUR', right_on='RCA', how='left')

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
            'por_mes': {},
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
        'pos_tt':              {'meta': safe_int(m.get('POSITIVAÇÃO TT')),              'realizado': real['pos_tt']},
        'pos_hob_azeite':      {'meta': safe_int(m.get('POSITIVAÇÃO HOB + AZEITE')),    'realizado': real['pos_hob_azeite']},
        'pos_reckit':          {'meta': safe_int(m.get('POSITIVAÇÃO RECKIT')),          'realizado': real['pos_reckit']},
        'pos_crusoe':          {'meta': safe_int(m.get('POSITIVAÇÃO CRUSOÉ')),          'realizado': real['pos_crusoe']},
        'pos_tatuzinho':       {'meta': safe_int(m.get('POSITIVAÇÃO TATUZINHO')),       'realizado': real['pos_tatuzinho']},
        'pos_redbull':         {'meta': safe_int(m.get('POSITIVAÇÃO RED BULL')),        'realizado': real['pos_redbull']},
        'pos_pinatti':         {'meta': safe_int(m.get('POSITIVAÇÃO PINATTI')),         'realizado': real['pos_pinatti']},
        'bonus_pernod':        {'meta': 0, 'realizado': real['bonus_pernod']},
        'pos_pernod':          {'meta': 0, 'realizado': real['pos_pernod']},
    }

vendedores_out = list(vendedores_dict.values())

# ── Gera metas_data.js ────────────────────────────────────────────────────────

payload = {
    'atualizado_em': datetime.now().strftime('%d/%m/%Y %H:%M'),
    'meses':         _meses_arquivo,
    'vendedores':    vendedores_out,
}

js_out = (
    "// Gerado automaticamente pelo notebook analisedados.ipynb\n\n"
    f"const METAS_DATA = {json.dumps(payload, ensure_ascii=False, indent=2)};\n"
)

output_path = str(Path(__file__).parent / "metas_data.js")
with open(output_path, 'w', encoding='utf-8') as f:
    f.write(js_out)

print(f"OK metas_data.js gerado — {len(vendedores_out)} vendedores, meses: {_meses_arquivo}")

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
    })

vendas_payload = {
    'atualizado_em': datetime.now().strftime('%d/%m/%Y %H:%M'),
    'meses':         _meses_str,
    'por_vendedor':  _por_vendedor_hist,
}

js_vendas = (
    "// Gerado automaticamente pelo notebook analisedados.ipynb\n\n"
    f"const VENDAS_DATA = {json.dumps(vendas_payload, ensure_ascii=False, indent=2)};\n"
)

vendas_path = str(Path(__file__).parent / "vendas_data.js")
with open(vendas_path, 'w', encoding='utf-8') as f:
    f.write(js_vendas)

print(f"OK vendas_data.js gerado -> {vendas_path}")

# ── Push para GitHub Pages ────────────────────────────────────────────────────

import subprocess
repo_dir = str(Path(__file__).parent)
subprocess.run(["git", "-C", repo_dir, "add", "metas_data.js", "vendas_data.js"], check=True)
subprocess.run(["git", "-C", repo_dir, "commit", "-m",
                f"Atualiza metas_data.js + vendas_data.js - {date.today().strftime('%d/%m/%Y')}"])
subprocess.run(["git", "-C", repo_dir, "push", "origin", "master"], check=True)
print("OK GitHub Pages atualizado.")
