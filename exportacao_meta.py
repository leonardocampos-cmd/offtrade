# EXPORTAÇÃO PARA metas.html
import json
import pandas as pd
from datetime import date
from pathlib import Path
from meta import engine, engine_theking, arquivo, tabela_vendas
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

def _build_nao_pos(nome_oracle):
    df = _df_nao_pos[_df_nao_pos['NOME_RCA'] == nome_oracle][
        ['CODCLI', 'CLIENTE', 'DTULTCOMP', 'FANTASIA', 'DESCRICAO']
    ].copy()
    df['FANTASIA']  = df['FANTASIA'].fillna('')
    df['DESCRICAO'] = df['DESCRICAO'].fillna('')
    result = []
    for (codcli, cliente), grp in df.groupby(['CODCLI', 'CLIENTE'], sort=False):
        dt = grp['DTULTCOMP'].dropna().max()
        result.append({
            '_dt': dt,
            'CLIENTE':   cliente,
            'DTULTCOMP': dt.strftime('%d/%m/%Y') if pd.notna(dt) else '',
            'produtos':  grp[['FANTASIA', 'DESCRICAO']].drop_duplicates().to_dict('records'),
        })
    result.sort(key=lambda x: x['_dt'] if pd.notna(x['_dt']) else pd.Timestamp.min, reverse=True)
    for r in result:
        del r['_dt']
    return result

vendedores_out = []
for _, m in metas_com_nome.iterrows():
    nome_oracle = m.get('NOME')
    grupo = por_vendedor.get_group(nome_oracle) if nome_oracle in por_vendedor.groups else tabela_vendas.iloc[0:0]

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
    })

payload = {
    "mes": date.today().strftime('%B %Y').capitalize(),
    "atualizado_em": date.today().strftime('%d/%m/%Y'),
    "vendedores": vendedores_out
}

js_out = (
    "// Gerado automaticamente pelo notebook analisedados.ipynb\n\n"
    f"const METAS_DATA = {json.dumps(payload, ensure_ascii=False, indent=2)};\n"
)

output_path = str(Path(__file__).parent / "metas_data.js")
with open(output_path, 'w', encoding='utf-8') as f:
    f.write(js_out)

print(f"✅ metas_data.js gerado com {len(vendedores_out)} vendedores → {output_path}")

# Push automático para o GitHub Pages
import subprocess
repo_dir = str(Path(__file__).parent)
subprocess.run(["git", "-C", repo_dir, "add", "metas_data.js"], check=True)
subprocess.run(["git", "-C", repo_dir, "commit", "-m", f"Atualiza metas_data.js - {date.today().strftime('%d/%m/%Y')}"])
subprocess.run(["git", "-C", repo_dir, "push", "origin", "master"], check=True)
print("✅ GitHub Pages atualizado.")
