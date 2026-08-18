"""
Gera raiox_oportunidades_data.js — ranking de quedas de faturamento e
quantidade por CLIENTE, INDÚSTRIA (fornecedor) e SKU (produto), em todas as
bases OFF TRADE (RJ e ES via CRC, SP via SPON, MG via MGON), comparando o
período corrente do mês (do dia 1 até hoje) contra o mesmo intervalo de dias
do mês anterior e do mesmo mês no ano anterior — pedido do usuário em
2026-08-16: "mapear quais clientes/indústrias/sku tivemos as maiores quedas,
comparativo vs julho e vs ano anterior", pra cada gerente cobrar o time na
segunda quinzena do mês. Os períodos são calculados dinamicamente a partir
da data de execução (sempre dia 1 até o dia corrente), então o script não
precisa de edição manual mês a mês — diferente dos raiox_*.py mais antigos
(ANO/MES_FIM fixos).
"""
import calendar
import json
from datetime import date, datetime
from pathlib import Path

import pandas as pd

from meta import engine, engine_spon, engine_mgon, engine_blended, carregar_dados
from utils import git_commit_push

HOJE = date.today()
DIA_CORTE = HOJE.day

# Faturamento mínimo (nos 3 períodos somados) pra uma linha entrar no
# ranking — evita poluir a lista com clientes/skus muito pequenos onde uma
# variação de R$20 vira "queda de 90%".
MIN_FATURAMENTO_RELEVANTE = 100.0

# filtra_por_estado=False: schema inteiro já é considerado daquele estado
# (BLENDED, sem PCUSUARI.ESTADO preenchido — mesmo caso de
# exportacao_sp.py/exportacao_meta.py) — filtrar por ESTADO='SP' aqui
# zerava a base inteira.
BASES = [
    {"estado": "RJ", "engine": engine,         "schema": "CRC",     "filiais": ["2", "4"], "filtra_por_estado": True},
    {"estado": "ES", "engine": engine,         "schema": "CRC",     "filiais": ["1"],      "filtra_por_estado": True},
    {"estado": "SP", "engine": engine_spon,    "schema": "SPON",    "filiais": ["1", "2"], "filtra_por_estado": True},
    {"estado": "SP", "engine": engine_blended, "schema": "BLENDED", "filiais": None,       "filtra_por_estado": False},
    {"estado": "MG", "engine": engine_mgon,    "schema": "MGON",    "filiais": ["1", "2"], "filtra_por_estado": True},
]


def _periodo(ano: int, mes: int, dia_corte: int) -> tuple[date, date]:
    dia_fim = min(dia_corte, calendar.monthrange(ano, mes)[1])
    return date(ano, mes, 1), date(ano, mes, dia_fim)


_ano_atual, _mes_atual_num = HOJE.year, HOJE.month
if _mes_atual_num == 1:
    _ano_mes_ant, _mes_ant_num = _ano_atual - 1, 12
else:
    _ano_mes_ant, _mes_ant_num = _ano_atual, _mes_atual_num - 1

PERIODO_ATUAL = _periodo(_ano_atual, _mes_atual_num, DIA_CORTE)
PERIODO_MES_ANTERIOR = _periodo(_ano_mes_ant, _mes_ant_num, DIA_CORTE)
PERIODO_ANO_ANTERIOR = _periodo(_ano_atual - 1, _mes_atual_num, DIA_CORTE)

_PERIODO_POR_ANOMES = {
    (_ano_atual, _mes_atual_num): 'atual',
    (_ano_mes_ant, _mes_ant_num): 'mes_anterior',
    (_ano_atual - 1, _mes_atual_num): 'ano_anterior',
}

_MESES_PT = ['', 'Jan', 'Fev', 'Mar', 'Abr', 'Mai', 'Jun', 'Jul', 'Ago', 'Set', 'Out', 'Nov', 'Dez']


def _label_periodo(ini: date, fim: date) -> str:
    return f"{ini.strftime('%d/%m')} a {fim.strftime('%d/%m/%Y')}"


def _dt(d: date) -> str:
    return d.strftime('%Y-%m-%d')


_FILTRO_DATAS = f"""(
       (TRUNC(M.DTMOV) BETWEEN TO_DATE('{_dt(PERIODO_ATUAL[0])}','YYYY-MM-DD') AND TO_DATE('{_dt(PERIODO_ATUAL[1])}','YYYY-MM-DD'))
    OR (TRUNC(M.DTMOV) BETWEEN TO_DATE('{_dt(PERIODO_MES_ANTERIOR[0])}','YYYY-MM-DD') AND TO_DATE('{_dt(PERIODO_MES_ANTERIOR[1])}','YYYY-MM-DD'))
    OR (TRUNC(M.DTMOV) BETWEEN TO_DATE('{_dt(PERIODO_ANO_ANTERIOR[0])}','YYYY-MM-DD') AND TO_DATE('{_dt(PERIODO_ANO_ANTERIOR[1])}','YYYY-MM-DD'))
)"""


def _query_vendedores(schema, estado, filtra_por_estado=True):
    estado_clause = f"AND U.ESTADO = '{estado}'" if filtra_por_estado else ""
    return f"""
        SELECT U.CODUSUR, U.NOME,
               COALESCE(S.NOME, 'Sem supervisor') AS SUPERVISOR,
               COALESCE(G.NOMEGERENTE, 'Sem gerente') AS GERENTE
        FROM {schema}.PCUSUARI U
        LEFT JOIN {schema}.PCSUPERV S ON U.CODSUPERVISOR = S.CODSUPERVISOR
        LEFT JOIN {schema}.PCGERENTE G ON S.CODGERENTE = G.CODGERENTE
        WHERE U.NOME LIKE '%OFF TRADE%' {estado_clause}
    """


def _query_vendas(schema, filiais, estado, filtra_por_estado=True):
    fil_clause = f"AND M.CODFILIAL IN ({','.join(filiais)})" if filiais else ""
    estado_clause = f"AND (U1.ESTADO = '{estado}' OR U2.ESTADO = '{estado}')" if filtra_por_estado else ""
    return f"""
        SELECT M.CODCLI, C.CLIENTE, COALESCE(C.FANTASIA, C.CLIENTE) AS NOME_CLIENTE,
               COALESCE(C.BAIRROENT,'') AS BAIRRO, COALESCE(C.CGCENT,'') AS CNPJ,
               COALESCE(A.RAMO,'OUTROS') RAMO, TRUNC(M.DTMOV,'MM') AS MES,
               COALESCE(F.FANTASIA,'SEM FANTASIA') AS FORNECEDOR,
               COALESCE(M.DESCRICAO, 'Produto ' || M.CODPROD) AS PRODUTO,
               C.CODUSUR1, C.CODUSUR2,
               SUM(M.QT) AS QTD, SUM(M.PUNIT*M.QT) AS FATURAMENTO
        FROM {schema}.PCMOV M
        JOIN {schema}.PCCLIENT C ON M.CODCLI = C.CODCLI
        JOIN {schema}.PCATIVI A ON C.CODATV1 = A.CODATIV
        JOIN {schema}.PCFORNEC F ON M.CODFORNEC = F.CODFORNEC
        LEFT JOIN {schema}.PCUSUARI U1 ON C.CODUSUR1 = U1.CODUSUR
        LEFT JOIN {schema}.PCUSUARI U2 ON C.CODUSUR2 = U2.CODUSUR
        WHERE (U1.NOME LIKE '%OFF TRADE%' OR U2.NOME LIKE '%OFF TRADE%')
          {estado_clause}
          {fil_clause}
          AND M.CODOPER = 'S'
          AND M.NUMNOTADEV IS NULL
          AND M.DTCANCEL IS NULL
          AND {_FILTRO_DATAS}
        GROUP BY M.CODCLI, C.CLIENTE, COALESCE(C.FANTASIA, C.CLIENTE), COALESCE(C.BAIRROENT,''), COALESCE(C.CGCENT,''),
                 COALESCE(A.RAMO,'OUTROS'), TRUNC(M.DTMOV,'MM'), COALESCE(F.FANTASIA,'SEM FANTASIA'),
                 COALESCE(M.DESCRICAO, 'Produto ' || M.CODPROD), C.CODUSUR1, C.CODUSUR2
    """


_vend_partes, _vendas_partes = [], []
fontes_indisponiveis = []

for base in BASES:
    estado, eng, schema, filiais = base["estado"], base["engine"], base["schema"], base["filiais"]
    filtra_por_estado = base["filtra_por_estado"]
    try:
        v = carregar_dados(_query_vendedores(schema, estado, filtra_por_estado), eng, f"oport_vendedores_{estado}_{schema}")
        v.columns = v.columns.str.upper()
        v['ESTADO'] = estado
        v['SCHEMA'] = schema
        _vend_partes.append(v)

        vd = carregar_dados(_query_vendas(schema, filiais, estado, filtra_por_estado), eng, f"oport_vendas_{estado}_{schema}")
        vd.columns = vd.columns.str.upper()
        vd['ESTADO'] = estado
        vd['SCHEMA'] = schema
        _vendas_partes.append(vd)
        print(f"  OK {estado}/{schema}: {len(vd)} linhas")
    except Exception as e:
        print(f"  [AVISO] {estado}/{schema} falhou ({str(e)[:150]}) — ignorado")
        fontes_indisponiveis.append(f"{estado}/{schema}")

# CODUSUR/CODCLI são chaves surrogate de cada instância Oracle — SP tem duas
# fontes independentes (SPON e BLENDED) cuja numeração colide quase inteira
# (confirmado 2026-08-18: 42977/44100 CODCLI e 15/15 CODUSUR em comum). Sem
# incluir SCHEMA na chave, um CODCLI/CODUSUR de BLENDED se misturava com um
# CODUSUR/CODCLI não relacionado do SPON — cliente/vendedor errado no ranking.
vendedores_off_trade = pd.concat(_vend_partes, ignore_index=True) if _vend_partes else pd.DataFrame(columns=['CODUSUR', 'NOME', 'SUPERVISOR', 'GERENTE', 'ESTADO', 'SCHEMA'])
_nome_por_chave = {
    (r['ESTADO'], r['SCHEMA'], int(r['CODUSUR'])): r['NOME'].replace('- OFF TRADE', '').replace('-OFF TRADE', '').strip()
    for _, r in vendedores_off_trade.iterrows()
}
_hier_por_chave = {
    (r['ESTADO'], r['SCHEMA'], int(r['CODUSUR'])): {'supervisor': r['SUPERVISOR'], 'gerente': r['GERENTE']}
    for _, r in vendedores_off_trade.iterrows()
}

vendas = pd.concat(_vendas_partes, ignore_index=True) if _vendas_partes else pd.DataFrame(
    columns=['CODCLI', 'CLIENTE', 'NOME_CLIENTE', 'BAIRRO', 'CNPJ', 'RAMO', 'MES', 'FORNECEDOR', 'PRODUTO',
             'CODUSUR1', 'CODUSUR2', 'QTD', 'FATURAMENTO', 'ESTADO', 'SCHEMA'])
vendas['MES'] = pd.to_datetime(vendas['MES'])
vendas['RAMO'] = vendas['RAMO'].fillna('OUTROS').str.strip()
vendas['FORNECEDOR'] = vendas['FORNECEDOR'].fillna('SEM FANTASIA').str.strip()
vendas['PRODUTO'] = vendas['PRODUTO'].fillna('').str.strip()
vendas['NOME_CLIENTE'] = vendas['NOME_CLIENTE'].fillna('').str.strip()
vendas['BAIRRO'] = vendas['BAIRRO'].fillna('').str.strip()
vendas['CNPJ'] = vendas['CNPJ'].fillna('').astype(str).str.strip()
# SPON grava CGCENT formatado com pontuação ("46.443.440/0001-14", 18
# caracteres) enquanto BLENDED/CRC gravam só os dígitos (14) — confirmado
# 2026-08-18. Sem remover a pontuação, nenhum CNPJ do SPON batia com o
# equivalente do BLENDED (comparação de string pura sempre falhava,
# silenciosamente caindo no fallback pra todo cliente do SPON).
vendas['CNPJ'] = vendas['CNPJ'].str.replace(r'\D', '', regex=True)
for col in ('CODUSUR1', 'CODUSUR2'):
    vendas[col] = vendas[col].apply(lambda v: int(v) if str(v).strip().replace('.0', '').isdigit() else None)

if len(fontes_indisponiveis) == len(BASES):
    print(f"[ERRO] Todas as {len(BASES)} bases falharam ({fontes_indisponiveis}) — nada foi gerado. Verifique a VPN/conexão Oracle e rode novamente.")
    raise SystemExit(1)

# Colunas de string vêm como dtype Arrow ("string"/"null") deste pandas —
# concatenar Series vazia (dtype null, quando uma base falha) com um literal
# Python quebra (ArrowNotImplementedError). astype(object) força texto puro
# e evita o crash independente de quantas bases tiverem dado certo.
# Cliente identificado por CNPJ (14 dígitos, mesmo padrão de
# exportacao_clientes_rca.py::_CNPJ14) quando disponível — assim um mesmo
# cliente real cadastrado tanto no SPON quanto no BLENDED (mesmo CNPJ, CODCLI
# diferente em cada base) entra como UM só no ranking, com faturamento
# somado. Sem CNPJ válido, cai no fallback schema-qualificado (evita
# colisão de CODCLI entre bases independentes, ver comentário acima).
_cnpj14 = vendas['CNPJ'].where(vendas['CNPJ'].str.len() == 14, '')
vendas['CLIENTE_KEY'] = vendas['ESTADO'].astype(object) + '|' + _cnpj14.where(
    _cnpj14 != '', vendas['SCHEMA'].astype(object) + '#' + vendas['CODCLI'].astype(str)
)
vendas['PERIODO'] = vendas['MES'].apply(lambda d: _PERIODO_POR_ANOMES.get((d.year, d.month)))
vendas = vendas[vendas['PERIODO'].notna()].copy()

rcas_com_venda = sorted(
    {(estado, schema, rca) for estado, schema, rca in
     list(zip(vendas['ESTADO'], vendas['SCHEMA'], vendas['CODUSUR1'])) + list(zip(vendas['ESTADO'], vendas['SCHEMA'], vendas['CODUSUR2']))
     if pd.notna(rca)}
    & set(_nome_por_chave)
)

vendedores_lista = sorted(
    ({'rca': rca, 'estado': estado, 'chave': f"{estado}-{schema}-{rca}",
      'nome': _nome_por_chave.get((estado, schema, rca), f"RCA {rca}"),
      'supervisor': _hier_por_chave.get((estado, schema, rca), {}).get('supervisor', 'Sem supervisor'),
      'gerente': _hier_por_chave.get((estado, schema, rca), {}).get('gerente', 'Sem gerente')}
     for estado, schema, rca in rcas_com_venda),
    key=lambda v: v['nome']
)


def _var_pct(atual: float, base_valor: float):
    if base_valor <= 0:
        return None
    return round((atual - base_valor) / base_valor * 100, 1)


def _metricas_periodo(df: pd.DataFrame) -> dict:
    fat = df.groupby('PERIODO')['FATURAMENTO'].sum().to_dict()
    qtd = df.groupby('PERIODO')['QTD'].sum().to_dict()
    fat_atual, fat_mes_ant, fat_ano_ant = float(fat.get('atual', 0)), float(fat.get('mes_anterior', 0)), float(fat.get('ano_anterior', 0))
    qt_atual, qt_mes_ant, qt_ano_ant = float(qtd.get('atual', 0)), float(qtd.get('mes_anterior', 0)), float(qtd.get('ano_anterior', 0))
    return {
        'fat_atual': round(fat_atual, 2), 'fat_mes_anterior': round(fat_mes_ant, 2), 'fat_ano_anterior': round(fat_ano_ant, 2),
        'qt_atual': round(qt_atual, 2), 'qt_mes_anterior': round(qt_mes_ant, 2), 'qt_ano_anterior': round(qt_ano_ant, 2),
        'queda_fat_mes_valor': round(fat_mes_ant - fat_atual, 2), 'queda_fat_ano_valor': round(fat_ano_ant - fat_atual, 2),
        'queda_fat_mes_pct': _var_pct(fat_atual, fat_mes_ant), 'queda_fat_ano_pct': _var_pct(fat_atual, fat_ano_ant),
        'queda_qt_mes_valor': round(qt_mes_ant - qt_atual, 2), 'queda_qt_ano_valor': round(qt_ano_ant - qt_atual, 2),
        'queda_qt_mes_pct': _var_pct(qt_atual, qt_mes_ant), 'queda_qt_ano_pct': _var_pct(qt_atual, qt_ano_ant),
    }


def _por_vendedor(df: pd.DataFrame) -> dict:
    """Métricas cruas (soma por período) por chave 'ESTADO-RCA', dono do
    cliente (CODUSUR1/2) — pra alimentar os filtros em cascata client-side
    (a página soma os brutos do recorte e recalcula % lá, igual
    raiox_industrias.html::metricasMescladas)."""
    resultado = {}
    for estado, schema, rca in rcas_com_venda:
        sub = df[(df['ESTADO'] == estado) & (df['SCHEMA'] == schema) & ((df['CODUSUR1'] == rca) | (df['CODUSUR2'] == rca))]
        if sub.empty:
            continue
        fat = sub.groupby('PERIODO')['FATURAMENTO'].sum().to_dict()
        qtd = sub.groupby('PERIODO')['QTD'].sum().to_dict()
        resultado[f"{estado}-{schema}-{rca}"] = {
            'fat_atual': round(float(fat.get('atual', 0)), 2),
            'fat_mes_anterior': round(float(fat.get('mes_anterior', 0)), 2),
            'fat_ano_anterior': round(float(fat.get('ano_anterior', 0)), 2),
            'qt_atual': round(float(qtd.get('atual', 0)), 2),
            'qt_mes_anterior': round(float(qtd.get('mes_anterior', 0)), 2),
            'qt_ano_anterior': round(float(qtd.get('ano_anterior', 0)), 2),
        }
    return resultado


def _relevante(m: dict) -> bool:
    return max(m['fat_atual'], m['fat_mes_anterior'], m['fat_ano_anterior']) >= MIN_FATURAMENTO_RELEVANTE


# ── Ranking por CLIENTE ──────────────────────────────────────────────────
clientes = []
for chave, grp in vendas.groupby('CLIENTE_KEY'):
    primeira = grp.iloc[0]
    m = _metricas_periodo(grp)
    if not _relevante(m):
        continue
    estado = primeira['ESTADO']
    # Cliente com CNPJ igual em SPON e BLENDED cai no mesmo grupo (mesma
    # CLIENTE_KEY) mas com linhas de schemas diferentes — não dá pra pegar
    # RCA só da primeira linha, senão o vendedor da outra base some.
    _candidatos_rca = {
        (row['ESTADO'], row['SCHEMA'], rca)
        for _, row in grp[['ESTADO', 'SCHEMA', 'CODUSUR1', 'CODUSUR2']].drop_duplicates().iterrows()
        for rca in (row['CODUSUR1'], row['CODUSUR2']) if rca is not None
    }
    _hier = None
    _vendedor_nome = None
    for chave_rca in _candidatos_rca:
        if chave_rca in _hier_por_chave:
            _hier = _hier_por_chave[chave_rca]
            _vendedor_nome = _nome_por_chave.get(chave_rca)
            break
    chaves_rca = [f"{est}-{sch}-{rca}" for est, sch, rca in _candidatos_rca if (est, sch, rca) in _hier_por_chave]

    # Abertura por indústria (e, dentro dela, por produto) do próprio cliente
    # — alimenta o drill-down cliente -> indústria -> produto na página.
    por_industria = []
    for fornecedor, grp_f in grp.groupby('FORNECEDOR'):
        por_produto = [
            {'produto': produto, **_metricas_periodo(grp_p)}
            for produto, grp_p in grp_f.groupby('PRODUTO')
        ]
        por_produto.sort(key=lambda x: x['queda_fat_mes_valor'], reverse=True)
        por_industria.append({'fornecedor': fornecedor, 'por_produto': por_produto, **_metricas_periodo(grp_f)})
    por_industria.sort(key=lambda x: x['queda_fat_mes_valor'], reverse=True)

    # 'chave' de exibição fica no formato antigo (ESTADO-CODCLI, sem SCHEMA)
    # pra não quebrar o link "Ver ficha" -> raiox_cliente_detalhe.html, que
    # espera esse formato e não conhece BLENDED. Só quando um CODCLI do
    # BLENDED coincidir com um CODCLI de cliente relevante do SPON (raro,
    # já que o corte de relevância filtra a maioria) essa chave de exibição
    # pode colidir entre dois clientes reais diferentes — o agrupamento
    # interno (CLIENTE_KEY, com SCHEMA) já garante que os números de cada um
    # não se misturam, só a navegação "Ver ficha"/estado de expansão da
    # tabela que pode ambiguar nesse caso raro.
    clientes.append({
        'codcli': int(primeira['CODCLI']), 'estado': estado, 'chave': f"{estado}-{int(primeira['CODCLI'])}",
        'nome': primeira['NOME_CLIENTE'] or primeira['CLIENTE'] or f"Cliente {primeira['CODCLI']}",
        'bairro': primeira['BAIRRO'] or '',
        'ramo': primeira['RAMO'],
        'vendedor': _vendedor_nome or 'Sem vendedor',
        'gerente': _hier['gerente'] if _hier else 'Sem gerente',
        'supervisor': _hier['supervisor'] if _hier else 'Sem supervisor',
        'chaves_rca': chaves_rca,
        'por_industria': por_industria,
        **m,
    })
clientes.sort(key=lambda c: c['queda_fat_mes_valor'], reverse=True)

# ── Ranking por INDÚSTRIA (fornecedor, aberto por estado) ───────────────
industrias = []
for (estado, fornecedor), grp in vendas.groupby(['ESTADO', 'FORNECEDOR']):
    m = _metricas_periodo(grp)
    if not _relevante(m):
        continue
    industrias.append({
        'fornecedor': fornecedor, 'estado': estado,
        **m,
        'por_vendedor': _por_vendedor(grp),
    })
industrias.sort(key=lambda f: f['queda_fat_mes_valor'], reverse=True)

# ── Ranking por SKU (produto + fornecedor, aberto por estado) ───────────
skus = []
for (estado, fornecedor, produto), grp in vendas.groupby(['ESTADO', 'FORNECEDOR', 'PRODUTO']):
    m = _metricas_periodo(grp)
    if not _relevante(m):
        continue
    skus.append({
        'produto': produto, 'fornecedor': fornecedor, 'estado': estado,
        **m,
        'por_vendedor': _por_vendedor(grp),
    })
skus.sort(key=lambda s: s['queda_fat_mes_valor'], reverse=True)

payload = {
    'atualizado_em': datetime.now().strftime('%d/%m/%Y %H:%M'),
    'periodo_atual': {'inicio': PERIODO_ATUAL[0].strftime('%d/%m/%Y'), 'fim': PERIODO_ATUAL[1].strftime('%d/%m/%Y'), 'label': _label_periodo(*PERIODO_ATUAL)},
    'periodo_mes_anterior': {'inicio': PERIODO_MES_ANTERIOR[0].strftime('%d/%m/%Y'), 'fim': PERIODO_MES_ANTERIOR[1].strftime('%d/%m/%Y'), 'label': _label_periodo(*PERIODO_MES_ANTERIOR)},
    'periodo_ano_anterior': {'inicio': PERIODO_ANO_ANTERIOR[0].strftime('%d/%m/%Y'), 'fim': PERIODO_ANO_ANTERIOR[1].strftime('%d/%m/%Y'), 'label': _label_periodo(*PERIODO_ANO_ANTERIOR)},
    'vendedores': vendedores_lista,
    'clientes': clientes,
    'industrias': industrias,
    'skus': skus,
    'fontes_indisponiveis': fontes_indisponiveis,
}

out_path = Path(__file__).parent / "raiox_oportunidades_data.js"
with open(out_path, 'w', encoding='utf-8') as f:
    f.write(f"// Gerado automaticamente\nconst RAIOX_OPORTUNIDADES_DATA = {json.dumps(payload, ensure_ascii=False, indent=2)};\n")

print(f"OK raiox_oportunidades_data.js — {len(clientes)} clientes, {len(industrias)} linhas indústria, {len(skus)} linhas sku")
print(f"Período atual: {payload['periodo_atual']['label']} | mês anterior: {payload['periodo_mes_anterior']['label']} | ano anterior: {payload['periodo_ano_anterior']['label']}")
if fontes_indisponiveis:
    print(f"[AVISO] Fontes indisponíveis: {fontes_indisponiveis}")

git_commit_push(
    ["raiox_oportunidades_data.js", "raiox_oportunidades.html"],
    f"Atualiza raiox_oportunidades_data.js - {date.today().strftime('%d/%m/%Y')}",
)
