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
    """Acha o Excel de controle de notas do mes, tolerando variacoes no nome
    do arquivo (ex: '06 JUNHO - Controle de Notas 2026.xlsx' vs
    '07 JULHO CONTROLE DE NOTAS.xlsx', '04 ABRIL - Controle de Notas Abril 2026.xlsx', '05 MAIO - Controle de Notas 2026.xlsx')."""
    mm   = d.strftime('%m')
    ano  = d.strftime('%Y')
    upper, cap = _MESES_PT[mm]
    pasta_dir = Path(
        r"G:\Drives compartilhados\01-Logística\LOGÍSTICA RJ\APOIO LOGÍSTICO"
        r"\CONTROLE DE NOTAS"
    ) / ano / f"{mm} {upper}"
    candidatos = [
        f for f in pasta_dir.glob("*.xlsx")
        if "controle de notas" in f.stem.lower()
    ]
    if candidatos:
        return str(max(candidatos, key=lambda f: f.stat().st_mtime))
    # Nenhum candidato encontrado: devolve o nome "padrao" so pra a mensagem
    # de erro do chamador mostrar onde deveria estar.
    return str(pasta_dir / f"{mm} {upper} - Controle de Notas {ano}.xlsx")

oracledb.init_oracle_client(lib_dir=r"C:\instantclient")
engine = create_engine(
    'oracle+oracledb://vpn:vpn2320vpn@crc_oci',
    pool_pre_ping=True,
    pool_recycle=3600,
    connect_args={"expire_time": 2}
)
from meta import carregar_dados

# ── Pedidos do mês ─────────────────────────────────────────────────────────────

def _limpar_pedidos(df):
    df = df.copy()
    df.columns     = df.columns.str.upper()
    df['TOTAL']    = pd.to_numeric(df['TOTAL'],   errors='coerce').fillna(0)
    df['QT']       = pd.to_numeric(df['QT'],      errors='coerce').fillna(0).astype(int)
    df['NUMNOTA_NUM'] = pd.to_numeric(df['NUMNOTA'], errors='coerce')
    df['DATA']     = pd.to_datetime(df['DATA'],   errors='coerce').dt.strftime('%d/%m/%Y')
    df['STATUS']   = df['STATUS'].fillna('').astype(str).str.strip()
    return df

tabela_pedidos = carregar_dados("""
    SELECT NUMPED, NUMNOTA, NOME, DATA, CODUSUR, CLIENTE, STATUS,
           DESCRICAO, PVENDA, QT, TOTAL, OBSENTREGA1
    FROM crc.PBI_PCPEDI
    WHERE CODFILIAL IN (2,4)
      AND NOME LIKE '%OFF TRADE%'
      AND DATA >= TRUNC(SYSDATE, 'MM')
      AND DATA < LAST_DAY(SYSDATE) + 1
    ORDER BY DATA DESC
""", engine, "pedidos")
tabela_pedidos = _limpar_pedidos(tabela_pedidos)

# Notas emitidas e nao canceladas dos ultimos N dias (nao so o mes corrente) —
# usada so pra achar "Emitida / Sem rota" que ficou pra tras em meses anteriores.
DIAS_NOTAS_ABERTAS = 90
tabela_pedidos_abertos = carregar_dados(f"""
    SELECT NUMPED, NUMNOTA, NOME, DATA, CODUSUR, CLIENTE, STATUS,
           DESCRICAO, PVENDA, QT, TOTAL, OBSENTREGA1
    FROM crc.PBI_PCPEDI
    WHERE CODFILIAL IN (2,4)
      AND NOME LIKE '%OFF TRADE%'
      AND NUMNOTA IS NOT NULL
      AND NVL(STATUS, 'X') != 'CANCELADA'
      AND DATA >= SYSDATE - {DIAS_NOTAS_ABERTAS}
    ORDER BY DATA DESC
""", engine, "pedidos_abertos")
tabela_pedidos_abertos = _limpar_pedidos(tabela_pedidos_abertos)

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

tabela_pedidos_abertos['CODUSUR_NUM'] = pd.to_numeric(tabela_pedidos_abertos['CODUSUR'], errors='coerce')
tabela_pedidos_abertos['NOME'] = (
    tabela_pedidos_abertos['CODUSUR_NUM']
    .map(_rca_to_display)
    .fillna(tabela_pedidos_abertos['NOME'].str.strip())
)

# ── Excel de logística — apenas a aba de HOJE ─────────────────────────────────

_hoje_d       = date.today()
hoje_str      = _hoje_d.strftime('%d.%m')   # "05.05"
hoje_str_hif  = _hoje_d.strftime('%d-%m')   # "05-05"
caminho_excel = _caminho_logistica(_hoje_d)

try:
    dict_abas = pd.read_excel(caminho_excel, sheet_name=None)
    aba_nome  = next((k for k in dict_abas if str(k) in (hoje_str, hoje_str_hif)), None)
    if aba_nome:
        df_hoje = dict_abas[aba_nome].copy()
    else:
        # Arquivo com aba única + coluna DATA
        df_all = pd.concat(dict_abas.values(), ignore_index=True)
        if 'DATA' in df_all.columns:
            df_all['_data_fmt'] = pd.to_datetime(df_all['DATA'], errors='coerce').dt.strftime('%d.%m')
            df_hoje = df_all[df_all['_data_fmt'] == hoje_str].drop(columns='_data_fmt').copy()
        else:
            df_hoje = df_all.copy()
    print(f"Logística: {caminho_excel} | aba: {aba_nome or 'DATA'} | {len(df_hoje)} linhas")
except FileNotFoundError:
    print(f"Aviso: arquivo de logística não encontrado:\n  {caminho_excel}")
    df_hoje = pd.DataFrame()

def _prep_logistica(df):
    cols = {'NF_NUM': [], 'PLACA': [], 'ROTA': [], 'STATUS_LOG': [], 'MOTIVO': []}
    if df.empty or 'Nº NF' not in df.columns:
        return pd.DataFrame(cols)
    df = df[df['Nº NF'].notna()].copy()
    df['NF_NUM'] = pd.to_numeric(df['Nº NF'], errors='coerce')
    df = df.dropna(subset=['NF_NUM']).drop_duplicates('NF_NUM')
    out = pd.DataFrame()
    out['NF_NUM']     = df['NF_NUM']
    out['PLACA']      = df['PLACA'].fillna('').astype(str).str.strip()    if 'PLACA'  in df.columns else ''
    out['ROTA']       = df['ROTA'].fillna('').astype(str).str.strip()     if 'ROTA'   in df.columns else ''
    out['STATUS_LOG'] = df['STATUS'].fillna('').astype(str).str.strip()   if 'STATUS' in df.columns else ''
    out['MOTIVO']     = df['MOTIVO'].fillna('').astype(str).str.strip()   if 'MOTIVO' in df.columns else ''
    return out.reset_index(drop=True)

logistica_hoje = _prep_logistica(df_hoje)

# ── Merge pedidos × logística de hoje ─────────────────────────────────────────

tabela_final = tabela_pedidos.merge(logistica_hoje, left_on='NUMNOTA_NUM', right_on='NF_NUM', how='left')
for col in ['PLACA', 'ROTA', 'STATUS_LOG', 'MOTIVO']:
    if col not in tabela_final.columns:
        tabela_final[col] = ''
    tabela_final[col] = tabela_final[col].fillna('').astype(str).str.strip()

tabela_final_abertos = tabela_pedidos_abertos.merge(logistica_hoje, left_on='NUMNOTA_NUM', right_on='NF_NUM', how='left')
for col in ['PLACA', 'ROTA', 'STATUS_LOG', 'MOTIVO']:
    if col not in tabela_final_abertos.columns:
        tabela_final_abertos[col] = ''
    tabela_final_abertos[col] = tabela_final_abertos[col].fillna('').astype(str).str.strip()

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
            'placa':      _s(r0.get('PLACA', '')),
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

_grupos_mes     = dict(tuple(tabela_final.groupby('NOME')))
_grupos_abertos = dict(tuple(tabela_final_abertos.groupby('NOME')))
_vazio_mes      = tabela_final.iloc[0:0]
_vazio_abertos  = tabela_final_abertos.iloc[0:0]

vendedores_out = []
for nome in sorted(set(_grupos_mes) | set(_grupos_abertos)):
    grp            = _grupos_mes.get(nome, _vazio_mes)
    grp_abertos    = _grupos_abertos.get(nome, _vazio_abertos)
    em_rota        = grp[grp['ROTA'] != '']
    nao_emitido    = grp[grp['NUMNOTA_NUM'].isna()]
    emitido_s_rota = grp_abertos[grp_abertos['NUMNOTA_NUM'].notna() & (grp_abertos['ROTA'] == '')]
    vendedores_out.append({
        'nome':           _s(nome),
        'em_rota':        _agrupar(em_rota),
        'nao_emitido':    _agrupar(nao_emitido),
        'emitido_s_rota': _agrupar(emitido_s_rota),
        'nao_entregue':   [],
    })

# ── Alertas de não entrega (bot Gmail) ────────────────────────────────────────

def _nf_clean(numnota):
    try:
        return str(int(float(numnota))) if numnota else ''
    except Exception:
        return str(numnota).strip()

alertas_path = Path(__file__).parent / 'alertas_rj.json'
if alertas_path.exists():
    try:
        _alertas_all = json.loads(alertas_path.read_text(encoding='utf-8'))
        hoje_iso = _hoje_d.isoformat()
        _nfs_alerta = {item['nf']: item for item in _alertas_all.get(hoje_iso, [])}
        if _nfs_alerta:
            print(f"Alertas de não entrega: {len(_nfs_alerta)} NF(s) para hoje")
            for v in vendedores_out:
                nfs_ja = set()
                for lista_key in ('em_rota', 'emitido_s_rota'):
                    restantes = []
                    for ped in v[lista_key]:
                        nf = _nf_clean(ped.get('numnota', ''))
                        if nf in _nfs_alerta and nf not in nfs_ja:
                            info = _nfs_alerta[nf]
                            ped = dict(ped)
                            ped['motivo_alerta']      = info.get('motivo', '')
                            ped['responsavel_alerta'] = info.get('responsavel', '')
                            v['nao_entregue'].append(ped)
                            nfs_ja.add(nf)
                        else:
                            restantes.append(ped)
                    v[lista_key] = restantes
    except Exception as e:
        print(f"Aviso: falha ao carregar alertas_rj.json: {e}")

payload = {
    'atualizado_em': datetime.now().strftime('%d/%m/%Y %H:%M'),
    'data_rota':     hoje_str,
    'vendedores':    vendedores_out,
}

import subprocess

out = Path(__file__).parent / 'entregas_data.js'
with open(out, 'w', encoding='utf-8') as f:
    f.write(f"const ENTREGAS_DATA = {json.dumps(payload, ensure_ascii=False, indent=2)};\n")

print(f"OK - {len(vendedores_out)} vendedores, rota do dia {hoje_str} -> {out}")

repo_dir = str(Path(__file__).parent)
subprocess.run(["git", "-C", repo_dir, "add", "entregas_data.js"], check=True)
subprocess.run(["git", "-C", repo_dir, "commit", "-m",
                f"Atualiza entregas_data.js - {date.today().strftime('%d/%m/%Y')}"])
subprocess.run(["git", "-C", repo_dir, "push", "origin", "master"], check=True)
print("OK entregas_data.js enviado ao GitHub Pages.")
