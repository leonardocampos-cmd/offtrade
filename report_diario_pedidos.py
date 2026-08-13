"""
Report diário por e-mail da página de pedidos (https://offtrade.duckdns.org/pedidos.html)
— anexa DUAS planilhas .xlsx, uma por estado (RJ e SP), cada uma com as 4
abas da página (Pedidos Feitos, Faturados, Cortados/Cancelados, Produtos
Cortados). Cada aba replica exatamente a mesma linha/coluna do botão
"Exportar CSV" de pedidos.html (uma linha por item do pedido, não por
pedido — ver ABAS/exportarCSV em pedidos.html).

Autenticação: mesmo token_gmail.json (OAuth, escopo gmail.modify) já usado
por email_pedidos.py / alerta_logistica_rj.py pra ler e-mails — esse escopo
também autoriza o endpoint de envio (users.messages.send).

Só RJ e SP, um arquivo por estado (campo 'estado' de cada pedido/item) —
pedido explícito do usuário em 2026-08-10, mesmo dia em que pediu pra rodar
isso na VPS em vez de local.

Usuário testou e confirmou em 2026-08-10 (recebeu no próprio e-mail) —
TEST_MODE=False, envia pra lista real desde então.
"""
import base64
import json
import re
import io
import unicodedata
from datetime import date, datetime, timedelta
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

import pandas as pd
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build

BASE        = Path(__file__).parent
TOKEN_GMAIL = BASE / "token_gmail.json"
PEDIDOS_JS  = BASE / "pedidos_data.js"
ESTOQUE_JS  = BASE / "estoque_data.js"
SCOPES      = ["https://www.googleapis.com/auth/gmail.modify"]

PAGINA_URL = "https://offtrade.duckdns.org/pedidos.html"

ESTADOS_INCLUIDOS = ['RJ', 'SP']

TEST_MODE   = False
EMAIL_TESTE = "leonardo.campos@rigarr.com.br"
DESTINATARIOS = [
    "giovani.cabral@rigarr.com.br",
    "allan.correa@rigarr.com.br",
    "marcus.tanamachi@rigarr.com.br",
    "daniel.diniz@rigarr.com.br",
]
COPIA = "offtrade@rigarr.com.br, danielle.soares@rigarr.com.br"

# (chave do payload, campo extra, label do campo extra, nome da aba)
_ABAS_PEDIDOS = [
    ('pedidos_feitos', 'sistema',    'Sistema',           'Pedidos Feitos'),
    ('faturados',       'status_log', 'Status Logística',  'Faturados'),
    ('cancelados',       'motivo',    'Motivo',            'Cortados-Cancelados'),
]


def _carregar_js(path, var_name):
    raw = path.read_text(encoding='utf-8')
    m = re.search(rf'{var_name}\s*=\s*(\{{.*\}});?\s*$', raw.strip(), re.DOTALL)
    if not m:
        raise RuntimeError(f"Não achei {var_name} em {path.name}")
    return json.loads(m.group(1))


# Mesma regra de pedidos.html (_agendamento em pedidos.html) pra extrair a
# data de entrega agendada do texto livre da obs do pedido.
_RE_AGENDAMENTO = re.compile(
    r'(?:entreg|agend)\w*[^\d]{0,30}?(\d{1,2})[/.\-](\d{1,2})(?:[/.\-](\d{2,4}))?',
    re.IGNORECASE,
)
_STATUS_ENTREGUE = {'ENTREGUE', 'COMPROVADO'}


def _eh_status_entregue(status_log):
    s = (status_log or '').strip().upper()
    return s in _STATUS_ENTREGUE or 'ENTREGA TOTAL' in s


def _normalizar(s):
    s = unicodedata.normalize('NFKD', s or '').encode('ascii', 'ignore').decode('ascii')
    return s.strip().upper()


# Região Metropolitana do RJ (21 municípios, IBGE) — só ela tem tracking de
# entrega via "Controle de Notas"; fora dela o pedido nunca vai ter status
# nessa planilha, então "sem status" ali não é uma pendência de verdade.
# Comparação por prefixo porque CIDADE_CLIENTE (Oracle) vem truncado em
# alguns casos (ex.: "SAO JOAO DE MER" para "São João de Meriti").
_REGIAO_METROPOLITANA_RJ = [_normalizar(c) for c in [
    "Belford Roxo", "Cachoeiras de Macacu", "Duque de Caxias", "Guapimirim",
    "Itaboraí", "Itaguaí", "Japeri", "Magé", "Maricá", "Mesquita",
    "Nilópolis", "Niterói", "Nova Iguaçu", "Paracambi", "Queimados",
    "Rio Bonito", "Rio de Janeiro", "São Gonçalo", "São João de Meriti",
    "Seropédica", "Tanguá",
]]


def _eh_regiao_metropolitana_rj(cidade):
    c = _normalizar(cidade)
    if not c:
        return False
    return any(m.startswith(c) for m in _REGIAO_METROPOLITANA_RJ)


def _status_display(p):
    """Texto de status logística pra exibir no report: o status real se
    tiver; senão 'AGUARDANDO ROTA DE ENTREGA' pra pedidos de fora da Região
    Metropolitana do RJ (onde não existe tracking via Controle de Notas —
    não é uma pendência de verdade, só ainda não tem rota atribuída);
    'Sem status' pro resto (esses sim precisam de atenção — pedido do
    usuário em 2026-08-12)."""
    status = (p.get('status_log') or '').strip()
    if status:
        return status
    if p.get('estado') == 'RJ' and not _eh_regiao_metropolitana_rj(p.get('cidade')):
        return 'AGUARDANDO ROTA DE ENTREGA'
    return 'Sem status'


def _parse_agendamento(obs):
    if not obs:
        return None
    m = _RE_AGENDAMENTO.search(obs)
    if not m:
        return None
    dd, mm = m.group(1).zfill(2), m.group(2).zfill(2)
    yy = m.group(3)
    yy = str(date.today().year) if not yy else ('20' + yy if len(yy) == 2 else yy)
    try:
        datetime.strptime(f'{dd}/{mm}/{yy}', '%d/%m/%Y')
    except ValueError:
        return None
    return f'{dd}/{mm}/{yy}'


def _filtrar_faturados_relatorio(pedidos):
    """Report diário (ajustado em 2026-08-12): pedidos com status de entrega
    (ENTREGUE/COMPROVADO/ENTREGA TOTAL — mesmo critério do badge verde em
    pedidos.html) só entram se a entrega foi no dia anterior ('data_entrega',
    ver pedidos.py) — senão o relatório reacumula toda entrega antiga já
    resolvida. Pedidos com qualquer outro status (ainda sem desfecho: em
    rota, aberto, cancelada, retorno, sem status...) entram sempre, sem
    filtro de data, porque esses são os que ainda precisam de atenção. Só
    afeta o report — pedidos.html continua mostrando tudo, sem esse filtro."""
    ontem = (date.today() - timedelta(days=1)).strftime('%Y-%m-%d')
    return [
        p for p in pedidos
        if not _eh_status_entregue(p.get('status_log')) or p.get('data_entrega') == ontem
    ]


def _dias_sem_entrega(status_log, agendamento_str):
    """None se já entregue, sem obs de agendamento, ou agendamento no futuro."""
    if not agendamento_str or _eh_status_entregue(status_log):
        return None
    dt = datetime.strptime(agendamento_str, '%d/%m/%Y').date()
    dias = (date.today() - dt).days
    return dias if dias > 0 else None


_STATUS_BLOQUEADO = {'BLOQUEADO', 'BLOQUEADO (ALÇADA)'}


def _linhas_pedidos(pedidos, campo_extra, label_extra):
    linhas = []
    is_faturados = campo_extra == 'status_log'
    is_cancelados = campo_extra == 'motivo'
    is_pedidos_feitos = campo_extra == 'sistema'
    col_total = 'Valor Cortado' if is_cancelados else 'Total Pedido'
    for p in pedidos:
        itens = p.get('itens') or [{'desc': '', 'qt': '', 'val': ''}]
        # Agendamento (obs) aparece em qualquer aba — mesmo padrão do badge
        # 📅 já mostrado em pedidos.html independente da aba.
        agendamento = _parse_agendamento(p.get('obs'))
        dias_sem_entrega = _dias_sem_entrega(p.get(campo_extra), agendamento) if is_faturados else None
        # 'total' do pedido cancelado é sempre 0 (nada foi faturado) —
        # 'valor_cortado_total' (ver pedidos.py) é o valor real que importa.
        valor_total = p.get('valor_cortado_total') if is_cancelados else p.get('total')
        for it in itens:
            linha = {
                'Pedido':        p.get('numped'),
                'NF':            p.get('numnota'),
                'Data':          p.get('data'),
                'DataOrd':       p.get('data_ord'),
                'Vendedor':      (p.get('nome') or '').replace(' - OFF TRADE', ''),
                'Estado':        p.get('estado'),
                'Cliente':       p.get('cliente'),
                label_extra:     _status_display(p) if is_faturados else p.get(campo_extra),
                'Produto':       it.get('desc'),
                # Cancelados: 'qt'/'val' (faturado) são sempre 0 — o que
                # importa aqui é o que foi cortado de verdade (pedido do
                # usuário em 2026-08-12: Qtd/Valor Produto zerados na aba).
                'Qtd':           it.get('qtd_cortada_total') if is_cancelados else it.get('qt'),
                'Valor Produto': it.get('valor_cortado') if is_cancelados else it.get('val'),
                col_total:       valor_total,
                'Agendamento':   agendamento,
            }
            if is_faturados:
                linha['Cidade'] = p.get('cidade') or ''
                linha['Data Entrega'] = p.get('data_entrega') or ''
                linha['Dias sem Entrega'] = dias_sem_entrega
            if is_pedidos_feitos:
                linha['Posição'] = p.get('posicao') or ''
                linha['Motivo Bloqueio'] = p.get('motivo') if (p.get('posicao') or '').strip().upper() in _STATUS_BLOQUEADO else ''
            linhas.append(linha)
    return pd.DataFrame(linhas)


def _indice_estoque():
    if not ESTOQUE_JS.exists():
        return {}
    idx = {}
    for p in _carregar_js(ESTOQUE_JS, 'ESTOQUE_DATA').get('produtos', []):
        try:
            fil, prod = int(p['codfilial']), int(p['codprod'])
        except (TypeError, ValueError, KeyError):
            continue
        idx[(str(p.get('empresa') or '').upper(), fil, prod)] = p.get('qtestoque')
    return idx


def _linhas_produtos_cortados(produtos, estoque_idx):
    linhas = []
    for p in produtos:
        try:
            fil, prod = int(p.get('codfilial')), int(p.get('codprod'))
            saldo = estoque_idx.get((str(p.get('sistema') or '').upper(), fil, prod))
        except (TypeError, ValueError):
            fil, saldo = None, None
        linhas.append({
            'Data':          p.get('data'),
            'DataOrd':       p.get('data_ord'),
            'Vendedor':      (p.get('nome') or '').replace(' - OFF TRADE', ''),
            'Estado':        p.get('estado'),
            'Cliente':       p.get('cliente'),
            'Pedido':        p.get('numped'),
            'Produto':       p.get('desc'),
            'Cód Produto':   p.get('codprod'),
            'Indústria':     p.get('industria'),
            'Qtd Cortada':   p.get('qtd_cortada_total'),
            'Qtd Pedida':    p.get('qt_original'),
            'Valor Cortado': p.get('valor_cortado'),
            'Saldo Estoque': saldo,
            'CodFilial':     fil,
        })
    return pd.DataFrame(linhas)


def _filtrar_estado(lista, estado):
    return [p for p in lista if (p.get('estado') or '').strip().upper() == estado]


def _resumo(tabelas_pedidos, cortados_filtrados):
    """tabelas_pedidos: lista de (aba, pedidos_filtrados) — um item por aba de
    pedido (Pedidos Feitos/Faturados/Cortados-Cancelados). Conta e soma por
    pedido (não por item, senão duplicaria valor entre os produtos)."""
    linhas = []
    for aba, pedidos in tabelas_pedidos:
        if aba == 'Cortados-Cancelados':
            # 'total' do pedido é sempre 0 aqui (nada foi faturado) — usa
            # 'valor_cortado_total' que pedidos.py já calcula por pedido
            # (real, de PEDIDOS_CANCELADOS.SUBTOT, com fallback pra soma dos
            # itens quando a view não tem registro pra esse pedido).
            valor = sum(p.get('valor_cortado_total') or 0 for p in pedidos)
            label_valor = f'{aba} — Valor Cortado'
        else:
            valor = sum(p.get('total') or 0 for p in pedidos)
            label_valor = f'{aba} — Valor Total'
        linhas.append({'Métrica': f'{aba} — Qtd Pedidos', 'Valor': len(pedidos)})
        linhas.append({'Métrica': label_valor, 'Valor': round(valor, 2)})
        if aba == 'Pedidos Feitos':
            qtd_agendado = sum(1 for p in pedidos if _parse_agendamento(p.get('obs')))
            linhas.append({'Métrica': f'{aba} — Com Agendamento', 'Valor': qtd_agendado})
            qtd_bloqueado = sum(1 for p in pedidos if (p.get('posicao') or '').strip().upper() in _STATUS_BLOQUEADO)
            linhas.append({'Métrica': f'{aba} — Bloqueados', 'Valor': qtd_bloqueado})
        if aba == 'Faturados':
            qtd_entregues = sum(1 for p in pedidos if _eh_status_entregue(p.get('status_log')))
            linhas.append({'Métrica': f'{aba} — Entregues (dia anterior)', 'Valor': qtd_entregues})

            qtd_aberto = sum(1 for p in pedidos if (p.get('status_log') or '').strip().upper() == 'ABERTO')
            linhas.append({'Métrica': f'{aba} — Notas em Aberto', 'Valor': qtd_aberto})

            # 'Sem status' de verdade (não conta os "aguardando rota" de fora
            # da Região Metropolitana do RJ — esses não são uma pendência,
            # só não têm tracking de entrega nessa região).
            sem_status = [p for p in pedidos if _status_display(p) == 'Sem status']
            linhas.append({'Métrica': f'{aba} — Sem Status/Entrega', 'Valor': len(sem_status)})
            qtd_aguardando_rota = sum(1 for p in pedidos if _status_display(p) == 'AGUARDANDO ROTA DE ENTREGA')
            if qtd_aguardando_rota:
                linhas.append({'Métrica': f'{aba} — Aguardando Rota (fora Região Metrop.)', 'Valor': qtd_aguardando_rota})
            dias_atraso = [
                d for p in sem_status
                if (d := _dias_sem_entrega(p.get('status_log'), _parse_agendamento(p.get('obs')))) is not None
            ]
            if dias_atraso:
                linhas.append({'Métrica': f'{aba} — Dias sem Entrega (máx)', 'Valor': max(dias_atraso)})
    linhas.append({'Métrica': 'Produtos Cortados — Qtd Itens', 'Valor': len(cortados_filtrados)})
    linhas.append({
        'Métrica': 'Produtos Cortados — Qtd Total Cortada',
        'Valor': sum(p.get('qtd_cortada_total') or 0 for p in cortados_filtrados),
    })
    linhas.append({
        'Métrica': 'Produtos Cortados — Valor Total Cortado',
        'Valor': round(sum(p.get('valor_cortado') or 0 for p in cortados_filtrados), 2),
    })
    return pd.DataFrame(linhas)


def montar_tabelas(payload, estoque_idx, estado):
    """Retorna dict ordenado {nome_aba: DataFrame} — usado tanto pro .xlsx
    (anexo do e-mail) quanto pro .html (preview local), pra não duplicar a
    lógica de filtro/formatação entre os dois formatos."""
    tabelas_pedidos = []
    for chave, campo_extra, label_extra, aba in _ABAS_PEDIDOS:
        pedidos_filtrados = _filtrar_estado(payload.get(chave, []), estado)
        if chave == 'faturados':
            pedidos_filtrados = _filtrar_faturados_relatorio(pedidos_filtrados)
        tabelas_pedidos.append((aba, pedidos_filtrados))

    cortados_filtrados = _filtrar_estado(payload.get('produtos_cortados', []), estado)

    tabelas = {'Resumo': _resumo(tabelas_pedidos, cortados_filtrados)}
    for (chave, campo_extra, label_extra, aba), (_, pedidos_filtrados) in zip(_ABAS_PEDIDOS, tabelas_pedidos):
        tabelas[aba] = _linhas_pedidos(pedidos_filtrados, campo_extra, label_extra)
    tabelas['Produtos Cortados'] = _linhas_produtos_cortados(cortados_filtrados, estoque_idx)
    return tabelas


def _eh_moeda(label):
    return 'valor' in label.lower()


def montar_planilha(tabelas):
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine='openpyxl') as writer:
        for aba, df in tabelas.items():
            df.drop(columns=['DataOrd', 'CodFilial'], errors='ignore').to_excel(writer, sheet_name=aba, index=False)
        if 'Resumo' in tabelas:
            ws = writer.sheets['Resumo']
            df_resumo = tabelas['Resumo']
            for i, metrica in enumerate(df_resumo['Métrica'], start=2):  # linha 1 = cabeçalho
                cel = ws.cell(row=i, column=2)  # coluna B = Valor
                cel.number_format = '"R$" #,##0.00' if _eh_moeda(metrica) else '#,##0'
            ws.column_dimensions['A'].width = 34
            ws.column_dimensions['B'].width = 16
    return buf.getvalue()


def _fmt_metrica(label, valor):
    if _eh_moeda(label):
        return 'R$ ' + f'{valor:,.2f}'.translate(str.maketrans({',': 'X', '.': ','})).replace('X', '.')
    return f'{int(valor):,}'.replace(',', '.')


# Cor por aba — mesma linguagem visual de pedidos.html (accent por status),
# só pra diferenciar as seções de relance na hora de rolar o e-mail.
_ABA_COR = {
    'Resumo':              '#f5c518',
    'Pedidos Feitos':      '#60a5fa',
    'Faturados':           '#4ade80',
    'Cortados-Cancelados': '#ef4444',
    'Produtos Cortados':   '#fb923c',
}

# Badge de status logística — mesmas categorias/cores de _statusLogBadge em
# pedidos.html.
_STATUS_BADGE_COR = [
    (lambda s: s in ('ENTREGUE', 'COMPROVADO') or 'ENTREGA TOTAL' in s, '#4ade80'),
    (lambda s: s == 'RETORNO' or 'ENTREGA PARCIAL' in s,                '#fb923c'),
    (lambda s: s == 'CANCELADA',                                        '#ef4444'),
    (lambda s: s == 'EM ROTA',                                          '#60a5fa'),
    (lambda s: s == 'CARREGADO',                                        '#a78bfa'),
    (lambda s: s == 'ABERTO',                                           '#eab308'),
]


def _status_badge_html(status):
    s = str(status or '').strip().upper()
    if s == 'AGUARDANDO ROTA DE ENTREGA':
        cor = '#38bdf8'
    elif not s or s == 'SEM STATUS':
        cor = '#94a3b8'
    else:
        cor = next((c for teste, c in _STATUS_BADGE_COR if teste(s)), '#94a3b8')
    label = status if status else 'Sem status'
    return f"<span class='badge' style='background:{cor}22;color:{cor};border-color:{cor}55'>{label}</span>"


def _esc(v):
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return ''
    return str(v).replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;').replace('"', '&quot;')


# Colunas de item (Produto/Qtd/Valor Produto) saem da linha-mestre e viram a
# mini-tabela por trás do botão "Ver detalhes" — DataOrd é só uso interno.
_COLS_ITEM = ('DataOrd', 'Produto', 'Qtd', 'Valor Produto')


def _linha_detalhes(table_id, idx, grupo, colspan):
    """Linha-mestre por pedido (1 por pedido, não por item) com botão 'Ver
    detalhes' que expande os produtos numa mini-tabela — pedido do usuário
    em 2026-08-12, antes cada produto poluía a tabela como uma linha solta."""
    det_id = f'{table_id}-det-{idx}'
    n = len(grupo)
    itens_html = ''.join(
        f"<tr><td>{_esc(r['Produto'])}</td><td>{_esc(r['Qtd'])}</td><td>{_esc(r['Valor Produto'])}</td></tr>"
        for _, r in grupo.iterrows()
    )
    botao = (
        f"<button type='button' class='btn-detalhes' data-target='{det_id}'>"
        f"Ver detalhes ({n})</button>"
    )
    detalhes = (
        f"<tr class='detalhes-row' id='{det_id}' style='display:none'>"
        f"<td colspan='{colspan}'><table class='mini-produtos'><thead><tr>"
        f"<th>Produto</th><th>Qtd</th><th>Valor Produto</th></tr></thead>"
        f"<tbody>{itens_html}</tbody></table></td></tr>"
    )
    return botao, detalhes


def _tabela_faturados_com_filtro(df, table_id):
    """Tabela com badge de status + dropdown JS pra filtrar linha por status
    logística direto no HTML (pedido do usuário em 2026-08-12). Cada linha
    também leva data-data (data do pedido, 'DataOrd' — AAAA-MM-DD) pro
    filtro global de data (pedido do usuário em 2026-08-12)."""
    status_unicos = sorted({(v or 'Sem status') for v in df['Status Logística']})
    opcoes = ''.join(f"<option value='{_esc(s)}'>{_esc(s)}</option>" for s in status_unicos)
    filtro_html = (
        f"<div class='filtro-status'><label for='{table_id}-sel'>Filtrar por status logística:</label>"
        f"<select id='{table_id}-sel' onchange=\"aplicarFiltros()\">"
        f"<option value=''>Todos ({df['Pedido'].nunique()})</option>{opcoes}</select></div>"
    )
    cols = [c for c in df.columns if c not in _COLS_ITEM]
    header = ''.join(f'<th>{_esc(c)}</th>' for c in cols) + '<th>Detalhes</th>'
    linhas = []
    for idx, (_, grupo) in enumerate(df.groupby('Pedido', sort=False)):
        row = grupo.iloc[0]
        status_raw = row['Status Logística'] or 'Sem status'
        cells = ''.join(
            f"<td>{_status_badge_html(row[c])}</td>" if c == 'Status Logística' else f"<td>{_esc(row[c])}</td>"
            for c in cols
        )
        botao, detalhes = _linha_detalhes(table_id, idx, grupo, len(cols) + 1)
        linhas.append(
            f"<tr data-status=\"{_esc(status_raw)}\" data-data=\"{_esc(row.get('DataOrd'))}\" "
            f"data-pedido=\"{_esc(row.get('Pedido'))}\" data-valor=\"{_esc(row.get('Total Pedido'))}\" "
            f"data-dias=\"{_esc(row.get('Dias sem Entrega'))}\">{cells}<td>{botao}</td></tr>{detalhes}"
        )
    tabela_html = (
        f"<table id='{table_id}' class='tbl-filtravel' data-aba='Faturados'>"
        f"<thead><tr>{header}</tr></thead><tbody>{''.join(linhas)}</tbody></table>"
    )
    return filtro_html, tabela_html


def _posicao_badge_html(posicao):
    s = str(posicao or '').strip().upper()
    if s in _STATUS_BLOQUEADO:
        cor = '#ef4444'
    elif s == 'LIBERADO':
        cor = '#4ade80'
    elif s == 'PENDENTE':
        cor = '#eab308'
    elif not s:
        return ''
    else:
        cor = '#94a3b8'
    return f"<span class='badge' style='background:{cor}22;color:{cor};border-color:{cor}55'>{posicao}</span>"


def _tabela_pedidos_feitos_com_destaque(df, table_id):
    """Linha inteira com fundo vermelho quando bloqueado — pedido do usuário
    em 2026-08-12 pra chamar atenção pros pedidos feitos travados. Cada
    linha também leva data-data pro filtro global de data, e uma por pedido
    (não por item) com botão 'Ver detalhes' pros produtos."""
    cols = [c for c in df.columns if c not in _COLS_ITEM]
    header = ''.join(f'<th>{_esc(c)}</th>' for c in cols) + '<th>Detalhes</th>'
    linhas = []
    for idx, (_, grupo) in enumerate(df.groupby('Pedido', sort=False)):
        row = grupo.iloc[0]
        bloqueado = str(row.get('Posição') or '').strip().upper() in _STATUS_BLOQUEADO
        agendado = bool(row.get('Agendamento'))
        cells = ''.join(
            f"<td>{_posicao_badge_html(row[c])}</td>" if c == 'Posição' else f"<td>{_esc(row[c])}</td>"
            for c in cols
        )
        tr_style = " style='background:rgba(239,68,68,.12)'" if bloqueado else ''
        botao, detalhes = _linha_detalhes(table_id, idx, grupo, len(cols) + 1)
        linhas.append(
            f"<tr{tr_style} data-data=\"{_esc(row.get('DataOrd'))}\" data-pedido=\"{_esc(row.get('Pedido'))}\" "
            f"data-valor=\"{_esc(row.get('Total Pedido'))}\" data-agendamento=\"{'1' if agendado else ''}\" "
            f"data-bloqueado=\"{'1' if bloqueado else ''}\">{cells}<td>{botao}</td></tr>{detalhes}"
        )
    return (
        f"<table id='{table_id}' class='tbl-filtravel' data-aba='Pedidos Feitos'>"
        f"<thead><tr>{header}</tr></thead><tbody>{''.join(linhas)}</tbody></table>"
    )


def _tabela_cortados_cancelados_com_data(df, table_id):
    """Mesmo tratamento de data-data das outras tabelas, com data-pedido/
    data-valor pro JS recalcular Qtd Pedidos/Valor Cortado dos cartões
    quando o filtro de data muda, e uma linha-mestre por pedido com botão
    'Ver detalhes' pros produtos cortados (pedido do usuário em 2026-08-12)."""
    cols = [c for c in df.columns if c not in _COLS_ITEM]
    header = ''.join(f'<th>{_esc(c)}</th>' for c in cols) + '<th>Detalhes</th>'
    linhas = []
    for idx, (_, grupo) in enumerate(df.groupby('Pedido', sort=False)):
        row = grupo.iloc[0]
        cells = ''.join(f'<td>{_esc(row[c])}</td>' for c in cols)
        botao, detalhes = _linha_detalhes(table_id, idx, grupo, len(cols) + 1)
        linhas.append(
            f"<tr data-data=\"{_esc(row.get('DataOrd'))}\" data-pedido=\"{_esc(row.get('Pedido'))}\" "
            f"data-valor=\"{_esc(row.get('Valor Cortado'))}\">{cells}<td>{botao}</td></tr>{detalhes}"
        )
    return (
        f"<table id='{table_id}' class='tbl-filtravel' data-aba='Cortados-Cancelados'>"
        f"<thead><tr>{header}</tr></thead><tbody>{''.join(linhas)}</tbody></table>"
    )


def _tabela_produtos_cortados_com_data(df, table_id):
    """Aqui os cartões (Qtd Itens/Qtd Total Cortada/Valor Total Cortado) são
    somados por item, sem deduplicar por pedido — cada linha já é um item
    cortado (pedido do usuário em 2026-08-12). Os data-* extras (codprod/
    industria/produtonome/codfilial/saldo) alimentam o ranking por produto
    (Indústria/Produto/Cód Produto/Qtd Cortada/Saldo Estoque) que o JS
    recalcula ao lado, deduplicando o Saldo Estoque por filial pra não somar
    o mesmo saldo repetido entre pedidos do mesmo produto (pedido do usuário
    em 2026-08-12) — sem mexer nos cartões acima."""
    cols = [c for c in df.columns if c not in ('DataOrd', 'CodFilial')]
    header = ''.join(f'<th>{_esc(c)}</th>' for c in cols)
    linhas = []
    for _, row in df.iterrows():
        cells = ''.join(f'<td>{_esc(row[c])}</td>' for c in cols)
        linhas.append(
            f"<tr data-data=\"{_esc(row.get('DataOrd'))}\" data-qtdcortada=\"{_esc(row.get('Qtd Cortada'))}\" "
            f"data-valorcortado=\"{_esc(row.get('Valor Cortado'))}\" data-codprod=\"{_esc(row.get('Cód Produto'))}\" "
            f"data-industria=\"{_esc(row.get('Indústria'))}\" data-produtonome=\"{_esc(row.get('Produto'))}\" "
            f"data-codfilial=\"{_esc(row.get('CodFilial'))}\" data-saldo=\"{_esc(row.get('Saldo Estoque'))}\">{cells}</tr>"
        )
    return (
        f"<table id='{table_id}' class='tbl-filtravel' data-aba='Produtos Cortados'>"
        f"<thead><tr>{header}</tr></thead><tbody>{''.join(linhas)}</tbody></table>"
    )


def _cartoes_html(linhas_aba):
    """data-metrica identifica o cartão pro JS recalcular o valor quando o
    filtro de data muda (pedido do usuário em 2026-08-12: filtro não
    atualizava os cartões, só as linhas das tabelas)."""
    return ''.join(
        f"<div class='card{' money' if _eh_moeda(row['Métrica']) else ''}' data-metrica=\"{_esc(row['Métrica'])}\">"
        f"<div class='lbl'>{row['Métrica'].split('—', 1)[1].strip()}</div>"
        f"<div class='val'>{_fmt_metrica(row['Métrica'], row['Valor'])}</div></div>"
        for _, row in linhas_aba.iterrows()
    )


def montar_html(tabelas, estado, hoje_str):
    resumo_df = tabelas.get('Resumo')
    secoes = []
    for aba, df in tabelas.items():
        resumo_aba = ''
        if aba != 'Resumo' and resumo_df is not None:
            linhas_aba = resumo_df[resumo_df['Métrica'].str.startswith(f'{aba} — ')]
            if not linhas_aba.empty:
                resumo_aba = f"<div class='resumo-cards'>{_cartoes_html(linhas_aba)}</div>"

        if aba == 'Resumo':
            # Cartões agrupados por aba (mesmo visual das mini-seções abaixo)
            # em vez de uma tabela Métrica/Valor achatada — pedido do
            # usuário em 2026-08-12, mesmo se ficar com bastante linha.
            abas_presentes = list(dict.fromkeys(m.split(' — ')[0] for m in df['Métrica']))
            blocos = []
            for pref in abas_presentes:
                sub = df[df['Métrica'].str.startswith(f'{pref} — ')]
                cor_sub = _ABA_COR.get(pref, '#f5c518')
                blocos.append(
                    f"<h3 style='color:{cor_sub}'>{pref}</h3>"
                    f"<div class='resumo-cards'>{_cartoes_html(sub)}</div>"
                )
            tabela_html = ''.join(blocos)
        elif aba == 'Faturados' and 'Status Logística' in df.columns:
            filtro_html, tabela_html = _tabela_faturados_com_filtro(df, f'tbl-fat-{estado}')
            resumo_aba += filtro_html
            problema = df[(df['Status Logística'] == 'Sem status') & df['Dias sem Entrega'].notna()].drop_duplicates(subset=['Pedido'])
            if not problema.empty:
                maxd = int(problema['Dias sem Entrega'].max())
                resumo_aba += (
                    f"<div class='alerta'>&#9888; {len(problema)} nota(s) faturada(s) sem status de "
                    f"logística nem confirmação de entrega — até {maxd} dia(s) após a data agendada na obs.</div>"
                )
        elif aba == 'Pedidos Feitos' and 'Posição' in df.columns:
            tabela_html = _tabela_pedidos_feitos_com_destaque(df, f'tbl-ped-{estado}')
            qtd_bloqueado = df[df['Posição'].str.upper().isin(_STATUS_BLOQUEADO)].drop_duplicates(subset=['Pedido'])
            if not qtd_bloqueado.empty:
                resumo_aba += (
                    f"<div class='alerta'>&#9888; {len(qtd_bloqueado)} pedido(s) feito(s) bloqueado(s) — "
                    "veja a coluna Motivo Bloqueio.</div>"
                )
        elif aba == 'Cortados-Cancelados' and 'DataOrd' in df.columns:
            tabela_html = _tabela_cortados_cancelados_com_data(df, f'tbl-canc-{estado}')
        elif aba == 'Produtos Cortados' and 'DataOrd' in df.columns:
            tabela_html = _tabela_produtos_cortados_com_data(df, f'tbl-cort-{estado}')
            # Ranking por produto (Indústria/Produto/Cód Produto/Qtd Cortada/
            # Saldo Estoque) — construído e recalculado inteiramente em JS a
            # partir das linhas visíveis da tabela acima, sem tocar nos
            # cartões de resumo (pedido do usuário em 2026-08-12).
            resumo_aba += f"<div id='ranking-cort-{estado}' class='ranking-produtos'></div>"
        else:
            tabela_html = df.to_html(index=False, na_rep='', border=0)

        cor = _ABA_COR.get(aba, '#f5c518')
        aberto = ' open' if aba == 'Resumo' else ''
        secoes.append(
            f"<details{aberto} style='border-left:3px solid {cor}'>"
            f"<summary style='color:{cor}'>{aba} <span class='qtd'>({len(df)})</span></summary>\n"
            f"{resumo_aba}\n{tabela_html}\n</details>"
        )
    corpo = "\n".join(secoes)
    return f"""<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="UTF-8">
<title>Report Diário de Pedidos — {estado} — {hoje_str}</title>
<style>
  body {{ background:#0f1117; color:#e2e8f0; font-family:'Segoe UI',system-ui,sans-serif; padding:24px; }}
  h1 {{ color:#3b82f6; font-size:1.3rem; }}
  details {{ margin-top:18px; border:1px solid #2d3144; border-radius:10px; padding:0 16px 16px; }}
  details[open] {{ padding-top:2px; }}
  summary {{ cursor:pointer; color:#f5c518; font-size:1rem; font-weight:700; padding:14px 0; list-style:none; }}
  summary::-webkit-details-marker {{ display:none; }}
  summary::before {{ content:'▸ '; }}
  details[open] > summary::before {{ content:'▾ '; }}
  summary .qtd {{ color:#94a3b8; font-weight:400; font-size:.82rem; }}
  table {{ border-collapse:collapse; width:100%; font-size:.85rem; margin-top:10px; }}
  th, td {{ padding:6px 10px; border-bottom:1px solid #2d3144; text-align:left; white-space:nowrap; }}
  th {{ background:#22263a; color:#94a3b8; text-transform:uppercase; font-size:.72rem; }}
  tr:hover td {{ background:#1a1d27; }}
  td.num {{ text-align:right; font-variant-numeric:tabular-nums; }}
  .resumo-cards {{ display:grid; grid-template-columns:repeat(auto-fill,minmax(150px,1fr)); gap:10px; margin-top:10px; }}
  .card {{ background:#1a1d27; border:1px solid #2d3144; border-radius:10px; padding:10px 14px; }}
  .card .lbl {{ font-size:.62rem; text-transform:uppercase; letter-spacing:.06em; color:#94a3b8; margin-bottom:4px; }}
  .card .val {{ font-size:1.15rem; font-weight:700; color:#e2e8f0; }}
  .card.money .val {{ color:#f5c518; }}
  .badge {{ display:inline-block; padding:2px 9px; border-radius:99px; font-size:.72rem; font-weight:700; border:1px solid; white-space:nowrap; }}
  .alerta {{ margin-top:10px; padding:8px 12px; border-radius:8px; background:rgba(239,68,68,.15); border:1px solid #ef4444; color:#fca5a5; font-size:.82rem; }}
  .filtro-status {{ margin-top:10px; display:flex; align-items:center; gap:8px; font-size:.82rem; color:#94a3b8; }}
  .filtro-status select {{ background:#1a1d27; color:#e2e8f0; border:1px solid #2d3144; border-radius:6px; padding:5px 8px; font-size:.82rem; font-family:inherit; }}
  .filtro-data {{ margin-top:14px; display:flex; align-items:center; gap:8px; flex-wrap:wrap; font-size:.82rem; color:#94a3b8; }}
  .filtro-data input {{ background:#1a1d27; color:#e2e8f0; border:1px solid #2d3144; border-radius:6px; padding:5px 8px; font-size:.82rem; font-family:inherit; }}
  .filtro-data button {{ background:#1a1d27; color:#e2e8f0; border:1px solid #2d3144; border-radius:6px; padding:5px 10px; font-size:.82rem; font-family:inherit; cursor:pointer; }}
  .filtro-data button:hover {{ background:#22263a; }}
  .btn-detalhes {{ background:#1a1d27; color:#e2e8f0; border:1px solid #2d3144; border-radius:6px; padding:4px 10px; font-size:.75rem; font-family:inherit; cursor:pointer; white-space:nowrap; }}
  .btn-detalhes:hover {{ border-color:#f5c518; color:#f5c518; }}
  .detalhes-row td {{ background:#12141c; padding:8px 10px 8px 26px; }}
  .mini-produtos {{ width:100%; font-size:.8rem; margin-top:0; }}
  .mini-produtos th {{ background:transparent; color:#94a3b8; font-size:.68rem; padding:4px 8px; }}
  .mini-produtos td {{ padding:4px 8px; border-bottom:1px solid #232738; white-space:normal; }}
  .ranking-produtos {{ margin-top:14px; }}
  .ranking-produtos h4 {{ font-size:.78rem; color:#94a3b8; text-transform:uppercase; letter-spacing:.05em; margin-bottom:6px; }}
  .ranking-tabela td:nth-child(4), .ranking-tabela td:nth-child(5) {{ text-align:right; font-variant-numeric:tabular-nums; }}
  .sem-dado {{ color:#94a3b8; font-size:.82rem; padding:8px 0; }}
</style>
</head>
<body>
<h1>Report Diário de Pedidos — {estado} — {hoje_str}</h1>
<div class='filtro-data'>
  <label for='data-de'>Data do pedido — de:</label>
  <input type='date' id='data-de' oninput='aplicarFiltros()'>
  <label for='data-ate'>até:</label>
  <input type='date' id='data-ate' oninput='aplicarFiltros()'>
  <button type='button' onclick="document.getElementById('data-de').value='';document.getElementById('data-ate').value='';aplicarFiltros()">Limpar</button>
</div>
{corpo}
<script>
function aplicarFiltros() {{
  var de = document.getElementById('data-de').value;
  var ate = document.getElementById('data-ate').value;
  document.querySelectorAll('table.tbl-filtravel').forEach(function(tabela) {{
    var sel = document.getElementById(tabela.id + '-sel');
    var statusFiltro = sel ? sel.value : '';
    tabela.querySelectorAll('tbody tr').forEach(function(tr) {{
      if (tr.classList.contains('detalhes-row')) return;
      var d = tr.getAttribute('data-data') || '';
      var okData = (!de || !d || d >= de) && (!ate || !d || d <= ate);
      var okStatus = !statusFiltro || tr.getAttribute('data-status') === statusFiltro;
      var visivel = okData && okStatus;
      tr.style.display = visivel ? '' : 'none';
      var det = tr.nextElementSibling;
      if (!visivel && det && det.classList.contains('detalhes-row')) {{
        det.style.display = 'none';
      }}
    }});
    atualizarCards(tabela);
    if (tabela.dataset.aba === 'Produtos Cortados') atualizarRankingProdutos(tabela);
  }});
}}

function escHtml(s) {{
  return String(s == null ? '' : s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}}

// Ranking por produto (Indústria/Produto/Cód Produto/Qtd Cortada/Saldo
// Estoque), recalculado a partir das linhas visíveis da tabela de Produtos
// Cortados — Qtd Cortada soma todas as ocorrências, Saldo Estoque dedup por
// filial (mesmo produto/filial repetido em vários pedidos não pode somar o
// mesmo saldo várias vezes). Não mexe nos cartões de resumo acima (pedido
// do usuário em 2026-08-12).
function atualizarRankingProdutos(tabela) {{
  var container = document.getElementById(tabela.id.replace('tbl-', 'ranking-'));
  if (!container) return;
  var visiveis = Array.prototype.filter.call(tabela.querySelectorAll('tbody tr'), function(tr) {{
    return tr.style.display !== 'none';
  }});

  var porProduto = {{}};
  visiveis.forEach(function(tr) {{
    var cp = tr.getAttribute('data-codprod');
    if (!cp) return;
    if (!porProduto[cp]) {{
      porProduto[cp] = {{
        industria: tr.getAttribute('data-industria') || '',
        produto: tr.getAttribute('data-produtonome') || '',
        codprod: cp,
        qtd: 0,
        saldos: {{}}
      }};
    }}
    var g = porProduto[cp];
    g.qtd += parseFloat(tr.getAttribute('data-qtdcortada') || '0') || 0;
    var fil = tr.getAttribute('data-codfilial');
    var saldo = tr.getAttribute('data-saldo');
    if (fil && saldo && !(fil in g.saldos)) {{
      g.saldos[fil] = parseFloat(saldo) || 0;
    }}
  }});

  var lista = Object.keys(porProduto).map(function(cp) {{
    var g = porProduto[cp];
    var saldoTotal = 0;
    Object.keys(g.saldos).forEach(function(fil) {{ saldoTotal += g.saldos[fil]; }});
    g.saldoTotal = saldoTotal;
    return g;
  }}).sort(function(a, b) {{ return b.qtd - a.qtd; }});

  if (!lista.length) {{
    container.innerHTML = "<h4>Ranking por produto</h4><div class='sem-dado'>Nenhum produto cortado no período filtrado.</div>";
    return;
  }}

  var linhas = lista.map(function(g) {{
    return '<tr><td>' + escHtml(g.industria) + '</td><td>' + escHtml(g.produto) + '</td><td>' +
      escHtml(g.codprod) + '</td><td>' + Math.round(g.qtd).toLocaleString('pt-BR') + '</td><td>' +
      g.saldoTotal.toLocaleString('pt-BR') + '</td></tr>';
  }}).join('');

  container.innerHTML =
    "<h4>Ranking por produto</h4><table class='ranking-tabela'><thead><tr><th>Indústria</th>" +
    "<th>Produto</th><th>Cód Produto</th><th>Qtd Cortada</th><th>Saldo Estoque</th></tr></thead>" +
    "<tbody>" + linhas + "</tbody></table>";
}}

// Botão "Ver detalhes" de cada pedido — expande/colapsa a mini-tabela de
// produtos logo abaixo (pedido do usuário em 2026-08-12).
document.addEventListener('click', function(e) {{
  var btn = e.target.closest('.btn-detalhes');
  if (!btn) return;
  var alvo = document.getElementById(btn.dataset.target);
  if (!alvo) return;
  var aberto = alvo.style.display !== 'none';
  alvo.style.display = aberto ? 'none' : 'table-row';
  var m = btn.textContent.match(/\(\d+\)/);
  btn.textContent = (aberto ? 'Ver detalhes ' : 'Ocultar detalhes ') + (m ? m[0] : '');
}});

function fmtMetrica(label, valor) {{
  if (label.toLowerCase().indexOf('valor') !== -1) {{
    return 'R$ ' + valor.toLocaleString('pt-BR', {{minimumFractionDigits: 2, maximumFractionDigits: 2}});
  }}
  return Math.round(valor).toLocaleString('pt-BR');
}}

function setCard(metrica, valor) {{
  document.querySelectorAll('[data-metrica="' + metrica.replace(/"/g, '\\\\"') + '"] .val').forEach(function(el) {{
    el.textContent = fmtMetrica(metrica, valor);
  }});
}}

// Recalcula os cartões de resumo (Qtd Pedidos, Valor Total, Entregues...) a
// partir das linhas ainda visíveis depois do filtro de data/status — sem
// isso os cartões ficavam com os números do dia inteiro mesmo filtrando as
// tabelas (pedido do usuário em 2026-08-12).
function atualizarCards(tabela) {{
  var aba = tabela.dataset.aba;
  if (!aba) return;
  var visiveis = Array.prototype.filter.call(tabela.querySelectorAll('tbody tr'), function(tr) {{
    return tr.style.display !== 'none';
  }});

  if (aba === 'Produtos Cortados') {{
    var qtdTotal = 0, valorTotal = 0;
    visiveis.forEach(function(tr) {{
      qtdTotal += parseFloat(tr.getAttribute('data-qtdcortada') || '0') || 0;
      valorTotal += parseFloat(tr.getAttribute('data-valorcortado') || '0') || 0;
    }});
    setCard('Produtos Cortados — Qtd Itens', visiveis.length);
    setCard('Produtos Cortados — Qtd Total Cortada', qtdTotal);
    setCard('Produtos Cortados — Valor Total Cortado', valorTotal);
    return;
  }}

  var pedidosVistos = {{}}, somaValor = 0;
  var agendados = {{}}, bloqueados = {{}};
  var entregues = {{}}, abertos = {{}}, semStatus = {{}}, aguardandoRota = {{}};
  var diasMax = null;

  visiveis.forEach(function(tr) {{
    var ped = tr.getAttribute('data-pedido');
    if (!ped) return;
    if (!(ped in pedidosVistos)) {{
      pedidosVistos[ped] = true;
      somaValor += parseFloat(tr.getAttribute('data-valor') || '0') || 0;
    }}
    if (tr.getAttribute('data-agendamento') === '1') agendados[ped] = true;
    if (tr.getAttribute('data-bloqueado') === '1') bloqueados[ped] = true;
    var status = tr.getAttribute('data-status');
    if (status) {{
      var su = status.toUpperCase();
      if (su === 'ENTREGUE' || su === 'COMPROVADO' || su.indexOf('ENTREGA TOTAL') !== -1) {{
        entregues[ped] = true;
      }} else if (su === 'ABERTO') {{
        abertos[ped] = true;
      }} else if (status === 'Sem status') {{
        semStatus[ped] = true;
        var dias = parseFloat(tr.getAttribute('data-dias'));
        if (!isNaN(dias)) diasMax = (diasMax === null) ? dias : Math.max(diasMax, dias);
      }} else if (status === 'AGUARDANDO ROTA DE ENTREGA') {{
        aguardandoRota[ped] = true;
      }}
    }}
  }});

  setCard(aba + ' — Qtd Pedidos', Object.keys(pedidosVistos).length);
  setCard(aba + (aba === 'Cortados-Cancelados' ? ' — Valor Cortado' : ' — Valor Total'), somaValor);

  if (aba === 'Pedidos Feitos') {{
    setCard(aba + ' — Com Agendamento', Object.keys(agendados).length);
    setCard(aba + ' — Bloqueados', Object.keys(bloqueados).length);
  }}
  if (aba === 'Faturados') {{
    setCard(aba + ' — Entregues (dia anterior)', Object.keys(entregues).length);
    setCard(aba + ' — Notas em Aberto', Object.keys(abertos).length);
    setCard(aba + ' — Sem Status/Entrega', Object.keys(semStatus).length);
    setCard(aba + ' — Aguardando Rota (fora Região Metrop.)', Object.keys(aguardandoRota).length);
    if (diasMax !== null) setCard(aba + ' — Dias sem Entrega (máx)', diasMax);
  }}
}}

// Popula o ranking por produto já na primeira carga da página, sem esperar
// o usuário mexer no filtro de data (pedido do usuário em 2026-08-12).
aplicarFiltros();
</script>
</body>
</html>
"""


def _get_service():
    if not TOKEN_GMAIL.exists():
        raise FileNotFoundError(
            "token_gmail.json não encontrado. Execute gmail_setup.py uma vez "
            "para autorizar o acesso ao Gmail."
        )
    creds = Credentials.from_authorized_user_file(str(TOKEN_GMAIL), SCOPES)
    if not creds.valid:
        if creds.expired and creds.refresh_token:
            creds.refresh(Request())
            TOKEN_GMAIL.write_text(creds.to_json(), encoding="utf-8")
        else:
            raise RuntimeError(
                "Token Gmail inválido ou sem refresh_token. "
                "Rode gmail_setup.py novamente para reautorizar."
            )
    return build("gmail", "v1", credentials=creds)


def enviar_report(service, destinatarios, arquivos, hoje_str, cc=None, htmls=None):
    """arquivos: lista de (estado, xlsx_bytes). htmls: lista de (estado, html_str)."""
    msg = MIMEMultipart()
    msg["Subject"] = f"Report Diário de Pedidos — {hoje_str}"
    msg["To"] = ", ".join(destinatarios)
    if cc:
        msg["Cc"] = cc
    estados_txt = " e ".join(estado for estado, _ in arquivos)
    corpo = (
        f"Segue em anexo o report diário de pedidos ({estados_txt}), um .xlsx e um .html "
        "por estado — cada um com 5 abas/seções: Resumo, Pedidos Feitos, Faturados, "
        "Cortados-Cancelados e Produtos Cortados (cada aba já com seu próprio resumo).\n\n"
        f"Página completa e sempre atualizada: {PAGINA_URL}\n"
    )
    msg.attach(MIMEText(corpo, "plain"))
    for estado, xlsx_bytes in arquivos:
        anexo = MIMEApplication(xlsx_bytes, _subtype="xlsx")
        nome_arquivo = f"pedidos_{estado}_{hoje_str.replace('/', '-')}.xlsx"
        anexo.add_header("Content-Disposition", "attachment", filename=nome_arquivo)
        msg.attach(anexo)
    for estado, html_str in (htmls or []):
        anexo = MIMEApplication(html_str.encode('utf-8'), _subtype="html")
        nome_arquivo = f"pedidos_{estado}_{hoje_str.replace('/', '-')}.html"
        anexo.add_header("Content-Disposition", "attachment", filename=nome_arquivo)
        msg.attach(anexo)
    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
    return service.users().messages().send(userId="me", body={"raw": raw}).execute()


def gerar_previews(enviar=False):
    """Monta as tabelas, salva os HTMLs localmente (RJ e SP) pra review e,
    só se enviar=True, manda o e-mail de fato com os anexos .xlsx + .html.

    Padrão é enviar=False — pedido do usuário em 2026-08-12 pra sempre
    revisar o conteúdo (via os HTMLs) antes de qualquer envio real."""
    payload = _carregar_js(PEDIDOS_JS, 'PEDIDOS_DATA')
    estoque_idx = _indice_estoque()
    hoje_str = date.today().strftime('%d/%m/%Y')
    hoje_arquivo = hoje_str.replace('/', '-')

    tabelas_por_estado = {
        estado: montar_tabelas(payload, estoque_idx, estado)
        for estado in ESTADOS_INCLUIDOS
    }

    html_paths = []
    htmls = []
    for estado, tabelas in tabelas_por_estado.items():
        html_str = montar_html(tabelas, estado, hoje_str)
        htmls.append((estado, html_str))
        html_path = BASE / f"report_pedidos_{estado}_{hoje_arquivo}.html"
        html_path.write_text(html_str, encoding='utf-8')
        html_paths.append(html_path)
        print(f"Preview salvo: {html_path}")

    if not enviar:
        print("Envio NÃO disparado (enviar=False) — confira os HTMLs acima antes de rodar com enviar=True.")
        return html_paths

    arquivos = [(estado, montar_planilha(tabelas)) for estado, tabelas in tabelas_por_estado.items()]
    destinatarios = [EMAIL_TESTE] if TEST_MODE else DESTINATARIOS
    cc = None if TEST_MODE else COPIA
    service = _get_service()
    enviar_report(service, destinatarios, arquivos, hoje_str, cc=cc, htmls=htmls)
    print(f"OK - report enviado para: {', '.join(destinatarios)}"
          f"{f' (cc: {cc})' if cc else ''} ({', '.join(e for e, _ in arquivos)})")
    if TEST_MODE:
        print("[TEST_MODE=True] Lista real (Giovani/Allan/Marcus/Daniel) NÃO foi usada.")
    return html_paths


def main():
    gerar_previews(enviar=True)


if __name__ == "__main__":
    main()
