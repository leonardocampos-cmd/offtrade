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
import re
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
               PED.FUNC_CANCEL, PC.POSICAO, PC.MOTIVOPOSICAO, PC.VLBONIFIC, PED.CODPROD, PED.CODFILIAL,
               PED.FORNECEDOR, PED.FANTASIA_FORNEC,
               U.ESTADO AS ESTADO_VENDEDOR, S.NOME AS NOME_SUPERVISOR, G.NOMEGERENTE,
               CL.MUNICENT AS CIDADE_CLIENTE
        FROM {schema}.PBI_PCPEDI PED
        LEFT JOIN {schema}.PCUSUARI  U  ON U.CODUSUR        = PED.CODUSUR
        LEFT JOIN {schema}.PCSUPERV  S  ON U.CODSUPERVISOR  = S.CODSUPERVISOR
        LEFT JOIN {schema}.PCGERENTE G  ON S.CODGERENTE     = G.CODGERENTE
        LEFT JOIN {schema}.PCPEDC    PC ON PC.NUMPED        = PED.NUMPED
        LEFT JOIN {schema}.PCCLIENT  CL ON CL.CODCLI        = PED.CODCLI
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


def _query_motivo_corte(schema):
    """PEDIDOS_CANCELADOS.MOTIVO tem o motivo de verdade por produto (ex:
    "CORTE TOTAL NO CARREGAMENTO", "FALTA DE PRODUTO") — bem mais específico
    que MOTIVOPOSICAO (PCPEDC, nível pedido). Cobre tanto corte total pré-NF
    quanto corte parcial dentro de pedido já faturado (confirmado: NUMPED
    352000099 aparece aqui E em PBI_PCPEDI com NUMNOTA preenchido).

    SUBTOT (QT*PVENDA registrado no momento do cancelamento) é o valor real
    do corte — mais confiável que estimar via TOTAL/QT da nota (que em
    cancelamento total é sempre 0, cai pro PVENDA "digitado" genérico)."""
    return f"""
        SELECT NUMPED, CODPROD, MOTIVO, DATACANC, QT, PVENDA, SUBTOT
        FROM {schema}.PEDIDOS_CANCELADOS
        WHERE DATACANC >= SYSDATE - {DIAS_JANELA}
    """


# thekings não tem essa view (ORA-00942, mesmo motivo documentado em
# exportacao_meta.py) — excluída daqui pra não derrubar engine_theking pro
# resto do script (3 retentativas + 10s cada por fonte morta).
_SOURCES_COM_MOTIVO_CORTE = [s for s in _SOURCES if s[0] != 'thekings']


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

# ── Motivo real do corte (PEDIDOS_CANCELADOS.MOTIVO), por (SISTEMA, NUMPED,
# CODPROD), e valor real cortado (PEDIDOS_CANCELADOS.SUBTOT) por (SISTEMA,
# NUMPED) — só por pedido, não por produto: a view registra produtos
# cortados ao longo de todo o histórico do pedido (inclusive substituições
# anteriores), então o CODPROD de lá pode não ter nenhuma relação com o
# item atual do PBI_PCPEDI pro mesmo NUMPED (confirmado: pedido 431001229
# tem 10 produtos no PBI_PCPEDI e 2 produtos completamente diferentes na
# view) — juntar por produto atribuiria valor ao item errado. Somando por
# pedido inteiro, o total fica confiável mesmo sem casar produto a produto.
_motivo_corte_lookup = {}
_valor_corte_pedido_lookup = {}
_motivo_corte_pedido_lookup = {}  # (SISTEMA, NUMPED) -> lista de motivos distintos (todos os produtos)
_chamadas_motivo_corte = [
    (_query_motivo_corte(_nome), _eng, f"motivo_corte_{_nome}")
    for _nome, _eng, _extra, _filiais in _SOURCES_COM_MOTIVO_CORTE
]
for (_nome, _eng, _extra, _filiais), _res in zip(_SOURCES_COM_MOTIVO_CORTE, carregar_paralelo(_chamadas_motivo_corte)):
    if isinstance(_res, Exception):
        print(f"[AVISO] motivo_corte_{_nome} falhou ({str(_res)[:80]}) — ignorado (motivo de corte dessa base cai pro motivo genérico)")
        continue
    _res.columns = _res.columns.str.upper()
    _res['DATACANC'] = pd.to_datetime(_res['DATACANC'], errors='coerce')
    _res = _res.sort_values('DATACANC')  # último (mais recente) sobrescreve os anteriores no loop abaixo
    for _, _row in _res.iterrows():
        try:
            _chave = (_nome, int(_row['NUMPED']), int(_row['CODPROD']))
        except (TypeError, ValueError):
            continue
        _motivo = str(_row['MOTIVO'] or '').strip()
        _chave_pedido = (_nome, int(_row['NUMPED']))
        if _motivo:
            _motivo_corte_lookup[_chave] = _motivo
            _motivo_corte_pedido_lookup.setdefault(_chave_pedido, [])
            if _motivo not in _motivo_corte_pedido_lookup[_chave_pedido]:
                _motivo_corte_pedido_lookup[_chave_pedido].append(_motivo)
        _subtot = pd.to_numeric(_row.get('SUBTOT'), errors='coerce')
        if pd.notna(_subtot):
            _valor_corte_pedido_lookup[_chave_pedido] = _valor_corte_pedido_lookup.get(_chave_pedido, 0.0) + float(_subtot)

tabela_pedidos = pd.concat(_parts, ignore_index=True)
tabela_pedidos.columns = tabela_pedidos.columns.str.upper()
tabela_pedidos['TOTAL']       = pd.to_numeric(tabela_pedidos['TOTAL'], errors='coerce').fillna(0)
tabela_pedidos['QT']          = pd.to_numeric(tabela_pedidos['QT'],    errors='coerce').fillna(0).astype(int)
tabela_pedidos['QTFALTA']     = pd.to_numeric(tabela_pedidos['QTFALTA'], errors='coerce').fillna(0)
tabela_pedidos['NUMNOTA_NUM'] = pd.to_numeric(tabela_pedidos['NUMNOTA'], errors='coerce')
tabela_pedidos['VLBONIFIC_NUM'] = pd.to_numeric(tabela_pedidos['VLBONIFIC'], errors='coerce').fillna(0)
tabela_pedidos['CODPROD_NUM']   = pd.to_numeric(tabela_pedidos['CODPROD'], errors='coerce')
tabela_pedidos['CODFILIAL_NUM'] = pd.to_numeric(tabela_pedidos['CODFILIAL'], errors='coerce')
tabela_pedidos['DATA_DT']     = pd.to_datetime(tabela_pedidos['DATA'], errors='coerce')
tabela_pedidos['DATA']        = tabela_pedidos['DATA_DT'].dt.strftime('%d/%m/%Y')
tabela_pedidos['STATUS']      = tabela_pedidos['STATUS'].fillna('').astype(str).str.strip()

tabela_pedidos['CIDADE'] = tabela_pedidos['CIDADE_CLIENTE'].fillna('').astype(str).str.strip()
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


_RE_ABA_DATA = re.compile(r'^(\d{1,2})[.\-](\d{1,2})$')


def _data_da_aba(nome_aba, ano):
    """Aba é nomeada 'dd.mm' ou 'dd-mm' (dia da rota/entrega) — mesmo padrão
    já usado pra achar a aba de hoje (_hoje_str/_hoje_str_hif). Devolve
    'YYYY-MM-DD' ou None se o nome não bater com esse padrão."""
    m = _RE_ABA_DATA.match(str(nome_aba).strip())
    if not m:
        return None
    dd, mm = int(m.group(1)), int(m.group(2))
    try:
        return date(ano, mm, dd).strftime('%Y-%m-%d')
    except ValueError:
        return None


_status_por_nf: dict = {}
_data_entrega_por_nf: dict = {}
# Do mais antigo pro mais recente: uma NF pode aparecer em mais de um mês
# (reentrega depois de "VOLTOU" etc.) — o mês mais recente tem que
# sobrescrever o mais antigo, não o contrário (_meses_recentes() devolve
# do mais recente pro mais antigo).
for _ano, _mes in reversed(_meses_recentes()):
    _mm = f"{_mes:02d}"
    _upper = _MESES_PT_STATUS[_mm]
    try:
        _caminho = _bpd.com_fallback(
            lambda mm=_mm, up=_upper, an=_ano: _bpd.caminho_controle_notas(mm, up, an),
            _caminho_controle_notas_local(_ano, _mm),
        )
        _abas = pd.read_excel(_caminho, sheet_name=None)
    except Exception as _ex:
        print(f"[AVISO] Controle de Notas {_mm}/{_ano} indisponível ({str(_ex)[:80]}) — ignorado")
        continue
    for _nome_aba, _df_aba in _abas.items():
        if 'Nº NF' not in _df_aba.columns or 'STATUS' not in _df_aba.columns:
            continue
        _data_aba = _data_da_aba(_nome_aba, _ano)
        _sub = _df_aba[['Nº NF', 'STATUS']].copy()
        _sub['Nº NF'] = pd.to_numeric(_sub['Nº NF'], errors='coerce')
        _sub = _sub.dropna(subset=['Nº NF'])
        for _, _r in _sub.iterrows():
            _status = str(_r['STATUS']).strip().upper() if pd.notna(_r['STATUS']) else ''
            if _status:
                _status_por_nf[int(_r['Nº NF'])] = _status
                if _data_aba:
                    _data_entrega_por_nf[int(_r['Nº NF'])] = _data_aba

print(f"Status de logística: {len(_status_por_nf)} NF(s) mapeada(s) (últimos {_meses_recentes.__defaults__[0]} meses)")

# ── "Em rota" hoje (mesma lógica do entregas.py) ───────────────────────────
# Um pedido está "em rota" quando a NF aparece na aba de HOJE da planilha
# "Controle de Notas" com a coluna ROTA preenchida (rota = viagem do dia,
# não o desfecho da entrega — isso é o STATUS, já coberto acima). Só cobre
# NFs da logística RJ (CRC), mesma limitação do status_log.
_hoje_d       = date.today()
_hoje_str     = _hoje_d.strftime('%d.%m')
_hoje_str_hif = _hoje_d.strftime('%d-%m')
_rota_hoje_por_nf: dict = {}
try:
    _mm_hoje = f"{_hoje_d.month:02d}"
    _upper_hoje = _MESES_PT_STATUS[_mm_hoje]
    _caminho_hoje = _bpd.com_fallback(
        lambda: _bpd.caminho_controle_notas(_mm_hoje, _upper_hoje, _hoje_d.year),
        _caminho_controle_notas_local(_hoje_d.year, _mm_hoje),
    )
    _abas_hoje = pd.read_excel(_caminho_hoje, sheet_name=None)
    _aba_hoje_nome = next((k for k in _abas_hoje if str(k) in (_hoje_str, _hoje_str_hif)), None)
    _df_hoje = _abas_hoje[_aba_hoje_nome].copy() if _aba_hoje_nome else pd.DataFrame()
    if not _df_hoje.empty and 'Nº NF' in _df_hoje.columns:
        _df_hoje = _df_hoje[_df_hoje['Nº NF'].notna()].copy()
        _df_hoje['NF_NUM'] = pd.to_numeric(_df_hoje['Nº NF'], errors='coerce')
        _df_hoje = _df_hoje.dropna(subset=['NF_NUM']).drop_duplicates('NF_NUM')
        for _, _r in _df_hoje.iterrows():
            _rota_val = str(_r.get('ROTA', '') or '').strip()
            if _rota_val:
                _rota_hoje_por_nf[int(_r['NF_NUM'])] = {
                    'rota':  _rota_val,
                    'placa': str(_r.get('PLACA', '') or '').strip(),
                }
    print(f"Em rota hoje ({_hoje_str}): {len(_rota_hoje_por_nf)} NF(s) | aba: {_aba_hoje_nome or 'não encontrada'}")
except Exception as e:
    print(f"[AVISO] Rota de hoje indisponível ({str(e)[:80]}) — 'em rota' fica sem dado")


def _rota_info(numnota):
    try:
        return _rota_hoje_por_nf.get(int(float(numnota)))
    except (TypeError, ValueError):
        return None

# ── Status de entrega SP (Canhoto Digital / ComprovaFácil) ────────────────────
# Cache gerado por canhoto_digital.py (scraping, roda separado — ver esse
# arquivo). Cobre só pedidos do sistema SPON; outras bases ficam sem status
# aqui mesmo (RJ já vem de _status_por_nf acima).

_RE_ENTREGUE_EM = re.compile(r'Entregue em:\s*(\d{1,2})/(\d{1,2})/(\d{2,4})', re.IGNORECASE)

_canhoto_path = Path(__file__).parent / 'canhoto_status.json'
_canhoto_por_nf: dict = {}
_canhoto_data_entrega_por_nf: dict = {}
if _canhoto_path.exists():
    try:
        _canhoto_raw = json.loads(_canhoto_path.read_text(encoding='utf-8'))
        _canhoto_por_nf = {
            nf: (info.get('sub_status') or info.get('status') or '')
            for nf, info in _canhoto_raw.items()
        }
        # 'info' tem texto livre tipo "Entregue em: 15/07/2026 às 16:14".
        for _nf, _info in _canhoto_raw.items():
            _m = _RE_ENTREGUE_EM.search(_info.get('info') or '')
            if _m:
                _dd, _mm, _yy = int(_m.group(1)), int(_m.group(2)), _m.group(3)
                _yy = int(_yy) if len(_yy) == 4 else 2000 + int(_yy)
                try:
                    _canhoto_data_entrega_por_nf[_nf] = date(_yy, _mm, _dd).strftime('%Y-%m-%d')
                except ValueError:
                    pass
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


def _data_entrega(numnota, sistema=''):
    """Data real de entrega (dd/mm/aaaa -> YYYY-MM-DD), ou '' se não achou —
    RJ vem da aba (dia da rota) em Controle de Notas, SP do texto 'Entregue
    em:' do Canhoto Digital."""
    if sistema == 'SPON':
        return _canhoto_data_entrega_por_nf.get(_nf_clean(numnota), '')
    try:
        return _data_entrega_por_nf.get(int(float(numnota)), '')
    except (ValueError, TypeError):
        return ''


# ── Tabela de preços RJ — pro motivo "desconto acima do permitido" ────────────
# Quando MOTIVOPOSICAO é "Item com desconto acima do permitido :CODPROD", o
# Winthor bloqueou o pedido — mostra o PVENDA digitado ao lado do Preço ON /
# Preço Promocional (TABELA DE PREÇO RJ.xlsx, mesma linha do produto,
# "COD CRC") junto do motivo. Só cobre RJ (a planilha só vale por lá, mesma
# limitação de conferencia_preco.py) — outros estados continuam vendo o
# motivo normal, sem os preços.
#
# NÃO cruza com PREÇO PROMO.xlsx ("COD PROMO"): testado com o pedido
# 412001460 (produto 4634, Red Bull Summer) e "COD PROMO" 4634 é um código
# de KIT/campanha completamente diferente por coincidência numérica — deu
# R$ 7,19 no lugar do preço real do produto (R$ 172,56/203,76), quando na
# verdade o digitado batia exatamente com o Preço Promocional aprovado.
# Confirmado com o usuário em 2026-08-05: só usar Tabela ON + Promocional,
# que são a mesma linha/produto (sem risco de colisão entre catálogos).
_precos_rj: dict = {}
try:
    _tab_on = pd.read_excel(
        _bpd.com_fallback(
            _bpd.caminho_tabela_preco_rj,
            r"G:\Drives compartilhados\EQUIPE DE VENDAS RJ\TABELA DE PREÇO RJ.xlsx",
        ),
        sheet_name='TABELA', skiprows=5, dtype=str,
    )
    _tab_on.columns = _tab_on.columns.str.strip()
    _tab_on['PREÇO'] = pd.to_numeric(_tab_on['PREÇO'].str.replace(',', '.'), errors='coerce').round(2)
    _tab_on['PREÇO PROMOCIONAL'] = pd.to_numeric(_tab_on['PREÇO PROMOCIONAL'].str.replace(',', '.'), errors='coerce').round(2)
    _tab_on['COD CRC'] = _tab_on['COD CRC'].astype(str).str.strip()

    for _, _r in _tab_on.iterrows():
        _cod = _r['COD CRC']
        if _cod and _cod != 'nan':
            _precos_rj[_cod] = {
                'preco_on':          _r['PREÇO'] if pd.notna(_r['PREÇO']) else None,
                'preco_promocional': _r['PREÇO PROMOCIONAL'] if pd.notna(_r['PREÇO PROMOCIONAL']) else None,
            }

    print(f"Tabela de preços RJ: {len(_precos_rj)} produto(s) mapeado(s) (Tabela ON)")
except Exception as e:
    print(f"[AVISO] Tabela de preços RJ indisponível ({str(e)[:100]}) — motivo de bloqueio fica sem preço de tabela")

# "TABELA CASTAS" — produtos (majoritariamente vinhos) que só existem nessa
# aba, fora da "TABELA" principal (ex.: COD CRC 4368, ARESTI ESTATE
# SELECTION CABERNET SAUVIGNON). Mesma coluna COD CRC da aba "TABELA" (não
# é um catálogo de código diferente tipo COD PROMO — ver aviso acima) — só
# preenche o que falta, nunca sobrescreve preço já mapeado pela "TABELA".
try:
    _tab_castas = pd.read_excel(
        _bpd.com_fallback(
            _bpd.caminho_tabela_preco_rj,
            r"G:\Drives compartilhados\EQUIPE DE VENDAS RJ\TABELA DE PREÇO RJ.xlsx",
        ),
        sheet_name='TABELA CASTAS', skiprows=5, dtype=str,
    )
    _tab_castas.columns = _tab_castas.columns.str.strip()
    _tab_castas['PREÇO'] = pd.to_numeric(_tab_castas['PREÇO'].str.replace(',', '.'), errors='coerce').round(2)
    _tab_castas['PREÇO PROMOCIONAL'] = pd.to_numeric(_tab_castas['PREÇO PROMOCIONAL'].str.replace(',', '.'), errors='coerce').round(2)
    _tab_castas['COD CRC'] = _tab_castas['COD CRC'].astype(str).str.strip()
    _novos = 0
    for _, _r in _tab_castas.iterrows():
        _cod = _r['COD CRC']
        if _cod and _cod != 'nan' and _cod not in _precos_rj:
            _precos_rj[_cod] = {
                'preco_on':          _r['PREÇO'] if pd.notna(_r['PREÇO']) else None,
                'preco_promocional': _r['PREÇO PROMOCIONAL'] if pd.notna(_r['PREÇO PROMOCIONAL']) else None,
            }
            _novos += 1
    print(f"Tabela CASTAS: +{_novos} produto(s) adicionados ({len(_precos_rj)} no total)")
except Exception as e:
    print(f"[AVISO] Aba TABELA CASTAS indisponível ({str(e)[:100]}) — produtos só cadastrados lá ficam sem preço de tabela")

# "TABELA OFF TRADE RJ - CRC" (mensal) — fallback pro que não está na TABELA
# DE PREÇO RJ.xlsx/TABELA CASTAS. Só preenche o que falta, mesma regra das
# duas acima. Pedido explícito do usuário em 2026-08-10.
_novos_fallback = 0
for _cod, _info in _bpd.carregar_precos_off_trade_fallback().items():
    if _cod not in _precos_rj:
        _precos_rj[_cod] = _info
        _novos_fallback += 1
print(f"Tabela OFF TRADE RJ - CRC: +{_novos_fallback} produto(s) adicionados ({len(_precos_rj)} no total)")


def _preco_tabela(codprod: str):
    """Menor entre Preço ON e Preço Promocional pro produto (mesma linha da
    TABELA DE PREÇO RJ.xlsx — sem cruzar com PREÇO PROMO.xlsx, ver aviso
    acima)."""
    info = _precos_rj.get(str(codprod))
    if not info:
        return None
    candidatos = [info[k] for k in ('preco_on', 'preco_promocional') if info.get(k) is not None]
    return round(min(candidatos), 2) if candidatos else None


_RE_MOTIVO_DESCONTO = re.compile(r'desconto acima do permitido\s*:\s*(\d+)', re.IGNORECASE)


def _preco_motivo_bloqueio(item):
    """Se o motivo for bloqueio por desconto excessivo, acha o código e o
    nome do produto citado, o preço digitado (PVENDA do item) e o piso de
    tabela pra mostrar junto. Retorna None se não for esse tipo de motivo,
    o item não for encontrado ou não tiver preço de tabela carregado (fora
    do RJ, produto não cadastrado nas planilhas etc.)."""
    m = _RE_MOTIVO_DESCONTO.search(item.get('motivo', ''))
    if not m:
        return None
    codprod = m.group(1)
    it = next((i for i in item['itens'] if i['codprod'] == codprod), None)
    if not it or it.get('pvenda') is None:
        return None
    pt = _preco_tabela(codprod)
    if pt is None:
        return None
    return codprod, it['desc'], it['pvenda'], pt


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

_numpeds_pagos_str = {str(int(n)) for n in _numpeds_pagos}
# Bonificação costuma vir com a "parcela" quitada no mesmo dia (não é
# cobrança de verdade, o sistema já lança paga) — a regra de "saiu de
# Faturados quando todas as parcelas foram pagas" existe pra tirar pedido
# realmente quitado da tela, mas pra bonificação isso tirava o pedido antes
# da entrega/logística ser acompanhada (pedido do usuário em 2026-08-12,
# ver NF 7302/450000509: VLBONIFIC=517.68, parcela paga no mesmo dia).
_numpeds_bonificacao = set(
    tabela_pedidos.loc[tabela_pedidos['VLBONIFIC_NUM'] > 0, 'NUMPED'].astype(str)
)

_cancelados = tabela_pedidos[tabela_pedidos['STATUS'] == 'CANCELADA']
_faturados  = tabela_pedidos[
    tabela_pedidos['NUMNOTA_NUM'].notna()
    & (tabela_pedidos['STATUS'] != 'CANCELADA')
    & (
        ~tabela_pedidos['NUMPED'].astype(str).isin(_numpeds_pagos_str)
        | tabela_pedidos['NUMPED'].astype(str).isin(_numpeds_bonificacao)
    )
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


def _motivo_corte(sistema, numped, codprod_num):
    """Motivo real do corte (PEDIDOS_CANCELADOS.MOTIVO), ou '' se não achou
    (produto sem corte registrado nessa view, ou base sem a view)."""
    try:
        chave = (sistema, int(numped), int(codprod_num))
    except (TypeError, ValueError):
        return ''
    return _motivo_corte_lookup.get(chave, '')


def _valor_cortado_pedido(sistema, numped):
    """Valor real cortado do pedido inteiro (soma de PEDIDOS_CANCELADOS.SUBTOT
    por NUMPED), ou None se não achou (pedido sem registro nessa view, ou
    base sem a view) — nesse caso cai pra soma do valor_cortado estimado por
    item (qtd_cortada_total * preço), calculado em _agrupar."""
    try:
        chave = (sistema, int(numped))
    except (TypeError, ValueError):
        return None
    return _valor_corte_pedido_lookup.get(chave)


def _motivos_corte_pedido(sistema, numped):
    """Motivo(s) real(is) do pedido inteiro (PEDIDOS_CANCELADOS.MOTIVO,
    todos os distintos daquele NUMPED, sem tentar casar produto — mesma
    razão de _valor_cortado_pedido), ou '' se não achou nenhum."""
    try:
        chave = (sistema, int(numped))
    except (TypeError, ValueError):
        return ''
    motivos = _motivo_corte_pedido_lookup.get(chave)
    return '; '.join(motivos) if motivos else ''


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
    _pvenda = pd.to_numeric(row.get('PVENDA'), errors='coerce')
    # Preço unitário: TOTAL/QT do que foi faturado é mais confiável (reflete
    # desconto/negociação real da nota); só cai pro PVENDA digitado quando o
    # item inteiro foi cortado (QT=0, sem nota pra calcular a partir dela).
    _preco_unit = (float(row['TOTAL']) / qt) if qt > 0 else (
        float(_pvenda) if pd.notna(_pvenda) else None
    )
    _valor_cortado = round(qtd_cortada_total * _preco_unit, 2) if _preco_unit is not None else None
    return {
        'desc':              _s(row['DESCRICAO']),
        'industria':         _s(row.get('FANTASIA_FORNEC')) or _s(row.get('FORNECEDOR')),
        'qt':                qt,
        'val':               round(float(row['TOTAL']), 2),
        'qtfalta':           qtfalta,
        'qtcortada':         qtcortada,
        'qtd_cortada_total': qtd_cortada_total,
        'qt_original':       qt + qtd_cortada_total,
        'valor_cortado':     _valor_cortado,
        'motivo_corte':      _motivo_corte(sistema, numped, row['CODPROD_NUM']),
        'cortado':           qtd_cortada_total > 0,
        'codprod':   _int_s(row['CODPROD_NUM']),
        'codfilial': _int_s(row['CODFILIAL_NUM']),
        'pvenda':    round(float(_pvenda), 2) if pd.notna(_pvenda) else None,
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
            'cidade':     _s(r0['CIDADE']),
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
        # Valor real cortado do pedido inteiro (soma de PEDIDOS_CANCELADOS.SUBTOT
        # por NUMPED) tem prioridade sobre a soma dos valor_cortado estimados
        # por item — ver docstring de _valor_cortado_pedido. Nome diferente de
        # 'valor_cortado' (que já existe por item) pra não colidir.
        _valor_real_pedido = _valor_cortado_pedido(sistema, numped)
        item['valor_cortado_total'] = (
            round(_valor_real_pedido, 2) if _valor_real_pedido is not None
            else round(sum(it['valor_cortado'] or 0 for it in item['itens']), 2)
        )
        # Motivo real do pedido inteiro (PEDIDOS_CANCELADOS, por NUMPED — não
        # por produto, mesma razão de valor_cortado_total acima) tem
        # prioridade sobre MOTIVOPOSICAO/FUNC_CANCEL (nível pedido, mais
        # genérico, vindo de PBI_PCPEDI).
        _motivos_reais = _motivos_corte_pedido(sistema, numped)
        if _motivos_reais:
            item['motivo'] = _motivos_reais
        if com_status_log:
            _status = _status_log(nf, sistema) if nf else ''
            _rota = _rota_info(nf) if nf else None
            item['em_rota'] = _rota is not None
            item['rota']    = _rota['rota']  if _rota else ''
            item['placa']   = _rota['placa'] if _rota else ''
            # Controle de Notas só preenche STATUS (ENTREGUE/RETORNO/etc.)
            # depois do desfecho da entrega — enquanto a NF só tem ROTA
            # atribuída (sem desfecho ainda), mostra "EM ROTA" na coluna
            # Status Logística em vez de deixar em branco.
            item['status_log'] = _status if _status else ('EM ROTA' if _rota is not None else '')
            item['data_entrega'] = _data_entrega(nf, sistema) if nf else ''
        _preco_motivo = _preco_motivo_bloqueio(item)
        if _preco_motivo:
            (item['motivo_codprod'], item['motivo_produto'],
             item['motivo_preco_digitado'], item['motivo_preco_tabela']) = _preco_motivo
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
                'cidade':     p['cidade'],
                'supervisor': p['supervisor'],
                'gerente':    p['gerente'],
                'status_ped': p['status_ped'],
                'posicao':    p['posicao'],
                'motivo':     it['motivo_corte'] or p['motivo'],
                'desc':       it['desc'],
                'industria':  it['industria'],
                'codprod':    it['codprod'],
                'codfilial':  it['codfilial'],
                'qt':                it['qt'],
                'qtfalta':           it['qtfalta'],
                'qtcortada':         it['qtcortada'],
                'qtd_cortada_total': it['qtd_cortada_total'],
                'qt_original':       it['qt_original'],
                'valor_cortado': it['valor_cortado'],
                'val':        it['val'],
                'total':      it['val'],
            })
    return out


_faturados_agrupados  = _agrupar(_faturados, com_status_log=True)
_cancelados_agrupados = _agrupar(_cancelados)

payload = {
    'atualizado_em':        datetime.now().strftime('%d/%m/%Y %H:%M'),
    'periodo_dias':         DIAS_JANELA,
    'fontes_indisponiveis': _fontes_indisponiveis,
    'pedidos_feitos':       _agrupar(_feitos),
    'faturados':            _faturados_agrupados,
    'cancelados':           _cancelados_agrupados,
    # Corte parcial (entregou parte) vem de Faturados; corte total (pedido
    # inteiro cortado) vem de Cortados/Cancelados — os dois têm que entrar
    # aqui, senão a aba "Produtos Cortados" só mostra metade dos cortes.
    'produtos_cortados':    sorted(
        _extrair_cortados(_faturados_agrupados) + _extrair_cortados(_cancelados_agrupados),
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
