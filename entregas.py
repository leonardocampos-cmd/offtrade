import pandas as pd
import oracledb
from sqlalchemy import create_engine
import json
from datetime import datetime, date
from pathlib import Path

_MESES_PT = {
    '01': ('JANEIRO',   'Janeiro'),
    '02': ('FEVEREIRO', 'Fevereiro'),
    '03': ('MARÇO',     'Março'),
    '04': ('ABRIL',     'Abril'),
    '05': ('MAIO',      'Maio'),
    '06': ('JUNHO',     'Junho'),
    '07': ('JULHO',     'Julho'),
    '08': ('AGOSTO',    'Agosto'),
    '09': ('SETEMBRO',  'Setembro'),
    '10': ('OUTUBRO',   'Outubro'),
    '11': ('NOVEMBRO',  'Novembro'),
    '12': ('DEZEMBRO',  'Dezembro'),
}

def _caminho_logistica(d: date) -> str:
    mm   = d.strftime('%m')
    ano  = d.strftime('%Y')
    upper, cap = _MESES_PT[mm]
    pasta   = f"{mm} {upper}"
    arquivo = f"{mm} FINANCEIRO - Controle de Notas {cap} {ano}.xlsx"
    return (
        r"G:\Drives compartilhados\01-Logística\LOGÍSTICA RJ\APOIO LOGÍSTICO"
        r"\CONTROLE DE SAÍDAS LOGÍSTICA\Controle de Saídas 2.0 - -"
        f"\\{ano}\\{pasta}\\{arquivo}"
    )

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

tabela_pedidos.columns     = tabela_pedidos.columns.str.upper()
tabela_pedidos['TOTAL']    = pd.to_numeric(tabela_pedidos['TOTAL'],   errors='coerce').fillna(0)
tabela_pedidos['QT']       = pd.to_numeric(tabela_pedidos['QT'],      errors='coerce').fillna(0).astype(int)
tabela_pedidos['NUMNOTA_NUM'] = pd.to_numeric(tabela_pedidos['NUMNOTA'], errors='coerce')
tabela_pedidos['DATA']     = pd.to_datetime(tabela_pedidos['DATA'],   errors='coerce').dt.strftime('%d/%m/%Y')
tabela_pedidos['STATUS']   = tabela_pedidos['STATUS'].fillna('').astype(str).str.strip()

# ── Mapeia Oracle name → display name (igual ao metas.html) ───────────────────

try:
    from meta import arquivo as _arquivo_meta
    _arq = _arquivo_meta[['RCA', 'VENDEDOR']].copy()
    _arq['RCA'] = pd.to_numeric(_arq['RCA'], errors='coerce')
    _arq = _arq.dropna(subset=['RCA', 'VENDEDOR']).drop_duplicates('RCA')
    _rca_to_display = dict(zip(_arq['RCA'], _arq['VENDEDOR'].str.strip()))
except Exception as e:
    print(f"Aviso: mapeamento de nomes falhou ({e}), usando nomes Oracle.")
    _rca_to_display = {}

tabela_pedidos['CODUSUR_NUM'] = pd.to_numeric(tabela_pedidos['CODUSUR'], errors='coerce')
tabela_pedidos['NOME'] = (
    tabela_pedidos['CODUSUR_NUM']
    .map(_rca_to_display)
    .fillna(tabela_pedidos['NOME'].str.strip())
)

# ── Excel de logística — apenas a aba de HOJE ─────────────────────────────────

hoje_str      = date.today().strftime('%d.%m')  # ex: "29.04"
caminho_excel = _caminho_logistica(date.today())

try:
    dict_abas = pd.read_excel(caminho_excel, sheet_name=None)
    df_hoje   = dict_abas.get(hoje_str, pd.DataFrame()).copy()
    print(f"Logística: {caminho_excel}")
except FileNotFoundError:
    print(f"Aviso: arquivo de logística não encontrado:\n  {caminho_excel}")
    dict_abas = {}
    df_hoje   = pd.DataFrame()

def _prep_logistica(df):
    cols = {'NF_NUM': [], 'ROTA': [], 'STATUS_LOG': [], 'MOTIVO': []}
    if df.empty or 'Nº NF' not in df.columns:
        return pd.DataFrame(cols)
    df = df[df['Nº NF'].notna()].copy()
    df['NF_NUM'] = pd.to_numeric(df['Nº NF'], errors='coerce')
    df = df.dropna(subset=['NF_NUM']).drop_duplicates('NF_NUM')
    out = pd.DataFrame()
    out['NF_NUM']     = df['NF_NUM']
    out['ROTA']       = df['ROTA'].fillna('').astype(str).str.strip()     if 'ROTA'   in df.columns else ''
    out['STATUS_LOG'] = df['STATUS'].fillna('').astype(str).str.strip()   if 'STATUS' in df.columns else ''
    out['MOTIVO']     = df['MOTIVO'].fillna('').astype(str).str.strip()   if 'MOTIVO' in df.columns else ''
    return out.reset_index(drop=True)

logistica_hoje = _prep_logistica(df_hoje)

# ── Merge pedidos × logística de hoje ─────────────────────────────────────────

tabela_final = tabela_pedidos.merge(logistica_hoje, left_on='NUMNOTA_NUM', right_on='NF_NUM', how='left')
for col in ['ROTA', 'STATUS_LOG', 'MOTIVO']:
    if col not in tabela_final.columns:
        tabela_final[col] = ''
    tabela_final[col] = tabela_final[col].fillna('').astype(str).str.strip()

# ── Serialização ───────────────────────────────────────────────────────────────

def _s(v):
    return '' if pd.isna(v) else str(v).strip()

def _agrupar(df):
    result = []
    for numped, grp in df.groupby('NUMPED', sort=False):
        r0 = grp.iloc[0]
        result.append({
            'numped':     _s(numped),
            'numnota':    _s(r0.get('NUMNOTA', '')),
            'data':       _s(r0['DATA']),
            'cliente':    _s(r0['CLIENTE']),
            'rota':       _s(r0.get('ROTA', '')),
            'status_ped': _s(r0['STATUS']),
            'status_log': _s(r0.get('STATUS_LOG', '')),
            'motivo':     _s(r0.get('MOTIVO', '')),
            'obs':        _s(r0['OBSENTREGA1']),
            'total':      round(float(grp['TOTAL'].sum()), 2),
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
    em_rota        = grp[grp['ROTA'] != '']
    nao_emitido    = grp[grp['NUMNOTA_NUM'].isna()]
    emitido_s_rota = grp[grp['NUMNOTA_NUM'].notna() & (grp['ROTA'] == '')]
    vendedores_out.append({
        'nome':           _s(nome),
        'em_rota':        _agrupar(em_rota),
        'nao_emitido':    _agrupar(nao_emitido),
        'emitido_s_rota': _agrupar(emitido_s_rota),
    })

payload = {
    'atualizado_em': datetime.now().strftime('%d/%m/%Y %H:%M'),
    'data_rota':     hoje_str,
    'vendedores':    vendedores_out,
}

import subprocess

out = Path(__file__).parent / 'entregas_data.js'
with open(out, 'w', encoding='utf-8') as f:
    f.write(f"const ENTREGAS_DATA = {json.dumps(payload, ensure_ascii=False, indent=2)};\n")

print(f"OK — {len(vendedores_out)} vendedores, rota do dia {hoje_str} → {out}")

repo_dir = str(Path(__file__).parent)
subprocess.run(["git", "-C", repo_dir, "add", "entregas_data.js"], check=True)
subprocess.run(["git", "-C", repo_dir, "commit", "-m",
                f"Atualiza entregas_data.js - {date.today().strftime('%d/%m/%Y')}"])
subprocess.run(["git", "-C", repo_dir, "push", "origin", "master"], check=True)
print("OK entregas_data.js enviado ao GitHub Pages.")
