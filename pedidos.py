"""
Gera pedidos_data.js: visão única (todos os vendedores OFF TRADE juntos, em
todas as empresas/sistemas) dos pedidos dos últimos 90 dias, separados em
Pedidos Feitos (ainda sem nota), Faturados (com nota, não cancelados) e
Cancelados.

Multi-base igual ao exportacao_inadimplencia.py (CRC, thekings, CASTAS,
GARRIDO, SPON, MGON) usando PBI_PCPEDI de cada schema — view existe em
todas as 6 bases. Faturados cruza a NF com a planilha "Controle de Notas"
da logística (mesmo status ENTREGUE/RETORNO/CANCELADA/etc. usado em
exportacao_inadimplencia.py) pra popular o status, já que STATUS do
Winthor normalmente vem vazio pra pedido já faturado.
"""
import json
from datetime import datetime, date
from pathlib import Path

import pandas as pd

from meta import engine, engine_theking, engine_castas, engine_garrido, engine_spon, engine_mgon, carregar_dados
import baixar_planilhas_drive as _bpd

DIAS_JANELA = 90

_SPON_EXTRA = ['%W.S%']

_SOURCES = [
    ("CRC",      engine,         None,         (1, 2, 4)),
    ("thekings", engine_theking, None,         None),
    ("CASTAS",   engine_castas,  None,         None),
    ("GARRIDO",  engine_garrido, None,         None),
    ("SPON",     engine_spon,    _SPON_EXTRA,  None),
    ("MGON",     engine_mgon,    None,         None),
]


def _nome_filter(extra_nomes=None):
    base = "PED.NOME LIKE '%OFF TRADE%'"
    if extra_nomes:
        extras = " OR ".join(f"PED.NOME LIKE '{p}'" for p in extra_nomes)
        return f"({base} OR {extras})"
    return base


def _query_pedidos(schema, extra_nomes=None, filiais=None):
    nome_f = _nome_filter(extra_nomes)
    filial_f = f"AND PED.CODFILIAL IN ({','.join(map(str, filiais))})" if filiais else ""
    return f"""
        SELECT PED.NUMPED, PED.NUMNOTA, PED.NOME, PED.DATA, PED.CODUSUR, PED.CLIENTE, PED.STATUS,
               PED.DESCRICAO, PED.PVENDA, PED.QT, PED.TOTAL, PED.OBSENTREGA1,
               U.ESTADO AS ESTADO_VENDEDOR, S.NOME AS NOME_SUPERVISOR, G.NOMEGERENTE
        FROM {schema}.PBI_PCPEDI PED
        LEFT JOIN {schema}.PCUSUARI  U ON U.CODUSUR        = PED.CODUSUR
        LEFT JOIN {schema}.PCSUPERV  S ON U.CODSUPERVISOR  = S.CODSUPERVISOR
        LEFT JOIN {schema}.PCGERENTE G ON S.CODGERENTE     = G.CODGERENTE
        WHERE {nome_f}
          {filial_f}
          AND PED.DATA >= SYSDATE - {DIAS_JANELA}
    """


_parts = []
_fontes_indisponiveis = []
for _nome, _eng, _extra, _filiais in _SOURCES:
    try:
        _df = carregar_dados(_query_pedidos(_nome, _extra, _filiais), _eng, f"pedidos_{_nome}")
        _df['SISTEMA'] = _nome
        _parts.append(_df)
    except Exception as ex:
        print(f"[AVISO] pedidos_{_nome} falhou ({str(ex)[:80]}) — ignorado")
        _fontes_indisponiveis.append(_nome)

if not _parts:
    raise RuntimeError("Nenhuma base carregada.")

tabela_pedidos = pd.concat(_parts, ignore_index=True)
tabela_pedidos.columns = tabela_pedidos.columns.str.upper()
tabela_pedidos['TOTAL']       = pd.to_numeric(tabela_pedidos['TOTAL'], errors='coerce').fillna(0)
tabela_pedidos['QT']          = pd.to_numeric(tabela_pedidos['QT'],    errors='coerce').fillna(0).astype(int)
tabela_pedidos['NUMNOTA_NUM'] = pd.to_numeric(tabela_pedidos['NUMNOTA'], errors='coerce')
tabela_pedidos['DATA_DT']     = pd.to_datetime(tabela_pedidos['DATA'], errors='coerce')
tabela_pedidos['DATA']        = tabela_pedidos['DATA_DT'].dt.strftime('%d/%m/%Y')
tabela_pedidos['STATUS']      = tabela_pedidos['STATUS'].fillna('').astype(str).str.strip()

tabela_pedidos['ESTADO'] = tabela_pedidos['ESTADO_VENDEDOR'].fillna('').astype(str).str.strip().str.upper().replace('', 'Sem Estado')
tabela_pedidos['NOME_SUPERVISOR'] = tabela_pedidos['NOME_SUPERVISOR'].fillna('').astype(str).str.strip().replace('', 'Sem Supervisor')
tabela_pedidos['NOMEGERENTE']     = tabela_pedidos['NOMEGERENTE'].fillna('').astype(str).str.strip().replace('', 'Sem Gerente')

# ── Mapeia Oracle name → display name (igual ao entregas.py) ──────────────────

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
tabela_pedidos['NOME'] = tabela_pedidos['NOME'].str.strip()
# CODUSUR não é chave global — o mesmo número identifica pessoas diferentes em
# schemas diferentes (ex: CODUSUR 471 é "Paulo Junior" no SPON, mas RCA 471 na
# planilha de metas RJ é outra pessoa). Só remapeia o nome dentro do próprio
# CRC, que é a base de onde vem a planilha de metas.
_is_crc = tabela_pedidos['SISTEMA'] == 'CRC'
tabela_pedidos.loc[_is_crc, 'NOME'] = (
    tabela_pedidos.loc[_is_crc, 'CODUSUR_NUM']
    .map(_rca_to_display)
    .fillna(tabela_pedidos.loc[_is_crc, 'NOME'])
)

# ── Status de logística (planilha "Controle de Notas") ────────────────────────
# Só cobre NFs da logística RJ (CRC) — pedidos de outras bases ficam sem
# status_log, que é o esperado (mostrado como "—" na página).

_MESES_PT_STATUS = {
    '01': 'JANEIRO', '02': 'FEVEREIRO', '03': 'MARÇO', '04': 'ABRIL',
    '05': 'MAIO', '06': 'JUNHO', '07': 'JULHO', '08': 'AGOSTO',
    '09': 'SETEMBRO', '10': 'OUTUBRO', '11': 'NOVEMBRO', '12': 'DEZEMBRO',
}


def _meses_recentes(n=4):
    hoje = date.today()
    ano, mes = hoje.year, hoje.month
    meses = []
    for _ in range(n):
        meses.append((ano, mes))
        mes -= 1
        if mes == 0:
            mes, ano = 12, ano - 1
    return meses


def _caminho_controle_notas_local(ano, mm):
    upper = _MESES_PT_STATUS[mm]
    pasta_dir = Path(
        r"G:\Drives compartilhados\01-Logística\LOGÍSTICA RJ\APOIO LOGÍSTICO"
        r"\CONTROLE DE NOTAS"
    ) / str(ano) / f"{mm} {upper}"
    candidatos = list(pasta_dir.glob("*.xlsx")) if pasta_dir.exists() else []
    return str(candidatos[0]) if candidatos else str(pasta_dir)


_status_por_nf: dict = {}
for _ano, _mes in _meses_recentes():
    _mm = f"{_mes:02d}"
    _upper = _MESES_PT_STATUS[_mm]
    try:
        _caminho = _bpd.com_fallback(
            lambda mm=_mm, up=_upper: _bpd.caminho_controle_notas(mm, up),
            _caminho_controle_notas_local(_ano, _mm),
        )
        _abas = pd.read_excel(_caminho, sheet_name=None)
    except Exception as _ex:
        print(f"[AVISO] Controle de Notas {_mm}/{_ano} indisponível ({str(_ex)[:80]}) — ignorado")
        continue
    for _df_aba in _abas.values():
        if 'Nº NF' not in _df_aba.columns or 'STATUS' not in _df_aba.columns:
            continue
        _sub = _df_aba[['Nº NF', 'STATUS']].copy()
        _sub['Nº NF'] = pd.to_numeric(_sub['Nº NF'], errors='coerce')
        _sub = _sub.dropna(subset=['Nº NF'])
        for _, _r in _sub.iterrows():
            _status = str(_r['STATUS']).strip().upper() if pd.notna(_r['STATUS']) else ''
            if _status:
                _status_por_nf[int(_r['Nº NF'])] = _status

print(f"Status de logística: {len(_status_por_nf)} NF(s) mapeada(s) (últimos {_meses_recentes.__defaults__[0]} meses)")


def _status_log(numnota):
    try:
        return _status_por_nf.get(int(float(numnota)), '')
    except (ValueError, TypeError):
        return ''


# ── Separação em 3 baldes mutuamente exclusivos ────────────────────────────────

_cancelados = tabela_pedidos[tabela_pedidos['STATUS'] == 'CANCELADA']
_faturados  = tabela_pedidos[
    tabela_pedidos['NUMNOTA_NUM'].notna() & (tabela_pedidos['STATUS'] != 'CANCELADA')
]
_feitos     = tabela_pedidos[
    tabela_pedidos['NUMNOTA_NUM'].isna() & (tabela_pedidos['STATUS'] != 'CANCELADA')
]


def _s(v):
    return '' if pd.isna(v) else str(v).strip()


def _nf_clean(numnota):
    if pd.isna(numnota) or not numnota:
        return ''
    try:
        return str(int(float(numnota)))
    except (TypeError, ValueError):
        return str(numnota).strip()


def _agrupar(df, com_status_log=False):
    result = []
    for (sistema, numped), grp in df.groupby(['SISTEMA', 'NUMPED'], sort=False):
        r0 = grp.iloc[0]
        nf = _nf_clean(r0.get('NUMNOTA', ''))
        data_dt = r0.get('DATA_DT')
        item = {
            'numped':     _s(numped),
            'numnota':    nf,
            'data':       _s(r0['DATA']),
            'data_ord':   data_dt.strftime('%Y-%m-%d') if pd.notna(data_dt) else '',
            'nome':       _s(r0['NOME']),
            'cliente':    _s(r0['CLIENTE']),
            'sistema':    _s(sistema),
            'estado':     _s(r0['ESTADO']),
            'supervisor': _s(r0['NOME_SUPERVISOR']),
            'gerente':    _s(r0['NOMEGERENTE']),
            'status_ped': _s(r0['STATUS']),
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
        }
        if com_status_log:
            item['status_log'] = _status_log(nf) if nf else ''
        result.append(item)
    result.sort(key=lambda p: p['data_ord'], reverse=True)
    return result


payload = {
    'atualizado_em':        datetime.now().strftime('%d/%m/%Y %H:%M'),
    'periodo_dias':         DIAS_JANELA,
    'fontes_indisponiveis': _fontes_indisponiveis,
    'pedidos_feitos':       _agrupar(_feitos),
    'faturados':            _agrupar(_faturados, com_status_log=True),
    'cancelados':           _agrupar(_cancelados),
}

out = Path(__file__).parent / 'pedidos_data.js'
with open(out, 'w', encoding='utf-8') as f:
    f.write(f"const PEDIDOS_DATA = {json.dumps(payload, ensure_ascii=False, indent=2)};\n")

print(
    f"OK - {len(payload['pedidos_feitos'])} pedido(s) feito(s), "
    f"{len(payload['faturados'])} faturado(s), "
    f"{len(payload['cancelados'])} cancelado(s) -> {out}"
)

import subprocess

repo_dir = str(Path(__file__).parent)
try:
    subprocess.run(["git", "-C", repo_dir, "add", "pedidos_data.js"], check=True)
    subprocess.run(["git", "-C", repo_dir, "commit", "-m",
                    f"Atualiza pedidos_data.js - {datetime.now().strftime('%d/%m/%Y')}"])
    subprocess.run(["git", "-C", repo_dir, "push", "origin", "master"], check=True)
    print("OK pedidos_data.js enviado ao GitHub Pages.")
except subprocess.CalledProcessError:
    print("[AVISO] git push falhou — ignorado, pipeline continua.")
