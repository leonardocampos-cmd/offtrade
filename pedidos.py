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

from meta import engine, engine_theking, engine_castas, engine_garrido, engine_spon, engine_mgon, carregar_dados, carregar_paralelo
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


def _s(v):
    return '' if pd.isna(v) else str(v).strip()


def _int_s(v):
    if pd.isna(v):
        return ''
    try:
        return str(int(v))
    except (TypeError, ValueError):
        return ''


def _nome_filter(extra_nomes=None, alias='PED'):
    base = f"{alias}.NOME LIKE '%OFF TRADE%'"
    if extra_nomes:
        extras = " OR ".join(f"{alias}.NOME LIKE '{p}'" for p in extra_nomes)
        return f"({base} OR {extras})"
    return base


def _query_pedidos(schema, extra_nomes=None, filiais=None):
    nome_f = _nome_filter(extra_nomes)
    filial_f = f"AND PED.CODFILIAL IN ({','.join(map(str, filiais))})" if filiais else ""
    return f"""
        SELECT PED.NUMPED, PED.NUMNOTA, PED.NOME, PED.DATA, PED.CODUSUR, PED.CLIENTE, PED.STATUS,
               PED.DESCRICAO, PED.PVENDA, PED.QT, PED.QTFALTA, PED.TOTAL, PED.OBSENTREGA1,
               PED.FUNC_CANCEL, PC.POSICAO, PC.MOTIVOPOSICAO, PED.CODPROD, PED.CODFILIAL,
               U.ESTADO AS ESTADO_VENDEDOR, S.NOME AS NOME_SUPERVISOR, G.NOMEGERENTE
        FROM {schema}.PBI_PCPEDI PED
        LEFT JOIN {schema}.PCUSUARI  U ON U.CODUSUR        = PED.CODUSUR
        LEFT JOIN {schema}.PCSUPERV  S ON U.CODSUPERVISOR  = S.CODSUPERVISOR
        LEFT JOIN {schema}.PCGERENTE G ON S.CODGERENTE     = G.CODGERENTE
        LEFT JOIN {schema}.PCPEDC    PC ON PC.NUMPED       = PED.NUMPED
        WHERE {nome_f}
          {filial_f}
          AND PED.DATA >= SYSDATE - {DIAS_JANELA}
    """


def _query_cortes(schema):
    """PED.QTFALTA (PBI_PCPEDI) não é 'quanto foi cortado desta nota' — é um
    saldo de demanda que pode ultrapassar a própria quantidade do pedido
    (investigado em 2026-07-31: pedido com QT=126 e QTFALTA=382). A
    quantidade real cortada num evento de separação/corte fica em
    PCCORTEI.QTCORTADA (mesma estrutura em CRC/thekings/SPON, confirmado)."""
    return f"""
        SELECT NUMPED, CODPROD, CODFILIAL, SUM(QTCORTADA) AS QTCORTADA
        FROM {schema}.PCCORTEI
        WHERE DATA >= SYSDATE - {DIAS_JANELA}
        GROUP BY NUMPED, CODPROD, CODFILIAL
    """


_parts = []
_fontes_indisponiveis = []
_chamadas_pedidos = [
    (_query_pedidos(_nome, _extra, _filiais), _eng, f"pedidos_{_nome}")
    for _nome, _eng, _extra, _filiais in _SOURCES
]
for (_nome, _eng, _extra, _filiais), _res in zip(_SOURCES, carregar_paralelo(_chamadas_pedidos)):
    if isinstance(_res, Exception):
        print(f"[AVISO] pedidos_{_nome} falhou ({str(_res)[:80]}) — ignorado")
        _fontes_indisponiveis.append(_nome)
    else:
        _res['SISTEMA'] = _nome
        _parts.append(_res)

if not _parts:
    raise RuntimeError("Nenhuma base carregada.")

# ── Cortes reais (PCCORTEI.QTCORTADA) por (SISTEMA, NUMPED, CODPROD, CODFILIAL) ──
# Carregado à parte de _parts: fonte indisponível aqui não derruba pedidos_data.js
# inteiro, só zera o corte real dessa base (cai pra "sem corte" nesses itens).
_cortes_lookup = {}
_chamadas_cortes = [
    (_query_cortes(_nome), _eng, f"cortes_{_nome}")
    for _nome, _eng, _extra, _filiais in _SOURCES
]
for (_nome, _eng, _extra, _filiais), _res in zip(_SOURCES, carregar_paralelo(_chamadas_cortes)):
    if isinstance(_res, Exception):
        print(f"[AVISO] cortes_{_nome} falhou ({str(_res)[:80]}) — ignorado (corte real indisponível nessa base)")
        continue
    _res.columns = _res.columns.str.upper()
    for _, _row in _res.iterrows():
        try:
            _chave = (_nome, int(_row['NUMPED']), int(_row['CODPROD']), _int_s(_row['CODFILIAL']))
        except (TypeError, ValueError):
            continue
        _cortes_lookup[_chave] = _cortes_lookup.get(_chave, 0.0) + float(_row['QTCORTADA'] or 0)

tabela_pedidos = pd.concat(_parts, ignore_index=True)
tabela_pedidos.columns = tabela_pedidos.columns.str.upper()
tabela_pedidos['TOTAL']       = pd.to_numeric(tabela_pedidos['TOTAL'], errors='coerce').fillna(0)
tabela_pedidos['QT']          = pd.to_numeric(tabela_pedidos['QT'],    errors='coerce').fillna(0).astype(int)
tabela_pedidos['QTFALTA']     = pd.to_numeric(tabela_pedidos['QTFALTA'], errors='coerce').fillna(0)
tabela_pedidos['NUMNOTA_NUM'] = pd.to_numeric(tabela_pedidos['NUMNOTA'], errors='coerce')
tabela_pedidos['CODPROD_NUM']   = pd.to_numeric(tabela_pedidos['CODPROD'], errors='coerce')
tabela_pedidos['CODFILIAL_NUM'] = pd.to_numeric(tabela_pedidos['CODFILIAL'], errors='coerce')
tabela_pedidos['DATA_DT']     = pd.to_datetime(tabela_pedidos['DATA'], errors='coerce')
tabela_pedidos['DATA']        = tabela_pedidos['DATA_DT'].dt.strftime('%d/%m/%Y')
tabela_pedidos['STATUS']      = tabela_pedidos['STATUS'].fillna('').astype(str).str.strip()

tabela_pedidos['ESTADO'] = tabela_pedidos['ESTADO_VENDEDOR'].fillna('').astype(str).str.strip().str.upper().replace('', 'Sem Estado')
tabela_pedidos['NOME_SUPERVISOR'] = tabela_pedidos['NOME_SUPERVISOR'].fillna('').astype(str).str.strip().replace('', 'Sem Supervisor')
tabela_pedidos['NOMEGERENTE']     = tabela_pedidos['NOMEGERENTE'].fillna('').astype(str).str.strip().replace('', 'Sem Gerente')

# ── Posição do pedido (PCPEDC.POSICAO) e motivo (MOTIVOPOSICAO/FUNC_CANCEL) ────
# Códigos vistos em produção: F=Faturado, C=Cancelado, L=Liberado, B=Bloqueado,
# M=Bloqueado (alçada) — qualquer outro mostra o próprio código.
_POSICAO_LABEL = {
    'F': 'Faturado', 'C': 'Cancelado', 'L': 'Liberado',
    'B': 'Bloqueado', 'M': 'Bloqueado (alçada)', 'A': 'Digitando',
    'P': 'Pendente',
}
tabela_pedidos['POSICAO'] = tabela_pedidos['POSICAO'].fillna('').astype(str).str.strip().str.upper()
tabela_pedidos['POSICAO_LABEL'] = tabela_pedidos['POSICAO'].map(_POSICAO_LABEL).fillna(tabela_pedidos['POSICAO'])

tabela_pedidos['MOTIVOPOSICAO'] = tabela_pedidos['MOTIVOPOSICAO'].fillna('').astype(str).str.strip()
tabela_pedidos['FUNC_CANCEL']   = tabela_pedidos['FUNC_CANCEL'].fillna('').astype(str).str.strip()
tabela_pedidos['MOTIVO'] = tabela_pedidos['MOTIVOPOSICAO']
_sem_motivo_mas_cancelado = (tabela_pedidos['MOTIVO'] == '') & (tabela_pedidos['STATUS'] == 'CANCELADA') & (tabela_pedidos['FUNC_CANCEL'] != '')
tabela_pedidos.loc[_sem_motivo_mas_cancelado, 'MOTIVO'] = 'Cancelado por ' + tabela_pedidos.loc[_sem_motivo_mas_cancelado, 'FUNC_CANCEL']

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

# ── Status de entrega SP (Canhoto Digital / ComprovaFácil) ────────────────────
# Cache gerado por canhoto_digital.py (scraping, roda separado — ver esse
# arquivo). Cobre só pedidos do sistema SPON; outras bases ficam sem status
# aqui mesmo (RJ já vem de _status_por_nf acima).

_canhoto_path = Path(__file__).parent / 'canhoto_status.json'
_canhoto_por_nf: dict = {}
if _canhoto_path.exists():
    try:
        _canhoto_raw = json.loads(_canhoto_path.read_text(encoding='utf-8'))
        _canhoto_por_nf = {
            nf: (info.get('sub_status') or info.get('status') or '')
            for nf, info in _canhoto_raw.items()
        }
        print(f"Canhoto Digital: {len(_canhoto_por_nf)} NF(s) mapeada(s) (cache local)")
    except Exception as e:
        print(f"Aviso: falha ao ler canhoto_status.json ({e})")


def _status_log(numnota, sistema=''):
    if sistema == 'SPON':
        return _canhoto_por_nf.get(_nf_clean(numnota), '')
    try:
        return _status_por_nf.get(int(float(numnota)), '')
    except (ValueError, TypeError):
        return ''


# ── PCPREST — títulos já pagos saem de Faturados ───────────────────────────────
# "Faturado" aqui representa nota em aberto pro financeiro; uma vez que TODAS
# as parcelas do pedido (PCPREST.DTPAG) estão pagas, a nota sai da lista —
# mesma tabela usada em exportacao_inadimplencia.py, join por NUMPED (mais
# confiável que casar por DUPLIC/NUMNOTA em texto).

def _query_pcprest(schema, extra_nomes=None):
    nome_f = _nome_filter(extra_nomes, alias='U')
    return f"""
        SELECT P.NUMPED, P.DTPAG
        FROM {schema}.PCPREST P
        JOIN {schema}.PCUSUARI U ON U.CODUSUR = P.CODUSUR
        WHERE {nome_f}
          AND P.DTVENC >= SYSDATE - {DIAS_JANELA + 60}
    """


_pcprest_partes = []
_chamadas_pcprest = [
    (_query_pcprest(_nome, _extra), _eng, f"pcprest_{_nome}")
    for _nome, _eng, _extra, _filiais in _SOURCES
]
for (_nome, _eng, _extra, _filiais), _res in zip(_SOURCES, carregar_paralelo(_chamadas_pcprest)):
    if isinstance(_res, Exception):
        print(f"[AVISO] pcprest_{_nome} falhou ({str(_res)[:80]}) — ignorado (pagamentos dessa base ficam sem checar)")
    else:
        _pcprest_partes.append(_res)

_numpeds_pagos = set()
if _pcprest_partes:
    _tabela_pcprest = pd.concat(_pcprest_partes, ignore_index=True)
    _tabela_pcprest.columns = _tabela_pcprest.columns.str.upper()
    _tabela_pcprest['NUMPED'] = pd.to_numeric(_tabela_pcprest['NUMPED'], errors='coerce')
    _resumo_pag = _tabela_pcprest.groupby('NUMPED')['DTPAG'].agg(lambda s: s.notna().all())
    _numpeds_pagos = set(_resumo_pag[_resumo_pag].index)
    print(f"PCPREST: {len(_numpeds_pagos)} pedido(s) com todas as parcelas pagas — saem de Faturados")

# ── Separação em baldes mutuamente exclusivos ──────────────────────────────────
# "Cancelados" já cobre o corte total na prática: neste Winthor, STATUS só
# vira CANCELADA depois que a NF existe (as 364 notas canceladas do período
# têm NUMNOTA preenchido — não existe "cancelado sem nunca ter faturado").
# Corte parcial (entregou parte, faltou parte) fica dentro de Faturados
# mesmo, só marca quais itens foram cortados (ver _agrupar/'tem_corte').

_cancelados = tabela_pedidos[tabela_pedidos['STATUS'] == 'CANCELADA']
_faturados  = tabela_pedidos[
    tabela_pedidos['NUMNOTA_NUM'].notna()
    & (tabela_pedidos['STATUS'] != 'CANCELADA')
    & ~tabela_pedidos['NUMPED'].astype(str).isin({str(int(n)) for n in _numpeds_pagos})
]
_feitos     = tabela_pedidos[
    tabela_pedidos['NUMNOTA_NUM'].isna() & (tabela_pedidos['STATUS'] != 'CANCELADA')
]


def _nf_clean(numnota):
    if pd.isna(numnota) or not numnota:
        return ''
    try:
        return str(int(float(numnota)))
    except (TypeError, ValueError):
        return str(numnota).strip()


def _corte_real(sistema, numped, codprod_num, codfilial_num):
    """Quantidade real cortada (PCCORTEI.QTCORTADA) pro item, ou 0.0 se não
    achou (ou se a fonte de cortes dessa base falhou ao carregar)."""
    try:
        chave = (sistema, int(numped), int(codprod_num), _int_s(codfilial_num))
    except (TypeError, ValueError):
        return 0.0
    return _cortes_lookup.get(chave, 0.0)


def _item_pedido(sistema, numped, row):
    qt = int(row['QT'])
    qtfalta = float(row['QTFALTA'])
    qtcortada = _corte_real(sistema, numped, row['CODPROD_NUM'], row['CODFILIAL_NUM'])
    # QTFALTA (PBI_PCPEDI) é saldo ainda em aberto do pedido original, QTCORTADA
    # (PCCORTEI) é corte formal já registrado — juntos reconstituem o pedido
    # original: qt_original = qt (faturado) + qtcortada (cortado) + qtfalta
    # (ainda faltando). Confirmado manualmente no pedido 588003212 (Grey
    # Goose): 126 + 164 + 382 = 672.
    qtd_cortada_total = qtcortada + qtfalta
    return {
        'desc':              _s(row['DESCRICAO']),
        'qt':                qt,
        'val':               round(float(row['TOTAL']), 2),
        'qtfalta':           qtfalta,
        'qtcortada':         qtcortada,
        'qtd_cortada_total': qtd_cortada_total,
        'qt_original':       qt + qtd_cortada_total,
        'cortado':           qtd_cortada_total > 0,
        'codprod':   _int_s(row['CODPROD_NUM']),
        'codfilial': _int_s(row['CODFILIAL_NUM']),
    }


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
            'posicao':    _s(r0['POSICAO_LABEL']),
            'motivo':     _s(r0['MOTIVO']),
            'obs':        _s(r0['OBSENTREGA1']),
            'total':      round(float(grp['TOTAL'].sum()), 2),
            'itens': [
                _item_pedido(sistema, numped, row)
                for _, row in grp.iterrows()
            ],
        }
        item['tem_corte'] = any(it['cortado'] for it in item['itens'])
        if com_status_log:
            item['status_log'] = _status_log(nf, sistema) if nf else ''
        result.append(item)
    result.sort(key=lambda p: p['data_ord'], reverse=True)
    return result


def _extrair_cortados(pedidos_agrupados):
    """Achata os itens com 'cortado' (corte parcial: entregou parte, faltou
    parte) dos pedidos Faturados numa lista de produtos — cada linha é um
    item cortado com o pedido a que pertence, pra alimentar a aba "Produtos
    Cortados" (que cruza com o saldo de estoque atual no front-end via
    estoque_data.js)."""
    out = []
    for p in pedidos_agrupados:
        for it in p['itens']:
            if not it['cortado']:
                continue
            out.append({
                'numped':     p['numped'],
                'numnota':    p['numnota'],
                'data':       p['data'],
                'data_ord':   p['data_ord'],
                'nome':       p['nome'],
                'cliente':    p['cliente'],
                'sistema':    p['sistema'],
                'estado':     p['estado'],
                'supervisor': p['supervisor'],
                'gerente':    p['gerente'],
                'status_ped': p['status_ped'],
                'posicao':    p['posicao'],
                'motivo':     p['motivo'],
                'desc':       it['desc'],
                'codprod':    it['codprod'],
                'codfilial':  it['codfilial'],
                'qt':                it['qt'],
                'qtfalta':           it['qtfalta'],
                'qtcortada':         it['qtcortada'],
                'qtd_cortada_total': it['qtd_cortada_total'],
                'qt_original':       it['qt_original'],
                'val':        it['val'],
                'total':      it['val'],
            })
    return out


_faturados_agrupados = _agrupar(_faturados, com_status_log=True)

payload = {
    'atualizado_em':        datetime.now().strftime('%d/%m/%Y %H:%M'),
    'periodo_dias':         DIAS_JANELA,
    'fontes_indisponiveis': _fontes_indisponiveis,
    'pedidos_feitos':       _agrupar(_feitos),
    'faturados':            _faturados_agrupados,
    'cancelados':           _agrupar(_cancelados),
    'produtos_cortados':    sorted(
        _extrair_cortados(_faturados_agrupados),
        key=lambda r: r['data_ord'], reverse=True
    ),
}

out = Path(__file__).parent / 'pedidos_data.js'
with open(out, 'w', encoding='utf-8') as f:
    f.write(f"const PEDIDOS_DATA = {json.dumps(payload, ensure_ascii=False, indent=2)};\n")

print(
    f"OK - {len(payload['pedidos_feitos'])} pedido(s) feito(s), "
    f"{len(payload['faturados'])} faturado(s), "
    f"{len(payload['cancelados'])} cortado(s)/cancelado(s), "
    f"{len(payload['produtos_cortados'])} produto(s) com corte parcial -> {out}"
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
