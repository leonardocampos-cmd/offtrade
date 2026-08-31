"""
Gera pedidos_bloqueados_data.js — página com todos os pedidos bloqueados
(PCPEDC.POSICAO IN ('B','P','M') — Bloqueado, Pendente e Montado) de todas
as bases (CRC, thekings, CASTAS, GARRIDO, SPON, MGON) — sem restringir a
vendedor RJ como o alerta de WhatsApp faz, aqui é visão completa pra gestão.

'M' = "Montado" no Winthor (nome oficial do status, confirmado pelo usuário
em 2026-08-26 — rótulo mostrado na página é literalmente esse, não
"Bloqueado (alçada)" como chamávamos antes), mas funcionalmente é bloqueio
por alçada de crédito/desconto: MOTIVOPOSICAO de pedidos 'M' é idêntico ao
de 'B' ("Item com desconto acima do permitido", "Valor do pedido menor que
mínimo" etc.) e só sai dessa posição via liberação (CODFUNCLIBERA/DTLIBERA)
— não é sobre "pedido montado/separado no depósito". 'M' ENTRA aqui,
diferente de alerta_pedidos_bloqueados.py/metas.html (que excluem por
pedido explícito do usuário em 2026-08-10) — es.html/mg.html/sp.html sempre
contaram 'M' como bloqueado (_POSICOES_PROBLEMA_PED lá inclui 'Bloqueado
(alçada)', nome antigo — não renomeado lá pra não mexer em outra página sem
pedido), e o usuário confirmou em 2026-08-25 que essa página deve bater com
a contagem de lá, não com a do alerta de WhatsApp (61 dos 69 pedidos
"problema" do RJ sozinho eram 'M' — excluir deixava a página visivelmente
incompleta).

PC.DTLIBERA IS NULL AND PC.DTCANCEL IS NULL (pedido do usuário em
2026-08-26, "está aparecendo muitos pedidos já liberados"): POSICAO NÃO
muda quando um pedido 'M' é liberado por alçada — fica 'M' pra sempre,
só DTLIBERA/CODFUNCLIBERA são preenchidos no momento da liberação.
Confirmado no CRC: 92 dos 105 pedidos 'M' (87%!) já tinham DTLIBERA
preenchido — sem esse filtro a página mostrava a maioria como "ainda
bloqueado" quando já tinha sido resolvido, às vezes dias atrás. 'B'
(bloqueado de verdade) nunca tem DTLIBERA preenchido no CRC (crédito/
cliente não passa por esse fluxo de liberação por alçada). Com esse
filtro, os campos liberado_por/liberado_em/cancelado_por/cancelado_em
ficam sempre vazios pra pedidos que aparecem aqui (só populam DEPOIS
da liberação/cancelamento, que agora tira o pedido da lista) — mantidos
mesmo assim pro caso de um pedido ser liberado no intervalo entre a
consulta e o próximo cron (5 min), quando ainda pode aparecer com esses
campos já preenchidos por uma execução anterior em cache no navegador.

Preço de tabela só existe pra RJ (TABELA DE PREÇO RJ.xlsx só vale por lá,
mesma limitação de pedidos.py/conferencia_preco.py) — outros estados ficam
com preco_tabela=None, mostrado como "—" na página.

bonificacao = PCPEDC.VLBONIFIC > 0 (mesmo campo/lógica que pedidos.py já usa
pra detectar bonificação) — pedido do usuário em 2026-08-26 pra destacar na
página. É um flag do PEDIDO (não por item, VLBONIFIC não varia por CODPROD
dentro do mesmo NUMPED).

Custo e margem (pedido do usuário em 2026-08-26): custo vem de
{schema}.ROTINA_105.CUSTOULTENT_2 — "custo última entrada" já normalizado
por unidade (mesma view/mesmo padrão "_2 = valor unitário de fato" que
exportacao_estoque.py documenta pra PVENDA_2; CUSTOULTENT "cru" vem
pré-multiplicado pela quantidade da última entrada, testado e descartado).
Query por CODPROD IN (...) direto no schema de cada pedido — ao contrário
da 1ª versão (planilha Profit RJ, só RJ e sem distinguir sistema, gerava
"custo" de produto errado por coincidência de código em thekings/GARRIDO/
SPON/MGON), aqui não há risco de cross-contaminação entre sistemas.
ROTINA_105 pode ter uma linha por CODFILIAL — fica com a de DTULTENT mais
recente por CODPROD. CASTAS não tem ROTINA_105 (mesma ausência documentada
em exportacao_estoque.py) — pedidos desse sistema ficam com custo=None
("—").

preco_venda/preco_tabela/diferenca/custo são o TOTAL da linha (unitário ×
PED.QT), não o preço unitário — pedido do usuário em 2026-08-26 ("precisam
ser o total"). margem continua sendo a razão (venda-custo)/venda, que não
muda multiplicando os dois lados por QT — não faz sentido "total" pra uma
%. 'qt' fica exposto em cada item pra dar contexto de como o total foi
calculado.

No nível do pedido, esses 5 campos são a SOMA de todos os itens (grand
total do pedido, não de um item representativo) — confirmado pelo usuário
em 2026-08-26 com o pedido 439000359/CRC (2 itens: R$155,80 + R$191,80 =
R$347,60). 'margem' do pedido é recalculada em cima dos totais agregados
((soma_venda - soma_custo) / soma_venda), não a média das margens dos
itens. Cada campo soma só os itens que têm valor — vira None só quando
NENHUM item do pedido tem esse campo (preco_venda praticamente sempre
presente, vem direto do Oracle; preco_tabela/custo dependem de lookup
externo e podem faltar por item, aí o pedido soma parcial).

Liberado por / Cancelado por (pedido do usuário em 2026-08-25): resolvem
PCPEDC.CODFUNCLIBERA/CODFUNCCANCEL contra PCEMPR.MATRICULA (funcionário
interno) — NÃO é o mesmo espaço de código de PCUSUARI.CODUSUR (vendedor),
ver memória project_pcempr_matricula_funcionario. Como só populam quando o
pedido JÁ foi liberado/cancelado, ficam vazios pra maioria dos pedidos
ainda bloqueados (é o esperado — servem sobretudo pra auditoria de casos
como alçada resolvida rápido, ou liberação parcial num pedido multi-item).

Roda SÓ na VPS, num cron próprio de 5 em 5 min (pedido do usuário em
2026-08-25) — fora do main.py de propósito (esse é horário). A VPS agora
alcança CASTAS via VPN própria (deixou de ser rede-local-only, ver
[[project_banco_castas_rede_local]] — memória desatualizada depois dessa
mudança), então não precisa do fallback local que o exportacao_meta.py usa.
Publica direto em /opt/offtrade-static (mesmo padrão de
exportacao_meta.py::_publicar_static) — deploy_static_vps.py IGNORA esse
arquivo de propósito (EXCLUDE_JS) pra não sobrescrever o dado fresco da VPS
com uma cópia local que nem deveria existir.
"""
import json
import os
from datetime import datetime
from pathlib import Path

import pandas as pd

from meta import engine, engine_theking, engine_castas, engine_garrido, engine_spon, engine_mgon, carregar_paralelo
import baixar_planilhas_drive as _bpd

DIAS_JANELA = 90

# Pedido do usuário em 2026-08-31: pra vendedor RJ, considerar só esses 3
# RCAs (CODUSUR) — não restringe os demais estados/bases, que continuam
# mostrando todos os vendedores OFF TRADE normalmente.
_RCAS_RJ_RESTRITOS = (159, 144, 155)

_SPON_EXTRA = ['%W.S%']

_SOURCES = [
    ("CRC",      engine,         None),
    ("thekings", engine_theking, None),
    ("CASTAS",   engine_castas,  None),
    ("GARRIDO",  engine_garrido, None),
    ("SPON",     engine_spon,    _SPON_EXTRA),
    ("MGON",     engine_mgon,    _SPON_EXTRA),
]


def _nome_filter(extra_nomes=None, alias='PED'):
    base = f"{alias}.NOME LIKE '%OFF TRADE%'"
    if extra_nomes:
        extras = " OR ".join(f"{alias}.NOME LIKE '{p}'" for p in extra_nomes)
        return f"({base} OR {extras})"
    return base


def _query_bloqueados(schema, extra_nomes=None):
    nome_f = _nome_filter(extra_nomes)
    rcas = ",".join(str(r) for r in _RCAS_RJ_RESTRITOS)
    return f"""
        SELECT PED.NUMPED, PED.DATA, PED.CLIENTE, PED.CODCLI, PED.CODPROD, PED.DESCRICAO, PED.PVENDA, PED.QT,
               PC.POSICAO, PC.MOTIVOPOSICAO, PC.VLBONIFIC,
               PC.DTFIMDIGITACAOPEDIDO, PC.HORA, PC.MINUTO,
               PC.CODFUNCLIBERA, PC.DTLIBERA, PC.CODFUNCCANCEL, PC.DTCANCEL,
               U.NOME AS VENDEDOR, U.ESTADO
        FROM {schema}.PBI_PCPEDI PED
        JOIN {schema}.PCUSUARI U ON U.CODUSUR = PED.CODUSUR
        JOIN {schema}.PCPEDC   PC ON PC.NUMPED = PED.NUMPED
        WHERE {nome_f}
          AND PC.POSICAO IN ('B', 'P', 'M')
          AND PC.DTLIBERA IS NULL
          AND PC.DTCANCEL IS NULL
          AND PED.DATA >= SYSDATE - {DIAS_JANELA}
          AND (U.ESTADO != 'RJ' OR U.CODUSUR IN ({rcas}))
    """


def _query_pcempr(schema, matriculas):
    lista = ",".join(str(m) for m in matriculas)
    return f"""
        SELECT MATRICULA, NOME, NOME_GUERRA
        FROM {schema}.PCEMPR
        WHERE MATRICULA IN ({lista})
    """


# ── Tabela de preços RJ (mesma fonte/lógica de pedidos.py/alerta_pedidos_bloqueados.py) ──

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
    print(f"Tabela de preços RJ: {len(_precos_rj)} produto(s) mapeado(s)")
except Exception as e:
    print(f"[AVISO] Tabela de preços RJ indisponível ({str(e)[:100]}) — preço de tabela fica vazio")

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

_novos_fallback = 0
for _cod, _info in _bpd.carregar_precos_off_trade_fallback().items():
    if _cod not in _precos_rj:
        _precos_rj[_cod] = _info
        _novos_fallback += 1
print(f"Tabela OFF TRADE RJ - CRC: +{_novos_fallback} produto(s) adicionados ({len(_precos_rj)} no total)")


# ── Bases de clientes OTD/OTI (mesma lógica/planilhas de conferencia_preco.py) ──
# Observação no pedido bloqueado quando o cliente está numa dessas bases —
# pedido do usuário em 2026-08-31, só informativo, não afeta filtro/preço.

def _carregar_codclis_base(caminho_fn, caminho_fallback, sheet_name, nome_base):
    try:
        df_base = pd.read_excel(
            _bpd.com_fallback(caminho_fn, caminho_fallback),
            sheet_name=sheet_name, dtype=str,
        )
        df_base.columns = df_base.columns.str.strip()
        codclis = set(df_base['CÓDIGO'].dropna().str.strip())
        print(f"Base {nome_base}: {len(codclis)} cliente(s)")
        return codclis
    except Exception as e:
        print(f"[AVISO] Base {nome_base} indisponível ({str(e)[:100]}) — observação {nome_base} não aplicada")
        return set()


_codclis_otd = _carregar_codclis_base(
    _bpd.caminho_base_otd,
    r"G:\Drives compartilhados\Off Trade\Campanhas e Metas\BASE OTD.xlsx",
    "BASE CENSUS OTD Q1_FY27", "OTD",
)
_codclis_oti = _carregar_codclis_base(
    _bpd.caminho_base_oti,
    r"G:\Drives compartilhados\Off Trade\Campanhas e Metas\BASE OTI JUNHO.xlsx",
    "Planilha1", "OTI",
)


def _observacao_base_cliente(codcli):
    bases = [nome for nome, codclis in (('OTD', _codclis_otd), ('OTI', _codclis_oti)) if codcli in codclis]
    if not bases:
        return ''
    return f"Cliente na base {' e '.join(bases)}"


def _preco_tabela(codprod):
    info = _precos_rj.get(str(codprod))
    if not info:
        return None
    candidatos = [info[k] for k in ('preco_on', 'preco_promocional') if info.get(k) is not None]
    return round(min(candidatos), 2) if candidatos else None


def _diferenca(preco_venda, preco_tabela):
    """Tabela - digitado: positivo = vendeu abaixo da tabela (desconto),
    negativo = vendeu acima. None se algum dos dois preços não existir."""
    if preco_venda is None or preco_tabela is None:
        return None
    return round(preco_tabela - preco_venda, 2)


_SCHEMAS_SEM_ROTINA_105 = {'CASTAS'}  # mesma ausência documentada em exportacao_estoque.py


def _query_custo_ultima_entrada(schema, codprods):
    """CUSTOULTENT_2 é o custo unitário de fato (padrão "_2" da view, ver
    docstring do módulo) — pode haver uma linha por CODFILIAL, fica com a
    de DTULTENT mais recente por CODPROD."""
    lista = ",".join(str(c) for c in codprods)
    return f"""
        SELECT CODPROD, CUSTOULTENT_2, DTULTENT FROM (
            SELECT CODPROD, CUSTOULTENT_2, DTULTENT,
                   ROW_NUMBER() OVER (PARTITION BY CODPROD ORDER BY DTULTENT DESC NULLS LAST) RN
            FROM {schema}.ROTINA_105
            WHERE CODPROD IN ({lista})
        ) WHERE RN = 1
    """


def _margem(preco_venda, custo):
    """Margem sobre o preço de venda: (venda - custo) / venda. None se
    faltar preço de venda ou custo, ou se preço de venda for zero."""
    if preco_venda is None or custo is None or not preco_venda:
        return None
    return round((preco_venda - custo) / preco_venda, 4)


_POSICAO_LABEL = {'B': 'Bloqueado', 'P': 'Pendente', 'M': 'Montado'}


def _s(v):
    return '' if pd.isna(v) else str(v).strip()


def _data_hora(row):
    """Timestamp de quando o pedido foi feito. PED.DATA (PBI_PCPEDI) só tem
    a data, sem hora (sempre 00:00) — PCPEDC.DTFIMDIGITACAOPEDIDO é o
    timestamp completo de quando a digitação terminou (mais preciso, cobre
    quem demorou pra fechar o pedido); quando vem nulo, cai pra
    PCPEDC.DATA + HORA/MINUTO (colunas inteiras separadas); sem nenhum dos
    dois, cai pra só a data (sem hora)."""
    if pd.notna(row.get('DTFIM_DT')):
        return row['DTFIM_DT']
    base = row.get('DATA_DT')
    if pd.isna(base):
        return None
    hora, minuto = row.get('HORA_NUM'), row.get('MINUTO_NUM')
    if pd.notna(hora) and pd.notna(minuto):
        try:
            return base.replace(hour=int(hora), minute=int(minuto))
        except ValueError:
            return base
    return base


def montar_pedidos_bloqueados():
    fontes_indisponiveis = []
    chamadas = [
        (_query_bloqueados(schema, extra), eng, f"bloqueados_{schema}")
        for schema, eng, extra in _SOURCES
    ]
    partes = []
    for (schema, eng, extra), res in zip(_SOURCES, carregar_paralelo(chamadas)):
        if isinstance(res, Exception):
            print(f"[AVISO] bloqueados_{schema} falhou ({str(res)[:100]}) — ignorado")
            fontes_indisponiveis.append(schema)
        else:
            res.columns = res.columns.str.upper()
            res['SISTEMA'] = schema
            partes.append(res)

    if not partes:
        return [], fontes_indisponiveis

    df = pd.concat(partes, ignore_index=True)
    df['PVENDA'] = pd.to_numeric(df['PVENDA'], errors='coerce')
    df['QT'] = pd.to_numeric(df['QT'], errors='coerce')
    df['DATA_DT'] = pd.to_datetime(df['DATA'], errors='coerce')
    df['DTFIM_DT'] = pd.to_datetime(df['DTFIMDIGITACAOPEDIDO'], errors='coerce')
    df['HORA_NUM'] = pd.to_numeric(df['HORA'], errors='coerce')
    df['MINUTO_NUM'] = pd.to_numeric(df['MINUTO'], errors='coerce')
    df['DTLIBERA_DT'] = pd.to_datetime(df['DTLIBERA'], errors='coerce')
    df['DTCANCEL_DT'] = pd.to_datetime(df['DTCANCEL'], errors='coerce')
    df['CODFUNCLIBERA_NUM'] = pd.to_numeric(df['CODFUNCLIBERA'], errors='coerce')
    df['CODFUNCCANCEL_NUM'] = pd.to_numeric(df['CODFUNCCANCEL'], errors='coerce')
    df['VLBONIFIC_NUM'] = pd.to_numeric(df['VLBONIFIC'], errors='coerce').fillna(0)
    df['CODCLI'] = df['CODCLI'].astype(str).str.strip()

    _engine_por_schema = {s: e for s, e, _ in _SOURCES}

    # Custo por produto: ROTINA_105.CUSTOULTENT_2, direto no schema de cada
    # pedido (evita a contaminação entre sistemas de uma 1ª versão que usava
    # a planilha Profit RJ pra todo mundo — ver docstring do módulo).
    _chamadas_custo = []
    for schema in df['SISTEMA'].unique():
        if schema in _SCHEMAS_SEM_ROTINA_105:
            continue
        codprods = set()
        for v in df.loc[df['SISTEMA'] == schema, 'CODPROD'].dropna():
            try:
                codprods.add(int(str(v).strip()))
            except (TypeError, ValueError):
                continue
        if codprods:
            _chamadas_custo.append((_query_custo_ultima_entrada(schema, codprods), _engine_por_schema[schema], f"custo_{schema}"))

    _custos = {}  # (schema, codprod) -> custo unitário (última entrada)
    if _chamadas_custo:
        for (query, eng, nome_tabela), res in zip(_chamadas_custo, carregar_paralelo(_chamadas_custo)):
            schema = nome_tabela.replace('custo_', '')
            if isinstance(res, Exception):
                print(f"[AVISO] {nome_tabela} falhou ({str(res)[:100]}) — custo/margem ficam vazios pra {schema}")
                continue
            res.columns = res.columns.str.upper()
            for _, r in res.iterrows():
                custo_v = r.get('CUSTOULTENT_2')
                if pd.isna(custo_v):
                    continue
                try:
                    codprod_key = str(int(r['CODPROD']))
                except (TypeError, ValueError):
                    continue
                _custos[(schema, codprod_key)] = round(float(custo_v), 2)

    # CODFUNCLIBERA/CODFUNCCANCEL são matrícula de funcionário (PCEMPR),
    # NÃO código de vendedor (PCUSUARI.CODUSUR) — mesmo espaço numérico,
    # cadastro diferente (ver memória project_pcempr_matricula_funcionario:
    # matrícula 218 resolvia errado como "outro vendedor" via PCUSUARI).
    _chamadas_pcempr = []
    for schema in df['SISTEMA'].unique():
        sub = df[df['SISTEMA'] == schema]
        codigos = set(sub['CODFUNCLIBERA_NUM'].dropna().astype(int)) | set(sub['CODFUNCCANCEL_NUM'].dropna().astype(int))
        if codigos:
            _chamadas_pcempr.append((_query_pcempr(schema, codigos), _engine_por_schema[schema], f"pcempr_{schema}"))

    _funcionario_lookup = {}  # (schema, matricula) -> nome
    if _chamadas_pcempr:
        for (query, eng, nome_tabela), res in zip(_chamadas_pcempr, carregar_paralelo(_chamadas_pcempr)):
            schema = nome_tabela.replace('pcempr_', '')
            if isinstance(res, Exception):
                print(f"[AVISO] {nome_tabela} falhou ({str(res)[:100]}) — 'liberado/cancelado por' fica só com o código dessa base")
                continue
            res.columns = res.columns.str.upper()
            for _, r in res.iterrows():
                try:
                    matricula = int(r['MATRICULA'])
                except (TypeError, ValueError):
                    continue
                nome_func = (r.get('NOME_GUERRA') or r.get('NOME') or '').strip()
                _funcionario_lookup[(schema, matricula)] = nome_func or str(matricula)

    def _nome_funcionario(schema, matricula_num):
        if pd.isna(matricula_num):
            return ''
        return _funcionario_lookup.get((schema, int(matricula_num)), str(int(matricula_num)))

    pedidos = []
    for (sistema, numped), grupo in df.groupby(['SISTEMA', 'NUMPED'], sort=False):
        primeira = grupo.iloc[0]
        motivo = _s(primeira['MOTIVOPOSICAO']) or '(motivo não informado)'

        itens = []
        for _, row in grupo.iterrows():
            codprod = _s(row['CODPROD'])
            qt = row['QT']
            _qt = float(qt) if pd.notna(qt) else None
            pvenda_unit = row['PVENDA']
            _pv_unit = round(float(pvenda_unit), 2) if pd.notna(pvenda_unit) else None
            _pt_unit = _preco_tabela(codprod)
            _cu_unit = _custos.get((sistema, codprod))
            _margem_v = _margem(_pv_unit, _cu_unit)  # razão — não muda multiplicando por QT

            # preco_venda/preco_tabela/custo abaixo já saem como TOTAL da
            # linha (unitário × QT) — pedido do usuário em 2026-08-26.
            _pv = round(_pv_unit * _qt, 2) if _pv_unit is not None and _qt is not None else None
            _pt = round(_pt_unit * _qt, 2) if _pt_unit is not None and _qt is not None else None
            _cu = round(_cu_unit * _qt, 2) if _cu_unit is not None and _qt is not None else None
            itens.append({
                'codprod':      codprod,
                'descricao':    _s(row['DESCRICAO']),
                'qt':           _qt,
                'preco_venda':  _pv,
                'preco_tabela': _pt,
                'diferenca':    _diferenca(_pv, _pt),
                'custo':        _cu,
                'margem':       _margem_v,
            })

        # Nível do pedido = soma de TODOS os itens (grand total), não só um
        # item representativo — pedido do usuário em 2026-08-26 (conferiu
        # 155,80 + 191,80 = 347,60 no pedido 439000359/CRC, 2 itens). Some só
        # o que tem valor; None só quando NENHUM item tem o campo (preco_venda
        # praticamente sempre presente — vem direto do Oracle; preco_tabela/
        # custo dependem de lookup externo e podem faltar por item).
        def _soma_ou_none(campo):
            valores = [it[campo] for it in itens if it.get(campo) is not None]
            return round(sum(valores), 2) if valores else None

        _qt_pedido = _soma_ou_none('qt')
        _pv_pedido = _soma_ou_none('preco_venda')
        _pt_pedido = _soma_ou_none('preco_tabela')
        _cu_pedido = _soma_ou_none('custo')
        _dif_pedido = _diferenca(_pv_pedido, _pt_pedido)
        _margem_pedido = _margem(_pv_pedido, _cu_pedido)

        data_dt = _data_hora(primeira)
        dtlibera_dt = primeira['DTLIBERA_DT']
        dtcancel_dt = primeira['DTCANCEL_DT']
        pedidos.append({
            'numped':        _s(numped),
            'sistema':       _s(sistema),
            'cliente':       _s(primeira['CLIENTE']),
            'vendedor':      _s(primeira['VENDEDOR']),
            'estado':        _s(primeira['ESTADO']),
            'data':          data_dt.strftime('%d/%m/%Y %H:%M') if pd.notna(data_dt) else '',
            'data_ord':      data_dt.strftime('%Y-%m-%d %H:%M:%S') if pd.notna(data_dt) else '',
            'posicao':       _POSICAO_LABEL.get(_s(primeira['POSICAO']).upper(), _s(primeira['POSICAO'])),
            'bonificacao':   bool(primeira['VLBONIFIC_NUM'] > 0),
            'observacao':    _observacao_base_cliente(_s(primeira['CODCLI'])),
            'motivo':        motivo,
            'qt':            _qt_pedido,
            'preco_venda':   _pv_pedido,
            'preco_tabela':  _pt_pedido,
            'diferenca':     _dif_pedido,
            'custo':         _cu_pedido,
            'margem':        _margem_pedido,
            'liberado_por':  _nome_funcionario(sistema, primeira['CODFUNCLIBERA_NUM']),
            'liberado_em':   dtlibera_dt.strftime('%d/%m/%Y %H:%M') if pd.notna(dtlibera_dt) else '',
            'cancelado_por': _nome_funcionario(sistema, primeira['CODFUNCCANCEL_NUM']),
            'cancelado_em':  dtcancel_dt.strftime('%d/%m/%Y %H:%M') if pd.notna(dtcancel_dt) else '',
            'itens':         itens,
        })

    pedidos.sort(key=lambda p: p['data_ord'], reverse=True)
    return pedidos, fontes_indisponiveis


def main():
    pedidos, fontes_indisponiveis = montar_pedidos_bloqueados()
    if fontes_indisponiveis:
        print(f"[AVISO] Fontes indisponíveis nesta execução: {fontes_indisponiveis}")

    payload = {
        'atualizado_em':        datetime.now().strftime('%d/%m/%Y %H:%M'),
        'periodo_dias':         DIAS_JANELA,
        'fontes_indisponiveis': fontes_indisponiveis,
        'pedidos':              pedidos,
    }

    out = Path(__file__).parent / 'pedidos_bloqueados_data.js'
    tmp = out.with_suffix('.js.tmp')
    with open(tmp, 'w', encoding='utf-8') as f:
        f.write(f"const PEDIDOS_BLOQUEADOS_DATA = {json.dumps(payload, ensure_ascii=False, indent=2)};\n")
    os.replace(tmp, out)
    print(f"OK - {len(pedidos)} pedido(s) bloqueado(s)/pendente(s) -> {out}")

    import subprocess
    repo_dir = str(Path(__file__).parent)
    try:
        subprocess.run(["git", "-C", repo_dir, "add", "pedidos_bloqueados_data.js"], check=True)
        subprocess.run(["git", "-C", repo_dir, "commit", "-m",
                        f"Atualiza pedidos_bloqueados_data.js - {datetime.now().strftime('%d/%m/%Y')}"])
        subprocess.run(["git", "-C", repo_dir, "push", "origin", "master"], check=True)
        print("OK pedidos_bloqueados_data.js enviado ao GitHub Pages.")
    except subprocess.CalledProcessError:
        print("[AVISO] git push falhou — ignorado, pipeline continua.")

    _publicar_static()


# ── Publica direto em /opt/offtrade-static (site) ─────────────────────────────
# Roda só na VPS (cron próprio de 5 em 5 min) — mesmo padrão de
# exportacao_meta.py::_publicar_static (rename atômico, shutil quando já está
# na própria VPS). Sem SFTP: diferente do meta.py, esse script nunca roda
# fora da VPS (ver docstring), não precisa do caminho remoto por rede.
def _publicar_static():
    if os.getenv("OFFTRADE_RUNTIME", "local") != "vps":
        return
    import shutil
    destino = "/opt/offtrade-static"
    origem = Path(__file__).parent / 'pedidos_bloqueados_data.js'
    if not origem.exists():
        return
    tmp = os.path.join(destino, ".pedidos_bloqueados_data.js.tmp_publish")
    shutil.copy(origem, tmp)
    os.replace(tmp, os.path.join(destino, "pedidos_bloqueados_data.js"))
    print(f"OK - pedidos_bloqueados_data.js copiado para {destino}")


if __name__ == "__main__":
    main()
