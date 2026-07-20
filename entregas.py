import os
import pandas as pd
import oracledb
from sqlalchemy import create_engine
import json
from datetime import datetime, date
from pathlib import Path
import baixar_planilhas_drive as _bpd

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

def _caminho_logistica_local(d: date) -> Path:
    """Fallback: acha o Excel direto na pasta sincronizada G:\\... (usado
    quando o Drive falha e a máquina local ainda tem o Drive Desktop)."""
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
        return max(candidatos, key=lambda f: f.stat().st_mtime)
    return pasta_dir / f"{mm} {upper} - Controle de Notas {ano}.xlsx"


def _caminho_logistica(d: date) -> str:
    """Acha o Excel de controle de notas do mes (via Drive), tolerando
    variacoes no nome do arquivo (ex: '06 JUNHO - Controle de Notas 2026.xlsx'
    vs '07 JULHO CONTROLE DE NOTAS.xlsx'). Cai pro caminho G:\\... sincronizado
    localmente se o Drive falhar."""
    mm   = d.strftime('%m')
    upper, cap = _MESES_PT[mm]
    return str(_bpd.com_fallback(
        lambda: _bpd.caminho_controle_notas(mm, upper),
        str(_caminho_logistica_local(d)),
    ))

from utils import ORACLE_LIB
oracledb.init_oracle_client(lib_dir=ORACLE_LIB)
_user = os.environ["VPN_USER"]
_pass = os.environ["VPN_PASSWORD"]
engine = create_engine(
    f'oracle+oracledb://{_user}:{_pass}@crc_oci',
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
    df['DATA_DT']  = pd.to_datetime(df['DATA'],   errors='coerce')
    df['DATA']     = df['DATA_DT'].dt.strftime('%d/%m/%Y')
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
           DESCRICAO, PVENDA, QT, QTFALTA, TOTAL, OBSENTREGA1
    FROM crc.PBI_PCPEDI
    WHERE CODFILIAL IN (2,4)
      AND NOME LIKE '%OFF TRADE%'
      AND NUMNOTA IS NOT NULL
      AND NVL(STATUS, 'X') != 'CANCELADA'
      AND DATA >= SYSDATE - {DIAS_NOTAS_ABERTAS}
    ORDER BY DATA DESC
""", engine, "pedidos_abertos")
tabela_pedidos_abertos = _limpar_pedidos(tabela_pedidos_abertos)

# QTFALTA=0 em todos os itens do pedido => ja foi totalmente entregue (mesmo sem
# rota hoje, porque foi entregue no dia dele). So conta como "aberta" quando
# ainda falta quantidade, ou quando QTFALTA nunca foi preenchida (incerto).
tabela_pedidos_abertos['QTFALTA'] = pd.to_numeric(tabela_pedidos_abertos['QTFALTA'], errors='coerce')
_qtfalta_pedido = tabela_pedidos_abertos.groupby('NUMPED')['QTFALTA'].transform(lambda s: s.sum(min_count=1))
tabela_pedidos_abertos['_JA_ENTREGUE'] = _qtfalta_pedido == 0

# Pedidos cancelados dos ultimos N dias — aba separada, so pra visibilidade.
tabela_pedidos_cancelados = carregar_dados(f"""
    SELECT NUMPED, NUMNOTA, NOME, DATA, CODUSUR, CLIENTE, STATUS,
           DESCRICAO, PVENDA, QT, TOTAL, OBSENTREGA1
    FROM crc.PBI_PCPEDI
    WHERE CODFILIAL IN (2,4)
      AND NOME LIKE '%OFF TRADE%'
      AND STATUS = 'CANCELADA'
      AND DATA >= SYSDATE - {DIAS_NOTAS_ABERTAS}
    ORDER BY DATA DESC
""", engine, "pedidos_cancelados")
tabela_pedidos_cancelados = _limpar_pedidos(tabela_pedidos_cancelados)

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

tabela_pedidos_cancelados['CODUSUR_NUM'] = pd.to_numeric(tabela_pedidos_cancelados['CODUSUR'], errors='coerce')
tabela_pedidos_cancelados['NOME'] = (
    tabela_pedidos_cancelados['CODUSUR_NUM']
    .map(_rca_to_display)
    .fillna(tabela_pedidos_cancelados['NOME'].str.strip())
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

# ── Histórico de rotas (todas as abas dos meses relevantes) ───────────────────
# "Em Rota hoje" só olha a data de hoje (acima). Pro resto das notas
# (Emitida/Sem rota), verifica se a nota já teve rota registrada em QUALQUER
# dia, usando o(s) mês(es) da própria data de emissão em PBI_PCPEDI — não só
# a planilha de hoje. Isso evita mostrar como "sem rota" notas antigas que já
# foram roteadas/entregues no dia delas (ex: emitida 08/04, roteada 09/04).

_meses_relevantes = sorted({
    (d.year, d.month) for d in tabela_pedidos_abertos['DATA_DT'].dropna()
})

_historico_partes = []
for _ano, _mm in _meses_relevantes:
    _caminho_mes = _caminho_logistica(date(_ano, _mm, 1))
    try:
        _abas_mes = pd.read_excel(_caminho_mes, sheet_name=None)
    except FileNotFoundError:
        print(f"Aviso: arquivo de logística de {_mm:02d}/{_ano} não encontrado: {_caminho_mes}")
        continue
    for _df_aba in _abas_mes.values():
        _prep = _prep_logistica(_df_aba)
        if not _prep.empty:
            _historico_partes.append(_prep)
    print(f"Histórico logística {_mm:02d}/{_ano}: {_caminho_mes} | {len(_abas_mes)} aba(s)")

logistica_historico = pd.concat(_historico_partes, ignore_index=True) if _historico_partes else pd.DataFrame(columns=['NF_NUM', 'ROTA'])
nfs_ja_roteadas = set(
    logistica_historico.loc[logistica_historico['ROTA'] != '', 'NF_NUM'].dropna().astype(int)
)
print(f"   {len(nfs_ja_roteadas)} NF(s) com rota já registrada em algum dia (histórico)")

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

def _agrupar_por_nf(df):
    """Indice por NUMNOTA (nao NUMPED) cobrindo todos os pedidos conhecidos,
    independente de qual 'balde' (em_rota/emitido_s_rota/etc) a nota caiu —
    usado como fallback pro alerta de nao-entrega achar notas que ja tiveram
    rota num dia passado e por isso nao aparecem em nenhuma das listas de
    hoje (nem 'em_rota', que so' olha a rota de hoje, nem 'emitido_s_rota',
    que exclui de proposito quem ja teve rota algum dia)."""
    result = {}
    df_com_nota = df[df['NUMNOTA_NUM'].notna()]
    for numnota, grp in df_com_nota.groupby('NUMNOTA_NUM'):
        agrupado = _agrupar(grp)
        if agrupado:
            result[int(numnota)] = agrupado[0]
    return result

_pedidos_por_nf = {**_agrupar_por_nf(tabela_final_abertos), **_agrupar_por_nf(tabela_final)}

_grupos_mes        = dict(tuple(tabela_final.groupby('NOME')))
_grupos_abertos    = dict(tuple(tabela_final_abertos.groupby('NOME')))
_grupos_cancelados = dict(tuple(tabela_pedidos_cancelados.groupby('NOME')))
_vazio_mes         = tabela_final.iloc[0:0]
_vazio_abertos     = tabela_final_abertos.iloc[0:0]
_vazio_cancelados  = tabela_pedidos_cancelados.iloc[0:0]

vendedores_out = []
for nome in sorted(set(_grupos_mes) | set(_grupos_abertos) | set(_grupos_cancelados)):
    grp            = _grupos_mes.get(nome, _vazio_mes)
    grp_abertos    = _grupos_abertos.get(nome, _vazio_abertos)
    grp_cancelados = _grupos_cancelados.get(nome, _vazio_cancelados)
    em_rota        = grp[grp['ROTA'] != '']
    nao_emitido    = grp[grp['NUMNOTA_NUM'].isna()]
    emitido_s_rota = grp_abertos[
        grp_abertos['NUMNOTA_NUM'].notna()
        & (grp_abertos['ROTA'] == '')
        & ~grp_abertos['_JA_ENTREGUE']
        & ~grp_abertos['NUMNOTA_NUM'].isin(nfs_ja_roteadas)
    ]
    vendedores_out.append({
        'nome':           _s(nome),
        'em_rota':        _agrupar(em_rota),
        'nao_emitido':    _agrupar(nao_emitido),
        'emitido_s_rota': _agrupar(emitido_s_rota),
        'canceladas':     _agrupar(grp_cancelados),
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
            _nfs_restantes = dict(_nfs_alerta)
            _vendedor_por_nome = {v['nome']: v for v in vendedores_out}
            for v in vendedores_out:
                nfs_ja = set()
                for lista_key in ('em_rota', 'emitido_s_rota'):
                    restantes = []
                    for ped in v[lista_key]:
                        nf = _nf_clean(ped.get('numnota', ''))
                        if nf in _nfs_restantes and nf not in nfs_ja:
                            info = _nfs_restantes.pop(nf)
                            ped = dict(ped)
                            ped['motivo_alerta']      = info.get('motivo', '')
                            ped['responsavel_alerta'] = info.get('responsavel', '')
                            v['nao_entregue'].append(ped)
                            nfs_ja.add(nf)
                        else:
                            restantes.append(ped)
                    v[lista_key] = restantes

            # Fallback: NF que ja teve rota num dia passado e falhou entao nao
            # esta em 'em_rota' (so' olha rota de hoje) nem 'emitido_s_rota'
            # (exclui de proposito quem ja teve rota algum dia) — busca o
            # pedido no indice global por NF e usa o RCA do proprio alerta
            # pra achar o vendedor certo.
            for nf, info in _nfs_restantes.items():
                rca_num = info.get('rca')
                nome_display = _rca_to_display.get(float(rca_num)) if rca_num is not None else None
                nome_display = nome_display or info.get('rca_nome', '')
                v = _vendedor_por_nome.get(_s(nome_display))
                if v is None:
                    continue
                try:
                    ped = dict(_pedidos_por_nf[int(nf)])
                except (KeyError, ValueError):
                    ped = {'numped': '', 'numnota': nf, 'data': '', 'cliente': info.get('cliente', ''),
                           'placa': '', 'rota': info.get('rota', ''), 'status_ped': '', 'status_log': '',
                           'motivo': '', 'obs': '', 'total': 0.0, 'itens': []}
                ped['motivo_alerta']      = info.get('motivo', '')
                ped['responsavel_alerta'] = info.get('responsavel', '')
                v['nao_entregue'].append(ped)
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
try:
    subprocess.run(["git", "-C", repo_dir, "add", "entregas_data.js"], check=True)
    subprocess.run(["git", "-C", repo_dir, "commit", "-m",
                    f"Atualiza entregas_data.js - {date.today().strftime('%d/%m/%Y')}"])
    subprocess.run(["git", "-C", repo_dir, "push", "origin", "master"], check=True)
    print("OK entregas_data.js enviado ao GitHub Pages.")
except subprocess.CalledProcessError:
    print("[AVISO] git push falhou — ignorado, pipeline continua.")
