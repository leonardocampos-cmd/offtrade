# EXPORTAÇÃO PARA metas.html
import json
import pandas as pd
from datetime import date, datetime
from pathlib import Path
from meta import engine, engine_theking, arquivo, tabela_vendas, tabela_vendas_anterior
import nao_positivados as _np_mod

_df_nao_pos = _np_mod.nao_positivados_full

# Busca o nome Oracle de cada vendedor pelo RCA (CODUSUR) nos dois bancos
map_rca = pd.concat([
    pd.read_sql("SELECT CODUSUR AS RCA, NOME FROM CRC.PCUSUARI WHERE NOME LIKE '%OFF TRADE%'", con=engine),
    pd.read_sql("SELECT CODUSUR AS RCA, NOME FROM thekings.PCUSUARI WHERE NOME LIKE '%OFF TRADE%'", con=engine_theking),
], ignore_index=True)
map_rca.columns = map_rca.columns.str.upper()
map_rca = map_rca.drop_duplicates(subset=['RCA'])
map_rca['RCA'] = pd.to_numeric(map_rca['RCA'], errors='coerce')
arquivo['RCA']  = pd.to_numeric(arquivo['RCA'],  errors='coerce')

# Junta arquivo de metas com o nome Oracle via RCA
metas_com_nome = arquivo.merge(map_rca, on='RCA', how='left')

por_vendedor = tabela_vendas.groupby('VENDEDOR')
por_vendedor_anterior = tabela_vendas_anterior.groupby('VENDEDOR') if not tabela_vendas_anterior.empty else {}

def real_fat(grupo, filtro=None):
    df = grupo[filtro(grupo)] if filtro else grupo
    return float(df['FATURAMENTO'].sum().round(2))

def real_pos(grupo, filtro=None):
    df = grupo[filtro(grupo)] if filtro else grupo
    return int(df['CODCLI'].nunique())

def safe_int(v):
    try: return int(v) if pd.notna(v) else 0
    except: return 0

def safe_float(v):
    try: return float(v) if pd.notna(v) else 0.0
    except: return 0.0

def _query_historico(schema):
    s = schema.upper()
    return f"""
        SELECT
            TRUNC(M.DTMOV, 'MM')  AS MES,
            M.CODUSUR              AS CODUSUR,
            SUM(M.PUNIT * M.QT)   AS FATURAMENTO,
            COUNT(DISTINCT M.CODCLI) AS POSITIVACAO
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
_hist_raw.columns = _hist_raw.columns.str.upper()
_hist_raw['MES']         = pd.to_datetime(_hist_raw['MES'], errors='coerce')
_hist_raw['FATURAMENTO'] = pd.to_numeric(_hist_raw['FATURAMENTO'], errors='coerce').fillna(0)
_hist_raw['POSITIVACAO'] = pd.to_numeric(_hist_raw['POSITIVACAO'], errors='coerce').fillna(0).astype(int)
_hist_raw['CODUSUR']     = pd.to_numeric(_hist_raw['CODUSUR'], errors='coerce')

# Junta CODUSUR → NOME para poder indexar igual ao resto
_hist_raw = _hist_raw.merge(
    map_rca.rename(columns={'NOME': 'NOME_ORACLE'}),
    left_on='CODUSUR', right_on='RCA', how='left'
)

def monthly_series(nome_oracle):
    df = _hist_raw[_hist_raw['NOME_ORACLE'] == nome_oracle].copy()
    if df.empty:
        return []
    agg = df.groupby('MES', as_index=False).agg(
        fat=('FATURAMENTO', 'sum'),
        pos=('POSITIVACAO', 'sum'),
    ).sort_values('MES')
    return [
        {
            'mes': row['MES'].strftime('%b/%y'),
            'fat': round(float(row['fat']), 2),
            'pos': int(row['pos']),
        }
        for _, row in agg.iterrows()
    ]

# Dias úteis do mês atual (seg–sex, sem feriados)
_hoje        = date.today()
_inicio_mes  = _hoje.replace(day=1)
_fim_mes     = (_hoje.replace(day=28) + pd.offsets.MonthEnd(1)).date()
_du_passados = max(len(pd.bdate_range(_inicio_mes, _hoje)), 1)
_du_total    = len(pd.bdate_range(_inicio_mes, _fim_mes))

def previsao(nome_oracle, fat_realizado, pos_realizado):
    # Abordagem 1 – ritmo atual extrapolado para o mês
    fat_proj = round(fat_realizado / _du_passados * _du_total, 2)
    pos_proj = round(pos_realizado / _du_passados * _du_total, 1)

    # Abordagem 2 – média dos últimos 3 meses fechados
    mes_atual = pd.Timestamp(_inicio_mes)
    df_hist = _hist_raw[
        (_hist_raw['NOME_ORACLE'] == nome_oracle) &
        (_hist_raw['MES'] < mes_atual)
    ].groupby('MES').agg(fat=('FATURAMENTO', 'sum'), pos=('POSITIVACAO', 'sum')).sort_values('MES').tail(3)

    fat_media = round(float(df_hist['fat'].mean()), 2) if len(df_hist) > 0 else 0.0
    pos_media = round(float(df_hist['pos'].mean()), 1) if len(df_hist) > 0 else 0.0

    return {
        'fat_proj':       fat_proj,
        'fat_media_hist': fat_media,
        'pos_proj':       pos_proj,
        'pos_media_hist': pos_media,
        'du_passados':    _du_passados,
        'du_total':       _du_total,
    }

def _build_vendas(grupo):
    if grupo.empty:
        return []
    cols = [c for c in ['DTMOV', 'CLIENTE', 'CODCLI', 'DESCRICAO', 'QT', 'FATURAMENTO'] if c in grupo.columns]
    df = grupo[cols].copy()
    df['QT']          = pd.to_numeric(df['QT'],          errors='coerce').fillna(0)
    df['FATURAMENTO'] = pd.to_numeric(df['FATURAMENTO'], errors='coerce').fillna(0)
    df = df.sort_values('DTMOV', ascending=False)
    result = []
    for _, row in df.iterrows():
        result.append({
            'data':     str(row['DTMOV']) if pd.notna(row.get('DTMOV')) else '',
            'cliente':  str(row['CLIENTE'])  if 'CLIENTE'  in row and pd.notna(row['CLIENTE'])  else str(row.get('CODCLI', '')),
            'produto':  str(row['DESCRICAO']) if pd.notna(row.get('DESCRICAO')) else '',
            'qt':       int(row['QT']),
            'valor':    round(float(row['FATURAMENTO']), 2),
        })
    return result

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
        # Produtos apenas da data mais recente
        prods = grp[grp['DTULTCOMP'] == dt][['FANTASIA', 'DESCRICAO']].drop_duplicates().to_dict('records')
        row = grp.iloc[0]
        result.append({
            '_dt': dt,
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

vendedores_out = []
for _, m in metas_com_nome.iterrows():
    nome_oracle = m.get('NOME')
    grupo = por_vendedor.get_group(nome_oracle) if nome_oracle in por_vendedor.groups else tabela_vendas.iloc[0:0]
    grupo_anterior = por_vendedor_anterior.get_group(nome_oracle) if hasattr(por_vendedor_anterior, 'groups') and nome_oracle in por_vendedor_anterior.groups else tabela_vendas_anterior.iloc[0:0]

    vendedores_out.append({
        "nome": str(m['VENDEDOR']),
        "rca":  str(int(m['RCA'])) if pd.notna(m['RCA']) else '',
        "fat_tt":               {"meta": safe_float(m.get('FATURAMENTO TT')),           "realizado": real_fat(grupo)},
        "fat_castas":           {"meta": safe_float(m.get('FAT CASTAS')),                "realizado": real_fat(grupo, lambda d: d['FANTASIA'].str.contains('castas', case=False, na=False))},
        "fat_domecq_passport":  {"meta": safe_float(m.get('FAT. DOMEQ + PASSPORT')),    "realizado": real_fat(grupo, lambda d: d['DESCRICAO'].str.contains('DOMECQ|PASSPORT', case=False, na=False))},
        "fat_hob_azeite":       {"meta": safe_float(m.get('FATURAMENTO HOB + AZEITE')), "realizado": real_fat(grupo, lambda d: d['DESCRICAO'].str.contains('AZEITE', case=False, na=False) | d['FANTASIA'].str.contains('HOB', case=False, na=False))},
        "pos_tt":               {"meta": safe_int(m.get('POSITIVAÇÃO TT')),              "realizado": real_pos(grupo)},
        "pos_hob_azeite":       {"meta": safe_int(m.get('POSITIVAÇÃO HOB + AZEITE')),    "realizado": real_pos(grupo, lambda d: d['DESCRICAO'].str.contains('AZEITE', case=False, na=False) | d['FANTASIA'].str.contains('HOB', case=False, na=False))},
        "pos_reckit":           {"meta": safe_int(m.get('POSITIVAÇÃO RECKIT')),          "realizado": real_pos(grupo, lambda d: d['FANTASIA'].str.contains('RECKIT', case=False, na=False))},
        "pos_crusoe":           {"meta": safe_int(m.get('POSITIVAÇÃO CRUSOÉ')),          "realizado": real_pos(grupo, lambda d: d['FANTASIA'].str.contains('ROBINSON CRUSOE', case=False, na=False))},
        "pos_tatuzinho":        {"meta": safe_int(m.get('POSITIVAÇÃO TATUZINHO')),       "realizado": real_pos(grupo, lambda d: d['FANTASIA'].str.contains('TATUZINHO', case=False, na=False))},
        "pos_redbull":          {"meta": safe_int(m.get('POSITIVAÇÃO RED BULL')),        "realizado": real_pos(grupo, lambda d: d['FANTASIA'].str.contains('RED BULL', case=False, na=False))},
        "pos_pinatti":          {"meta": safe_int(m.get('POSITIVAÇÃO PINATTI')),         "realizado": real_pos(grupo, lambda d: d['FANTASIA'].str.contains('PINATI', case=False, na=False))},
        "nao_positivados": _build_nao_pos(nome_oracle),
        "historico":       monthly_series(nome_oracle),
        "previsao":        previsao(nome_oracle, real_fat(grupo), real_pos(grupo)),
        "vendas":          _build_vendas(grupo_anterior),
    })

payload = {
    "mes": date.today().strftime('%B %Y').capitalize(),
    "atualizado_em": datetime.now().strftime('%d/%m/%Y %H:%M'),
    "vendedores": vendedores_out
}

js_out = (
    "// Gerado automaticamente pelo notebook analisedados.ipynb\n\n"
    f"const METAS_DATA = {json.dumps(payload, ensure_ascii=False, indent=2)};\n"
)

output_path = str(Path(__file__).parent / "metas_data.js")
with open(output_path, 'w', encoding='utf-8') as f:
    f.write(js_out)

print(f"OK metas_data.js gerado com {len(vendedores_out)} vendedores -> {output_path}")

# ── Geração do vendas_data.js ─────────────────────────────────────────────────

_MESES_PT = {
    'Jan': 'Jan', 'Feb': 'Fev', 'Mar': 'Mar', 'Apr': 'Abr',
    'May': 'Mai', 'Jun': 'Jun', 'Jul': 'Jul', 'Aug': 'Ago',
    'Sep': 'Set', 'Oct': 'Out', 'Nov': 'Nov', 'Dec': 'Dez',
}

def _mes_pt(dt):
    return f"{_MESES_PT.get(dt.strftime('%b'), dt.strftime('%b'))}/{dt.strftime('%y')}"

def _query_vendas_historico(schema):
    s = schema.upper()
    return f"""
        SELECT
            TRUNC(M.DTMOV, 'MM')            AS MES,
            TO_CHAR(M.DTMOV, 'DD/MM/YYYY') AS DATA,
            M.CODCLI,
            C.CLIENTE,
            M.DESCRICAO                     AS PRODUTO,
            M.QT,
            (M.PUNIT * M.QT)               AS VALOR,
            U.NOME                          AS NOME_ORACLE
        FROM {s}.PCMOV M
        JOIN {s}.PCUSUARI U ON M.CODUSUR = U.CODUSUR
        LEFT JOIN {s}.PCCLIENT C ON M.CODCLI = C.CODCLI
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
_vh.columns = _vh.columns.str.upper()
_vh['MES']   = pd.to_datetime(_vh['MES'],   errors='coerce')
_vh['QT']    = pd.to_numeric(_vh['QT'],     errors='coerce').fillna(0).astype(int)
_vh['VALOR'] = pd.to_numeric(_vh['VALOR'],  errors='coerce').fillna(0).round(2)
_vh['CLIENTE'] = _vh['CLIENTE'].fillna(_vh['CODCLI'])

# Oracle name → display name (mesmo mapeamento do loop de metas)
_oracle_to_display = {
    str(r['NOME']): str(r['VENDEDOR'])
    for _, r in metas_com_nome.iterrows()
    if pd.notna(r.get('NOME'))
}
_vh['VENDEDOR'] = _vh['NOME_ORACLE'].map(_oracle_to_display)
_vh = _vh[_vh['VENDEDOR'].notna()].copy()

# Meses disponíveis (ordem decrescente)
_meses_dt  = sorted(_vh['MES'].dropna().unique(), reverse=True)
_meses_str = [_mes_pt(m) for m in _meses_dt]
_vh['MES_STR'] = _vh['MES'].apply(_mes_pt)

# Monta dicionário {vendedor: {mes: [linhas]}}
_por_vendedor_hist: dict = {}
for _, row in _vh.iterrows():
    v_nome = row['VENDEDOR']
    mes    = row['MES_STR']
    _por_vendedor_hist.setdefault(v_nome, {}).setdefault(mes, []).append({
        'data':    str(row['DATA']),
        'cliente': str(row['CLIENTE']),
        'produto': str(row['PRODUTO']) if pd.notna(row['PRODUTO']) else '',
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

# Push automático para o GitHub Pages
import subprocess
repo_dir = str(Path(__file__).parent)
subprocess.run(["git", "-C", repo_dir, "add", "metas_data.js", "vendas_data.js"], check=True)
subprocess.run(["git", "-C", repo_dir, "commit", "-m", f"Atualiza metas_data.js + vendas_data.js - {date.today().strftime('%d/%m/%Y')}"])
subprocess.run(["git", "-C", repo_dir, "push", "origin", "master"], check=True)
print("OK GitHub Pages atualizado.")
