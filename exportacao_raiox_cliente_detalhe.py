"""
Gera raiox_cliente_detalhe_data.js — ficha individual de cada cliente OFF
TRADE em todas as bases (RJ e ES via CRC, SP via SPON, MG via MGON): dados
cadastrais, histórico mensal e detalhado (data/indústria/produto/qtd/valor)
de compras, faturamento por vendedor (quem efetivamente vendeu, via
PCMOV.CODUSUR) e por indústria/fornecedor. CODCLI e CODUSUR não são únicos
entre bases — cliente e vendedor são identificados por chave composta
"ESTADO-código". Alimenta raiox_cliente_detalhe.html (busca por nome,
acessível a partir dos botões "Ver clientes" de cada ramo em
raiox_clientes.html).
"""
import json
import subprocess
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd

from meta import engine, engine_spon, engine_mgon, carregar_dados

ANO = 2026
MES_INI = f"{ANO}-01-01"
MES_FIM = f"{ANO}-07-31"

HOJE = date.today()

# Nº mínimo de compradores no grupo (ramo+estado) pra uma indústria entrar no
# ranking de recomendação — evita recomendar algo que só 1-2 clientes atípicos compram.
MIN_COMPRADORES_RECOMENDACAO = 3
# Faturamento mínimo na janela anterior pra considerar queda relevante (ignora
# clientes muito pequenos/irregulares, onde uma variação de R$50 vira "-80%").
MIN_FATURAMENTO_RISCO = 200.0
# Nº mínimo de compras (logo, de intervalos) pro ciclo médio ser confiável —
# com só 2 compras, um único intervalo "é" a média; se essas 2 compras caíram
# em dias seguidos por coincidência (ou um pedido dividido em 2 notas), o
# ciclo médio sai artificialmente curto (ex.: 1 dia) e qualquer intervalo
# normal depois já dispara "atrasado" incorretamente.
MIN_COMPRAS_CICLO_CONFIAVEL = 3

BASES = [
    {"estado": "RJ", "engine": engine,      "schema": "CRC",  "filiais": ["2", "4"]},
    {"estado": "ES", "engine": engine,      "schema": "CRC",  "filiais": ["1"]},
    {"estado": "SP", "engine": engine_spon, "schema": "SPON", "filiais": ["1", "2"]},
    {"estado": "MG", "engine": engine_mgon, "schema": "MGON", "filiais": ["1", "2"]},
]


def _query_vendedores(schema, estado):
    # RJ e ES compartilham o schema CRC — sem o filtro de ESTADO, as duas
    # iterações trariam o mesmo roster de vendedores/clientes duplicado.
    # Traz supervisor/gerente pra alimentar os filtros em cascata da página.
    return f"""
        SELECT U.CODUSUR, U.NOME,
               COALESCE(S.NOME, 'Sem supervisor') AS SUPERVISOR,
               COALESCE(G.NOMEGERENTE, 'Sem gerente') AS GERENTE
        FROM {schema}.PCUSUARI U
        LEFT JOIN {schema}.PCSUPERV S ON U.CODSUPERVISOR = S.CODSUPERVISOR
        LEFT JOIN {schema}.PCGERENTE G ON S.CODGERENTE = G.CODGERENTE
        WHERE U.NOME LIKE '%OFF TRADE%' AND U.ESTADO = '{estado}'
    """


def _query_clientes(schema, estado):
    return f"""
        SELECT C.CODCLI, C.CLIENTE, COALESCE(C.FANTASIA, C.CLIENTE) FANTASIA,
               COALESCE(C.MUNICENT,'') CIDADE, COALESCE(A.RAMO,'OUTROS') RAMO,
               C.CODUSUR1, C.CODUSUR2, C.DTULTCOMP, COALESCE(R.DESCRICAO,'') REDE
        FROM {schema}.PCCLIENT C
        JOIN {schema}.PCATIVI A ON C.CODATV1 = A.CODATIV
        LEFT JOIN {schema}.PCUSUARI U1 ON C.CODUSUR1 = U1.CODUSUR
        LEFT JOIN {schema}.PCUSUARI U2 ON C.CODUSUR2 = U2.CODUSUR
        LEFT JOIN {schema}.PCREDECLIENTE R ON C.CODREDE = R.CODREDE
        WHERE (U1.NOME LIKE '%OFF TRADE%' OR U2.NOME LIKE '%OFF TRADE%')
          AND (U1.ESTADO = '{estado}' OR U2.ESTADO = '{estado}')
    """


def _query_vendas(schema, filiais):
    fil_clause = f"AND M.CODFILIAL IN ({','.join(filiais)})" if filiais else ""
    return f"""
        SELECT M.CODCLI, TRUNC(M.DTMOV,'MM') AS MES,
               COALESCE(F.FANTASIA,'SEM FANTASIA') AS FORNECEDOR,
               M.CODUSUR, SUM(M.PUNIT*M.QT) AS FATURAMENTO
        FROM {schema}.PCMOV M
        JOIN {schema}.PCCLIENT C ON M.CODCLI = C.CODCLI
        JOIN {schema}.PCFORNEC F ON M.CODFORNEC = F.CODFORNEC
        LEFT JOIN {schema}.PCUSUARI U1 ON C.CODUSUR1 = U1.CODUSUR
        LEFT JOIN {schema}.PCUSUARI U2 ON C.CODUSUR2 = U2.CODUSUR
        WHERE (U1.NOME LIKE '%OFF TRADE%' OR U2.NOME LIKE '%OFF TRADE%')
          {fil_clause}
          AND M.CODOPER = 'S'
          AND M.NUMNOTADEV IS NULL
          AND M.DTCANCEL IS NULL
          AND TRUNC(M.DTMOV) >= TO_DATE('{MES_INI}','YYYY-MM-DD')
          AND TRUNC(M.DTMOV) <= TO_DATE('{MES_FIM}','YYYY-MM-DD')
        GROUP BY M.CODCLI, TRUNC(M.DTMOV,'MM'), COALESCE(F.FANTASIA,'SEM FANTASIA'), M.CODUSUR
    """


def _query_historico(schema, filiais):
    fil_clause = f"AND M.CODFILIAL IN ({','.join(filiais)})" if filiais else ""
    return f"""
        SELECT M.CODCLI, TRUNC(M.DTMOV) AS DATA,
               COALESCE(F.FANTASIA,'SEM FANTASIA') AS FORNECEDOR,
               COALESCE(P.DESCRICAO, 'Produto ' || M.CODPROD) AS PRODUTO,
               M.QT, (M.PUNIT*M.QT) AS VALOR
        FROM {schema}.PCMOV M
        JOIN {schema}.PCCLIENT C ON M.CODCLI = C.CODCLI
        JOIN {schema}.PCFORNEC F ON M.CODFORNEC = F.CODFORNEC
        LEFT JOIN {schema}.PCPRODUT P ON M.CODPROD = P.CODPROD
        LEFT JOIN {schema}.PCUSUARI U1 ON C.CODUSUR1 = U1.CODUSUR
        LEFT JOIN {schema}.PCUSUARI U2 ON C.CODUSUR2 = U2.CODUSUR
        WHERE (U1.NOME LIKE '%OFF TRADE%' OR U2.NOME LIKE '%OFF TRADE%')
          {fil_clause}
          AND M.CODOPER = 'S'
          AND M.NUMNOTADEV IS NULL
          AND M.DTCANCEL IS NULL
          AND TRUNC(M.DTMOV) >= TO_DATE('{MES_INI}','YYYY-MM-DD')
          AND TRUNC(M.DTMOV) <= TO_DATE('{MES_FIM}','YYYY-MM-DD')
        ORDER BY M.CODCLI, TRUNC(M.DTMOV) DESC
    """


_vend_partes, _cli_partes, _vendas_partes, _hist_partes = [], [], [], []
fontes_indisponiveis = []

for base in BASES:
    estado, eng, schema, filiais = base["estado"], base["engine"], base["schema"], base["filiais"]
    try:
        v = carregar_dados(_query_vendedores(schema, estado), eng, f"raiox_clidet_vendedores_{estado}")
        v.columns = v.columns.str.upper()
        v['ESTADO'] = estado
        _vend_partes.append(v)

        c = carregar_dados(_query_clientes(schema, estado), eng, f"raiox_clidet_base_{estado}")
        c.columns = c.columns.str.upper()
        c['ESTADO'] = estado
        _cli_partes.append(c)

        vd = carregar_dados(_query_vendas(schema, filiais), eng, f"raiox_clidet_vendas_{estado}")
        vd.columns = vd.columns.str.upper()
        vd['ESTADO'] = estado
        _vendas_partes.append(vd)

        h = carregar_dados(_query_historico(schema, filiais), eng, f"raiox_clidet_historico_{estado}")
        h.columns = h.columns.str.upper()
        h['ESTADO'] = estado
        _hist_partes.append(h)
        print(f"  OK {estado}: {len(c)} clientes, {len(vd)} linhas venda, {len(h)} linhas histórico")
    except Exception as e:
        print(f"  [AVISO] {estado} falhou ({str(e)[:150]}) — ignorado")
        fontes_indisponiveis.append(estado)

vendedores_off_trade = pd.concat(_vend_partes, ignore_index=True) if _vend_partes else pd.DataFrame(columns=['CODUSUR', 'NOME', 'SUPERVISOR', 'GERENTE', 'ESTADO'])
_nome_por_chave = {
    (r['ESTADO'], int(r['CODUSUR'])): r['NOME'].replace('- OFF TRADE', '').replace('-OFF TRADE', '').strip()
    for _, r in vendedores_off_trade.iterrows()
}
_hier_por_chave = {
    (r['ESTADO'], int(r['CODUSUR'])): {'supervisor': r['SUPERVISOR'], 'gerente': r['GERENTE']}
    for _, r in vendedores_off_trade.iterrows()
}

clientes = pd.concat(_cli_partes, ignore_index=True) if _cli_partes else pd.DataFrame(
    columns=['CODCLI', 'CLIENTE', 'FANTASIA', 'CIDADE', 'RAMO', 'CODUSUR1', 'CODUSUR2', 'DTULTCOMP', 'REDE', 'ESTADO'])
clientes['CIDADE']   = clientes['CIDADE'].fillna('').str.strip()
clientes['RAMO']     = clientes['RAMO'].fillna('OUTROS').str.strip()
clientes['FANTASIA'] = clientes['FANTASIA'].fillna('').str.strip()
clientes['CLIENTE']  = clientes['CLIENTE'].fillna('').str.strip()
clientes['REDE']     = clientes['REDE'].fillna('').str.strip()
for col in ('CODUSUR1', 'CODUSUR2'):
    clientes[col] = clientes[col].apply(lambda v: int(v) if str(v).strip().replace('.0', '').isdigit() else None)
clientes['CLIENTE_KEY'] = clientes['ESTADO'] + '-' + clientes['CODCLI'].astype(str)

vendas = pd.concat(_vendas_partes, ignore_index=True) if _vendas_partes else pd.DataFrame(
    columns=['CODCLI', 'MES', 'FORNECEDOR', 'CODUSUR', 'FATURAMENTO', 'ESTADO'])
vendas['MES'] = pd.to_datetime(vendas['MES'])
vendas['FORNECEDOR'] = vendas['FORNECEDOR'].fillna('SEM FANTASIA').str.strip()
vendas['CODUSUR'] = vendas['CODUSUR'].apply(lambda v: int(v) if str(v).strip().replace('.0', '').isdigit() else None)
vendas['CLIENTE_KEY'] = vendas['ESTADO'] + '-' + vendas['CODCLI'].astype(str)

meses_com_dado = sorted(vendas['MES'].dt.strftime('%Y-%m').unique())
clientes_com_venda = set(vendas['CLIENTE_KEY'].unique())

historico = pd.concat(_hist_partes, ignore_index=True) if _hist_partes else pd.DataFrame(
    columns=['CODCLI', 'DATA', 'FORNECEDOR', 'PRODUTO', 'QT', 'VALOR', 'ESTADO'])
historico['DATA'] = pd.to_datetime(historico['DATA'])
historico['FORNECEDOR'] = historico['FORNECEDOR'].fillna('SEM FANTASIA').str.strip()
historico['PRODUTO'] = historico['PRODUTO'].fillna('').str.strip()
historico['CLIENTE_KEY'] = historico['ESTADO'] + '-' + historico['CODCLI'].astype(str)
_historico_por_cliente = {
    chave: [
        {
            'data': r['DATA'].strftime('%d/%m/%Y'),
            'industria': r['FORNECEDOR'],
            'produto': r['PRODUTO'],
            'quantidade': float(r['QT']),
            'valor': round(float(r['VALOR']), 2),
        }
        for _, r in grp.iterrows()
    ]
    for chave, grp in historico.groupby('CLIENTE_KEY')
}

def _ciclo_e_risco(chave: str, hist_cliente: list) -> dict:
    """Ciclo médio de recompra (dias entre compras) e sinal de queda recente,
    ambos calculados a partir das datas reais de compra (historico_compras),
    não do calendário — evita viés de mês corrente incompleto."""
    datas = sorted({
        datetime.strptime(h['data'], '%d/%m/%Y').date()
        for h in hist_cliente
    })
    resultado = {
        'ciclo_medio_dias': None,
        'dias_desde_ultima_compra': None,
        'atrasado_recompra': False,
        'em_risco_queda': False,
        'queda_pct': 0.0,
        'faturamento_30d': 0.0,
        'faturamento_30_60d': 0.0,
    }
    if not datas:
        return resultado

    dias_desde_ultima = (HOJE - datas[-1]).days
    resultado['dias_desde_ultima_compra'] = dias_desde_ultima

    if len(datas) >= MIN_COMPRAS_CICLO_CONFIAVEL:
        gaps = [(b - a).days for a, b in zip(datas[:-1], datas[1:])]
        ciclo_medio = sum(gaps) / len(gaps)
        resultado['ciclo_medio_dias'] = round(ciclo_medio, 1)
        resultado['atrasado_recompra'] = dias_desde_ultima > max(ciclo_medio * 1.5, 30)

    janela_recente = HOJE - timedelta(days=30)
    janela_anterior = HOJE - timedelta(days=60)
    fat_recente = sum(h['valor'] for h in hist_cliente
                       if datetime.strptime(h['data'], '%d/%m/%Y').date() > janela_recente)
    fat_anterior = sum(h['valor'] for h in hist_cliente
                        if janela_anterior < datetime.strptime(h['data'], '%d/%m/%Y').date() <= janela_recente)
    resultado['faturamento_30d'] = round(fat_recente, 2)
    resultado['faturamento_30_60d'] = round(fat_anterior, 2)
    if fat_anterior >= MIN_FATURAMENTO_RISCO and fat_recente < fat_anterior * 0.6:
        resultado['em_risco_queda'] = True
        resultado['queda_pct'] = round((1 - fat_recente / fat_anterior) * 100, 1)

    return resultado


registros = []
for _, c in clientes.iterrows():
    chave = c['CLIENTE_KEY']
    estado = c['ESTADO']
    v_cli = vendas[vendas['CLIENTE_KEY'] == chave]

    fat_ytd = float(v_cli['FATURAMENTO'].sum())
    n_meses = v_cli['MES'].nunique() or 1
    media_mensal = fat_ytd / n_meses if not v_cli.empty else 0.0

    por_mes = {
        d.strftime('%Y-%m'): round(float(f), 2)
        for d, f in v_cli.groupby('MES')['FATURAMENTO'].sum().items()
    }

    por_industria = (
        v_cli.groupby('FORNECEDOR')['FATURAMENTO'].sum()
        .sort_values(ascending=False)
    )
    top_industrias = [
        {'fantasia': f, 'faturamento': round(float(v), 2),
         'pct': round(float(v) / fat_ytd * 100, 1) if fat_ytd else 0.0}
        for f, v in por_industria.items()
    ]

    por_vendedor_s = v_cli.groupby('CODUSUR')['FATURAMENTO'].sum().sort_values(ascending=False)
    top_vendedores = [
        {'rca': int(rca), 'nome': _nome_por_chave.get((estado, int(rca)), f"RCA {int(rca)}"), 'faturamento': round(float(v), 2)}
        for rca, v in por_vendedor_s.items() if pd.notna(rca)
    ]

    vendedores_cadastro = [
        {'rca': rca, 'nome': _nome_por_chave[(estado, rca)]}
        for rca in (c['CODUSUR1'], c['CODUSUR2'])
        if (estado, rca) in _nome_por_chave
    ]

    # Hierarquia do cliente = a do vendedor principal (CODUSUR1), com
    # fallback pro CODUSUR2 — usada nos filtros em cascata da página.
    _hier_cliente = None
    for rca in (c['CODUSUR1'], c['CODUSUR2']):
        if (estado, rca) in _hier_por_chave:
            _hier_cliente = _hier_por_chave[(estado, rca)]
            break
    gerente = _hier_cliente['gerente'] if _hier_cliente else 'Sem gerente'
    supervisor = _hier_cliente['supervisor'] if _hier_cliente else 'Sem supervisor'

    ultima_compra = ''
    if pd.notna(c.get('DTULTCOMP')):
        try:
            ultima_compra = pd.to_datetime(c['DTULTCOMP']).strftime('%d/%m/%Y')
        except Exception:
            ultima_compra = ''

    hist_cliente = _historico_por_cliente.get(chave, [])
    ciclo_risco = _ciclo_e_risco(chave, hist_cliente)

    registros.append({
        'codcli': int(c['CODCLI']),
        'estado': estado,
        'chave': chave,
        'nome': c['FANTASIA'] or c['CLIENTE'] or f"Cliente {c['CODCLI']}",
        'razao_social': c['CLIENTE'],
        'cidade': c['CIDADE'] or 'N/D',
        'ramo': c['RAMO'],
        'rede': c['REDE'] or 'Sem rede',
        'gerente': gerente,
        'supervisor': supervisor,
        'vendedores_cadastro': vendedores_cadastro,
        'ultima_compra': ultima_compra,
        'ativo_periodo': chave in clientes_com_venda,
        'faturamento_ytd': round(fat_ytd, 2),
        'media_mensal': round(media_mensal, 2),
        'por_mes': por_mes,
        'top_industrias': top_industrias,
        'top_vendedores': top_vendedores,
        'historico_compras': hist_cliente,
        'ciclo_medio_dias': ciclo_risco['ciclo_medio_dias'],
        'dias_desde_ultima_compra': ciclo_risco['dias_desde_ultima_compra'],
        'atrasado_recompra': ciclo_risco['atrasado_recompra'],
        'em_risco_queda': ciclo_risco['em_risco_queda'],
        'queda_pct': ciclo_risco['queda_pct'],
        'faturamento_30d': ciclo_risco['faturamento_30d'],
        'faturamento_30_60d': ciclo_risco['faturamento_30_60d'],
        'prioridade_visita_score': round(media_mensal * (1 + (ciclo_risco['dias_desde_ultima_compra'] or 0) / 30), 2),
    })

# ── Recomendações de produto (cross-sell) ────────────────────────────────
# Pra cada grupo (ramo, estado), calcula a penetração de cada fornecedor
# (% de clientes do grupo que compram) e o ticket médio de quem compra.
# Cada cliente recebe, como recomendação, os fornecedores mais penetrantes
# no seu grupo que ele ainda NÃO compra — um "o que meus vizinhos compram
# que eu não vendo pra esse cliente ainda".
_grupos: dict[tuple, list] = {}
for r in registros:
    _grupos.setdefault((r['ramo'], r['estado']), []).append(r)

for (ramo, estado), membros in _grupos.items():
    total_grupo = len(membros)
    fornecedor_compradores: dict[str, list] = {}
    for r in membros:
        for ind in r['top_industrias']:
            fornecedor_compradores.setdefault(ind['fantasia'], []).append(ind['faturamento'])

    ranking_grupo = []
    for fornecedor, valores in fornecedor_compradores.items():
        if fornecedor == 'SEM FANTASIA':
            continue
        n_compradores = len(valores)
        if n_compradores < MIN_COMPRADORES_RECOMENDACAO:
            continue
        ranking_grupo.append({
            'fornecedor': fornecedor,
            'penetracao_pct': round(n_compradores / total_grupo * 100, 1),
            'ticket_medio': round(sum(valores) / n_compradores, 2),
        })
    ranking_grupo.sort(key=lambda x: x['penetracao_pct'], reverse=True)

    for r in membros:
        comprados = {ind['fantasia'] for ind in r['top_industrias']}
        r['recomendacoes'] = [
            item for item in ranking_grupo if item['fornecedor'] not in comprados
        ][:5]

registros.sort(key=lambda r: r['nome'])

payload = {
    'atualizado_em': datetime.now().strftime('%d/%m/%Y %H:%M'),
    'meses_com_dado': meses_com_dado,
    'clientes': registros,
    'fontes_indisponiveis': fontes_indisponiveis,
}

out_path = Path(__file__).parent / "raiox_cliente_detalhe_data.js"
with open(out_path, 'w', encoding='utf-8') as f:
    f.write(f"// Gerado automaticamente\nconst RAIOX_CLIENTE_DETALHE_DATA = {json.dumps(payload, ensure_ascii=False, indent=2)};\n")

print(f"OK raiox_cliente_detalhe_data.js — {len(registros)} clientes")
if fontes_indisponiveis:
    print(f"[AVISO] Fontes indisponíveis: {fontes_indisponiveis}")

repo_dir = str(Path(__file__).parent)
subprocess.run(["git", "-C", repo_dir, "add", "raiox_cliente_detalhe_data.js", "raiox_cliente_detalhe.html"], check=False)
subprocess.run(["git", "-C", repo_dir, "commit", "-m",
                f"Atualiza raiox_cliente_detalhe_data.js - {date.today().strftime('%d/%m/%Y')}"])
subprocess.run(["git", "-C", repo_dir, "push", "origin", "master"], check=False)
print("OK GitHub Pages atualizado.")
