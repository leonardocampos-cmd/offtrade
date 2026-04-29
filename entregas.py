import pandas as pd
import oracledb
from sqlalchemy import create_engine
import re
import json
from datetime import datetime
from pathlib import Path

oracledb.init_oracle_client(lib_dir=r"C:\instantclient")
engine = create_engine('oracle+oracledb://vpn:vpn2320vpn@crc_oci')

# ── Pedidos do mês ─────────────────────────────────────────────────────────────

tabela_pedidos = pd.read_sql("""
    SELECT NUMPED, NUMNOTA, NOME, DATA, CODUSUR, CLIENTE, STATUS,
           DESCRICAO, PVENDA, QT, TOTAL, OBSENTREGA1
    FROM crc.PBI_PCPEDI
    WHERE CODFILIAL IN (2,4)
      AND NOME LIKE '%OFF TRADE%'
      AND DATA >= TRUNC(SYSDATE, 'MM')
      AND DATA < LAST_DAY(SYSDATE) + 1
    ORDER BY DATA DESC
""", con=engine, dtype=str)

tabela_pedidos.columns = tabela_pedidos.columns.str.upper()
tabela_pedidos['TOTAL']       = pd.to_numeric(tabela_pedidos['TOTAL'],   errors='coerce').fillna(0)
tabela_pedidos['QT']          = pd.to_numeric(tabela_pedidos['QT'],      errors='coerce').fillna(0).astype(int)
tabela_pedidos['NUMNOTA_NUM'] = pd.to_numeric(tabela_pedidos['NUMNOTA'], errors='coerce')
tabela_pedidos['DATA']        = pd.to_datetime(tabela_pedidos['DATA'],   errors='coerce').dt.strftime('%d/%m/%Y')

# ── Excel de logística ─────────────────────────────────────────────────────────

caminho_excel = (
    r"G:\Drives compartilhados\01-Logística\LOGÍSTICA RJ\APOIO LOGÍSTICO"
    r"\CONTROLE DE SAÍDAS LOGÍSTICA\Controle de Saídas 2.0 - -\2026\04 ABRIL"
    r"\04 COPIA FINANCEIRO - Controle de Notas Abril 2026.xlsx"
)

dict_abas   = pd.read_excel(caminho_excel, sheet_name=None)
padrao_data = re.compile(r'^\d{2}\.\d{2}$')
lista_dfs   = [df for aba, df in dict_abas.items() if padrao_data.match(aba)]

if lista_dfs:
    logistica = pd.concat(lista_dfs, ignore_index=True)
    logistica = logistica[logistica['Nº NF'].notna()].copy()
    logistica['NF_NUM'] = pd.to_numeric(logistica['Nº NF'], errors='coerce')
    logistica = logistica.dropna(subset=['NF_NUM']).drop_duplicates('NF_NUM')
    colunas_keep = ['NF_NUM']
    if 'ROTA' in logistica.columns:
        logistica['ROTA'] = logistica['ROTA'].fillna('').astype(str).str.strip()
        colunas_keep.append('ROTA')
    logistica = logistica[colunas_keep]
else:
    logistica = pd.DataFrame(columns=['NF_NUM', 'ROTA'])

# ── Merge ──────────────────────────────────────────────────────────────────────

tabela_final = tabela_pedidos.merge(logistica, left_on='NUMNOTA_NUM', right_on='NF_NUM', how='left')
if 'ROTA' not in tabela_final.columns:
    tabela_final['ROTA'] = ''
tabela_final['ROTA'] = tabela_final['ROTA'].fillna('').astype(str).str.strip()

# ── Serialização ───────────────────────────────────────────────────────────────

def _s(v):
    return '' if pd.isna(v) else str(v).strip()

def _agrupar(df):
    result = []
    for numped, grp in df.groupby('NUMPED', sort=False):
        r0 = grp.iloc[0]
        result.append({
            'numped':  _s(numped),
            'numnota': _s(r0.get('NUMNOTA', '')),
            'data':    _s(r0['DATA']),
            'cliente': _s(r0['CLIENTE']),
            'rota':    _s(r0.get('ROTA', '')),
            'obs':     _s(r0['OBSENTREGA1']),
            'total':   round(float(grp['TOTAL'].sum()), 2),
            'itens': [
                {
                    'desc': _s(row['DESCRICAO']),
                    'qt':   int(row['QT']),
                    'val':  round(float(row['TOTAL']), 2),
                }
                for _, row in grp.iterrows()
            ],
        })
    return result

vendedores_out = []
for nome, grp in tabela_final.groupby('NOME'):
    em_rota     = grp[grp['ROTA'] != '']
    nao_emitido = grp[grp['NUMNOTA_NUM'].isna()]
    vendedores_out.append({
        'nome':        _s(nome),
        'em_rota':     _agrupar(em_rota),
        'nao_emitido': _agrupar(nao_emitido),
    })

payload = {
    'atualizado_em': datetime.now().strftime('%d/%m/%Y %H:%M'),
    'vendedores':    vendedores_out,
}

out = Path(__file__).parent / 'entregas_data.js'
with open(out, 'w', encoding='utf-8') as f:
    f.write(f"const ENTREGAS_DATA = {json.dumps(payload, ensure_ascii=False, indent=2)};\n")

print(f"OK — {len(vendedores_out)} vendedores → {out}")
