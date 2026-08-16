"""
Gera performance_equipe_data.js — visão de performance por vendedor/time OFF
TRADE (BI cruzado: base cadastro, base ativa, faturamento, positivações,
indústrias, TDP), com comparação "mesmo período" ano a ano (01/jan-hoje de
2025 vs 01/jan-hoje de 2026). Multi-base (CRC, thekings, CASTAS, GARRIDO,
SPON, MGON), mesmo padrão de exportacao_meta.py.

Pedido do usuário em 2026-08-12, a partir do esboço "VISÃO BI_ PERFORMANCE
EQUIPE.xlsx". Métricas novas (não existem em nenhum outro exportador):
  - Positivações (soma mensal de clientes distintos, não é o mesmo que
    positivação única)
  - Positivações Únicas (clientes distintos no período inteiro) + vs Base
    Cadastro (% da base cadastrada que comprou no período)
  - Indústrias / Qtd Indústria por Cliente (nunique CODFORNEC, médio por
    cliente)
  - TDP / Qtd SKUs por Cliente (nunique CODPROD, médio por cliente) — "TDP"
    aqui é a definição do próprio esboço: soma de itens diferentes vendidos
    no período, não o índice de TDP ponderado usado em trade marketing.
"""
import json
import subprocess
from datetime import date, datetime
from pathlib import Path

import pandas as pd

from meta import engine, engine_theking, engine_castas, engine_garrido, engine_spon, engine_mgon, engine_blended, carregar_dados, carregar_paralelo

# RCA -> (nome, time). Cópia de exportacao_raiox_vendedores.py — não dá pra
# importar de lá direto porque esse módulo roda a query inteira (Oracle +
# git commit/push) no top-level assim que é importado, sem guarda de
# __main__. Fonte: campanha_crusoe.py — lista oficial confirmada com o
# usuário em 08/07/2026 (ver memória project-campanhas-times). Só cobre o
# RJ; vendedores de outros estados/sistemas entram como "Outros". Manter em
# sincronia manualmente com TIMES_RJ em exportacao_raiox_vendedores.py.
TIMES_RJ = {
    275: ("Maria Luiza", "KEY_ACCOUNT"),
    158: ("Jose Marcelo Cardoso", "KEY_ACCOUNT"),
    144: ("Diogo Raposo", "ATACAREJO"),
    153: ("Angelo Neves Suzart", "ATACAREJO"),
    412: ("Barbara Cabral", "ATACAREJO"),
    419: ("Natali de Oliveira", "ATACAREJO"),
    439: ("Mateus Cardoso", "ATACAREJO"),
    450: ("Leandro Souza", "ATACAREJO"),
    471: ("Ana Clara Fassano", "ATACAREJO"),
    156: ("Marilena Tragel", "CONVENIENCE"),
    378: ("Fabio Valotti", "CONVENIENCE"),
    379: ("Jorge Maciel", "CONVENIENCE"),
    431: ("Adeilson Gonçalvez", "CONVENIENCE"),
}
TIME_LABEL = {
    "KEY_ACCOUNT": "Key Account",
    "ATACAREJO": "Atacarejo (Pequeno e médio varejo)",
    "CONVENIENCE": "Convenience (Varejo tradicional e Conveniência)",
    "OUTROS": "Outros / sem time definido",
}

_SOURCES = [
    ("CRC",      engine),
    ("thekings", engine_theking),
    ("CASTAS",   engine_castas),
    ("GARRIDO",  engine_garrido),
    ("SPON",     engine_spon),
    ("MGON",     engine_mgon),
    # Operação nova em SP (ver meta.py::engine_blended) — pedido do usuário
    # em 2026-08-14, mesma lacuna já corrigida em exportacao_meta.py.
    ("BLENDED",  engine_blended),
]

_hoje = date.today()


def _mesmo_dia_ano_anterior(hoje):
    try:
        return hoje.replace(year=hoje.year - 1)
    except ValueError:
        # 29/fev sem correspondente no ano anterior — cai pro dia 28.
        return hoje.replace(year=hoje.year - 1, day=28)


_ANO_ATUAL = _hoje.year
_ANO_ANTERIOR = _ANO_ATUAL - 1
_LIMITE_ANO_ANTERIOR = _mesmo_dia_ano_anterior(_hoje)


def _query_vendas(schema):
    """Grão (RCA, cliente, fornecedor, produto, ano, mês) — já bem mais leve
    que nível de nota/item, e suficiente pra tudo que essa página precisa
    (nunique de cliente/indústria/produto por RCA/período). As duas janelas
    de data (ano atual e ano anterior "mesmo período") vêm na mesma query
    pra evitar duplicar round-trips."""
    return f"""
        SELECT M.CODUSUR, U.NOME AS NOME_RCA, COALESCE(U.ESTADO, 'Sem Estado') AS ESTADO,
               COALESCE(U.TIPOVEND, 'Sem tipo') AS TIPOVEND,
               M.CODCLI, M.CODFORNEC, COALESCE(F.FANTASIA, F.FORNECEDOR) AS INDUSTRIA,
               M.CODPROD,
               EXTRACT(YEAR FROM M.DTMOV)  AS ANO,
               EXTRACT(MONTH FROM M.DTMOV) AS MES,
               SUM(M.PUNIT * M.QT) AS VALOR
        FROM {schema}.PCMOV M
        JOIN {schema}.PCUSUARI U ON M.CODUSUR = U.CODUSUR
        LEFT JOIN {schema}.PCFORNEC F ON M.CODFORNEC = F.CODFORNEC
        WHERE U.NOME LIKE '%OFF TRADE%'
          AND M.CODOPER IN ('S','SB')
          AND M.NUMNOTADEV IS NULL
          AND M.DTCANCEL IS NULL
          AND (
            (M.DTMOV >= DATE '{_ANO_ANTERIOR}-01-01' AND M.DTMOV <= DATE '{_LIMITE_ANO_ANTERIOR.isoformat()}')
            OR
            (M.DTMOV >= DATE '{_ANO_ATUAL}-01-01' AND M.DTMOV <= TRUNC(SYSDATE))
          )
        GROUP BY M.CODUSUR, U.NOME, U.ESTADO, U.TIPOVEND, M.CODCLI, M.CODFORNEC,
                 COALESCE(F.FANTASIA, F.FORNECEDOR), M.CODPROD,
                 EXTRACT(YEAR FROM M.DTMOV), EXTRACT(MONTH FROM M.DTMOV)
    """


def _query_cadastro(schema):
    return f"""
        SELECT C.CODCLI, C.CODUSUR1, C.CODUSUR2
        FROM {schema}.PCCLIENT C
        LEFT JOIN {schema}.PCUSUARI U1 ON C.CODUSUR1 = U1.CODUSUR
        LEFT JOIN {schema}.PCUSUARI U2 ON C.CODUSUR2 = U2.CODUSUR
        WHERE (U1.NOME LIKE '%OFF TRADE%' OR U2.NOME LIKE '%OFF TRADE%')
    """


def _query_hierarquia(schema):
    """RCA -> supervisor -> gerente -> CPF. Mesma estrutura de
    exportacao_meta.py (_query_hierarquia). Nem toda base tem
    PCSUPERV/PCGERENTE — quem chamar trata a exceção e cai pra
    'Sem gerente'. CPF (pedido do usuário em 2026-08-16) identifica a mesma
    pessoa cadastrada com CODUSUR diferente em mais de um sistema — usado
    pra agrupar num "vendedor" único em vez de uma linha por sistema."""
    return f"""
        SELECT U.CODUSUR,
               COALESCE(S.NOME, 'Sem supervisor') AS NOME_SUPERVISOR,
               COALESCE(G.NOMEGERENTE, 'Sem gerente') AS NOME_GERENTE,
               COALESCE(U.CPF, '') AS CPF
        FROM {schema}.PCUSUARI U
        LEFT JOIN {schema}.PCSUPERV S ON U.CODSUPERVISOR = S.CODSUPERVISOR
        LEFT JOIN {schema}.PCGERENTE G ON S.CODGERENTE = G.CODGERENTE
        WHERE U.NOME LIKE '%OFF TRADE%'
    """


fontes_indisponiveis = []

_chamadas_vendas = [(_query_vendas(nome), eng, f"perf_vendas_{nome}") for nome, eng in _SOURCES]
_partes_vendas = []
for (nome, _eng), resultado in zip(_SOURCES, carregar_paralelo(_chamadas_vendas)):
    if isinstance(resultado, Exception):
        print(f"[AVISO] perf_vendas_{nome} falhou ({str(resultado)[:100]}) — ignorado")
        fontes_indisponiveis.append(f"vendas_{nome}")
        continue
    resultado.columns = resultado.columns.str.upper()
    resultado['SISTEMA'] = nome
    _partes_vendas.append(resultado)

if not _partes_vendas:
    raise RuntimeError("Nenhuma fonte de vendas disponível — todas as bases Oracle estão fora do ar.")

vendas = pd.concat(_partes_vendas, ignore_index=True)
vendas['CODUSUR'] = pd.to_numeric(vendas['CODUSUR'], errors='coerce')
vendas['ANO'] = pd.to_numeric(vendas['ANO'], errors='coerce').astype('Int64')
vendas['MES'] = pd.to_numeric(vendas['MES'], errors='coerce').astype('Int64')
vendas['VALOR'] = pd.to_numeric(vendas['VALOR'], errors='coerce').fillna(0.0)
vendas['NOME_RCA'] = vendas['NOME_RCA'].fillna('').str.strip()
vendas['ESTADO'] = vendas['ESTADO'].fillna('Sem Estado').str.strip().str.upper()
_TIPOVEND_LABEL = {'E': 'Externo', 'I': 'Interno', 'R': 'Representante', 'P': 'Profissional'}
vendas['TIPOVEND'] = vendas['TIPOVEND'].fillna('').str.strip().str.upper().map(_TIPOVEND_LABEL).fillna('Sem tipo')
vendas['INDUSTRIA'] = vendas['INDUSTRIA'].fillna('Sem Indústria').str.strip()
vendas = vendas.dropna(subset=['CODUSUR', 'ANO', 'MES'])
vendas['CHAVE_RCA'] = vendas['SISTEMA'] + '-' + vendas['CODUSUR'].astype(int).astype(str)

_chamadas_cad = [(_query_cadastro(nome), eng, f"perf_cadastro_{nome}") for nome, eng in _SOURCES]
_partes_cad = []
for (nome, _eng), resultado in zip(_SOURCES, carregar_paralelo(_chamadas_cad)):
    if isinstance(resultado, Exception):
        print(f"[AVISO] perf_cadastro_{nome} falhou ({str(resultado)[:100]}) — ignorado")
        fontes_indisponiveis.append(f"cadastro_{nome}")
        continue
    resultado.columns = resultado.columns.str.upper()
    resultado['SISTEMA'] = nome
    _partes_cad.append(resultado)

cadastro = pd.concat(_partes_cad, ignore_index=True) if _partes_cad else pd.DataFrame(columns=['CODCLI', 'CODUSUR1', 'CODUSUR2', 'SISTEMA'])
for col in ('CODUSUR1', 'CODUSUR2'):
    cadastro[col] = pd.to_numeric(cadastro[col], errors='coerce')

# ── Hierarquia (RCA -> gerente) — nem toda base tem PCSUPERV/PCGERENTE ──────
_chamadas_hier = [(_query_hierarquia(nome), eng, f"perf_hierarquia_{nome}") for nome, eng in _SOURCES]
_partes_hier = []
for (nome, _eng), resultado in zip(_SOURCES, carregar_paralelo(_chamadas_hier)):
    if isinstance(resultado, Exception):
        print(f"[AVISO] perf_hierarquia_{nome} falhou ({str(resultado)[:100]}) — gerente fica 'Sem gerente' nessa base")
        continue
    resultado.columns = resultado.columns.str.upper()
    resultado['SISTEMA'] = nome
    _partes_hier.append(resultado)

hierarquia = pd.concat(_partes_hier, ignore_index=True) if _partes_hier else pd.DataFrame(columns=['CODUSUR', 'NOME_SUPERVISOR', 'NOME_GERENTE', 'CPF', 'SISTEMA'])
hierarquia['CODUSUR'] = pd.to_numeric(hierarquia['CODUSUR'], errors='coerce')
hierarquia['CHAVE_RCA'] = hierarquia['SISTEMA'] + '-' + hierarquia['CODUSUR'].astype('Int64').astype(str)
_gerente_por_chave = hierarquia.drop_duplicates(subset=['CHAVE_RCA']).set_index('CHAVE_RCA')['NOME_GERENTE'].to_dict()
# Só os dígitos — evita CPF não bater por causa de máscara (000.000.000-00
# vs 00000000000) entre bases diferentes.
hierarquia['CPF'] = hierarquia['CPF'].fillna('').astype(str).str.replace(r'\D', '', regex=True)
_cpf_por_chave = hierarquia.drop_duplicates(subset=['CHAVE_RCA']).set_index('CHAVE_RCA')['CPF'].to_dict()

# CODCLI/CODPROD são só únicos DENTRO de cada schema — o mesmo número em
# CRC e SPON são clientes/produtos diferentes. Enquanto cada "vendedor" do
# payload era uma única CHAVE_RCA (== um único sistema), nunique(CODCLI) já
# saía certo sem querer. Agora que um vendedor pode agrupar CHAVE_RCA de
# sistemas diferentes (via CPF, ver abaixo), nunique correria risco de
# colidir cliente/produto de bases diferentes com o mesmo código — por isso
# as métricas passam a usar essas chaves prefixadas por sistema.
vendas['CODCLI_G'] = vendas['SISTEMA'] + '-' + vendas['CODCLI'].astype(str)
vendas['CODPROD_G'] = vendas['SISTEMA'] + '-' + vendas['CODPROD'].astype(str)


def _time_de(sistema, estado, codusur):
    if sistema in ('CRC',) and estado == 'RJ' and int(codusur) in TIMES_RJ:
        _, time_key = TIMES_RJ[int(codusur)]
        return time_key
    return 'OUTROS'


# ── Nome/estado/time por RCA (última venda define o nome de exibição) ──────
_rca_info = (
    vendas.sort_values(['ANO', 'MES'])
    .drop_duplicates(subset=['CHAVE_RCA'], keep='last')
    [['CHAVE_RCA', 'SISTEMA', 'CODUSUR', 'NOME_RCA', 'ESTADO', 'TIPOVEND']]
    .copy()
)
_rca_info['TIME'] = _rca_info.apply(lambda r: _time_de(r['SISTEMA'], r['ESTADO'], r['CODUSUR']), axis=1)
_rca_info['GERENTE'] = _rca_info['CHAVE_RCA'].map(_gerente_por_chave).fillna('Sem gerente')
_rca_info['NOME_EXIBICAO'] = _rca_info['NOME_RCA'].str.replace(' - OFF TRADE', '', regex=False).str.replace('-OFF TRADE', '', regex=False).str.strip()

# Pedido do usuário em 2026-08-16: vendedor que vende em mais de um sistema
# (CPF igual, CODUSUR diferente por base — ex. alguém ativo tanto na CRC
# quanto na SPON) vira UMA linha só, não uma por sistema. Sem CPF (campo
# vazio em PCUSUARI) não dá pra provar que é a mesma pessoa com segurança —
# nesse caso cada CHAVE_RCA continua isolada (fallback pro comportamento
# antigo).
_rca_info['CPF'] = _rca_info['CHAVE_RCA'].map(_cpf_por_chave).fillna('')
_rca_info['CHAVE_VENDEDOR'] = _rca_info.apply(
    lambda r: f"CPF-{r['CPF']}" if r['CPF'] else r['CHAVE_RCA'], axis=1
)
# "Identidade" de exibição (nome/estado/tipo/sistema) do grupo = a do
# CHAVE_RCA com maior faturamento no ano atual — desempata de forma estável
# quando o mesmo CPF aparece em sistemas diferentes.
_fat_atual_por_chave = vendas[vendas['ANO'] == _ANO_ATUAL].groupby('CHAVE_RCA')['VALOR'].sum().to_dict()
_rca_info['FAT_ATUAL_CHAVE'] = _rca_info['CHAVE_RCA'].map(_fat_atual_por_chave).fillna(0.0)

# ── Base cadastro por RCA (cliente com CODUSUR1 ou CODUSUR2 == RCA) ────────
_base_cadastro_qtd = {}
for chave in _rca_info['CHAVE_RCA']:
    sistema, codusur_s = chave.split('-', 1)
    codusur = int(codusur_s)
    sub = cadastro[cadastro['SISTEMA'] == sistema]
    clientes = sub[(sub['CODUSUR1'] == codusur) | (sub['CODUSUR2'] == codusur)]['CODCLI'].nunique()
    _base_cadastro_qtd[chave] = int(clientes)

# ── Base ativa (últimos 3 meses, relativo a hoje — não é ano-calendário) ───
_periodos_ativa = set()
_m, _a = _hoje.month, _hoje.year
for _ in range(3):
    _periodos_ativa.add(_a * 100 + _m)
    _m -= 1
    if _m == 0:
        _m, _a = 12, _a - 1
_periodo_vendas = vendas['ANO'].astype(int) * 100 + vendas['MES'].astype(int)
_base_ativa_qtd = vendas[_periodo_vendas.isin(_periodos_ativa)].groupby('CHAVE_RCA')['CODCLI'].nunique().to_dict()


def _metricas(df):
    """df: subconjunto de `vendas` já filtrado por ano — devolve dict de
    métricas agregadas (usado tanto por RCA quanto por time)."""
    if df.empty:
        return {
            'faturamento': 0.0, 'positivacoes': 0, 'positivacoes_unicas': 0,
            'industrias': 0, 'qtd_industria_cliente': 0.0,
            'tdp': 0, 'qtd_sku_cliente': 0.0,
        }
    faturamento = round(float(df['VALOR'].sum()), 2)
    positivacoes = int(df.groupby(['ANO', 'MES'])['CODCLI_G'].nunique().sum())
    positivacoes_unicas = int(df['CODCLI_G'].nunique())
    industrias = int(df['INDUSTRIA'].nunique())
    qtd_industria_cliente = round(float(df.groupby('CODCLI_G')['INDUSTRIA'].nunique().mean()), 2)
    tdp = int(df['CODPROD_G'].nunique())
    qtd_sku_cliente = round(float(df.groupby('CODCLI_G')['CODPROD_G'].nunique().mean()), 2)
    return {
        'faturamento': faturamento, 'positivacoes': positivacoes,
        'positivacoes_unicas': positivacoes_unicas, 'industrias': industrias,
        'qtd_industria_cliente': qtd_industria_cliente, 'tdp': tdp,
        'qtd_sku_cliente': qtd_sku_cliente,
    }


def _serie_mensal(df):
    """{'YYYY-MM': {'faturamento':.., 'positivacoes_unicas':.., 'tdp':.., 'qtd_industria_cliente':.., 'qtd_sku_cliente':..}}"""
    serie = {}
    for (ano, mes), grp in df.groupby(['ANO', 'MES']):
        chave = f"{int(ano)}-{int(mes):02d}"
        serie[chave] = {
            'faturamento': round(float(grp['VALOR'].sum()), 2),
            'positivacoes_unicas': int(grp['CODCLI_G'].nunique()),
            'industrias': int(grp['INDUSTRIA'].nunique()),
            'qtd_industria_cliente': round(float(grp.groupby('CODCLI_G')['INDUSTRIA'].nunique().mean()), 2),
            'tdp': int(grp['CODPROD_G'].nunique()),
            'qtd_sku_cliente': round(float(grp.groupby('CODCLI_G')['CODPROD_G'].nunique().mean()), 2),
        }
    return serie


def _serie_por_uf(df):
    """{'UF': {'faturamento':.., 'positivacoes_unicas':.., 'tdp':.., 'qtd_industria_cliente':..}}"""
    serie = {}
    for uf, grp in df.groupby('ESTADO'):
        serie[uf] = {
            'faturamento': round(float(grp['VALOR'].sum()), 2),
            'positivacoes_unicas': int(grp['CODCLI_G'].nunique()),
            'industrias': int(grp['INDUSTRIA'].nunique()),
            'qtd_industria_cliente': round(float(grp.groupby('CODCLI_G')['INDUSTRIA'].nunique().mean()), 2),
            'tdp': int(grp['CODPROD_G'].nunique()),
        }
    return serie


def _serie_por_industria(df):
    """{'INDUSTRIA': {'faturamento':.., 'positivacoes_unicas':.., 'tdp':.., 'qtd_sku_cliente':..}} —
    usado pelo filtro de Indústria em performance_equipe.html (só recorta a
    tabela, período acumulado; não cruza com mês/UF pra não explodir o
    tamanho do payload — 131 indústrias x 153 vendedores x 2 anos)."""
    serie = {}
    for industria, grp in df.groupby('INDUSTRIA'):
        serie[industria] = {
            'faturamento': round(float(grp['VALOR'].sum()), 2),
            'positivacoes_unicas': int(grp['CODCLI_G'].nunique()),
            'tdp': int(grp['CODPROD_G'].nunique()),
            'qtd_sku_cliente': round(float(grp.groupby('CODCLI_G')['CODPROD_G'].nunique().mean()), 2),
        }
    return serie


def _bloco_periodo(df):
    return {
        'metricas': _metricas(df),
        'mensal': _serie_mensal(df),
        'por_uf': _serie_por_uf(df),
        'por_industria': _serie_por_industria(df),
    }


# ── Monta payload por vendedor (agrupado por CPF quando disponível — um
# vendedor que vende em mais de um sistema vira uma linha só) ──────────────
vendedores = []
for chave_vendedor, grupo in _rca_info.groupby('CHAVE_VENDEDOR'):
    chaves_rca_grupo = set(grupo['CHAVE_RCA'])
    principal = grupo.sort_values('FAT_ATUAL_CHAVE', ascending=False).iloc[0]
    sistemas_grupo = sorted(grupo['SISTEMA'].unique().tolist())

    df_grp = vendas[vendas['CHAVE_RCA'].isin(chaves_rca_grupo)]
    df_atual = df_grp[df_grp['ANO'] == _ANO_ATUAL]
    df_anterior = df_grp[df_grp['ANO'] == _ANO_ANTERIOR]
    base_cad = sum(_base_cadastro_qtd.get(c, 0) for c in chaves_rca_grupo)
    pos_unicas_atual = int(df_atual['CODCLI_G'].nunique())
    vendedores.append({
        'chave': chave_vendedor,
        'sistema': principal['SISTEMA'],
        'sistemas': sistemas_grupo,
        'rca': int(principal['CODUSUR']),
        'nome': principal['NOME_EXIBICAO'],
        'estado': principal['ESTADO'],
        'time': principal['TIME'],
        'time_label': TIME_LABEL[principal['TIME']],
        'gerente': principal['GERENTE'],
        'tipo_vendedor': principal['TIPOVEND'],
        'base_cadastro': base_cad,
        'base_ativa': int(sum(_base_ativa_qtd.get(c, 0) for c in chaves_rca_grupo)),
        'vs_base_pct': round(100 * pos_unicas_atual / base_cad, 1) if base_cad else None,
        str(_ANO_ATUAL):    _bloco_periodo(df_atual),
        str(_ANO_ANTERIOR): _bloco_periodo(df_anterior),
    })
vendedores.sort(key=lambda v: v[str(_ANO_ATUAL)]['metricas']['faturamento'], reverse=True)

# ── Monta payload por gerente (recalcula do zero — nunique não soma entre RCAs) ──
# "MESMA VISÃO GERÊNCIA" do esboço = mesma tabela, rollup por gerente real
# (hierarquia PCUSUARI->PCSUPERV->PCGERENTE), não por time comercial.
gerentes = []
for gerente_nome in sorted(_rca_info['GERENTE'].unique()):
    sub_ger = _rca_info[_rca_info['GERENTE'] == gerente_nome]
    chaves_ger = set(sub_ger['CHAVE_RCA'])
    df_ger = vendas[vendas['CHAVE_RCA'].isin(chaves_ger)]
    df_atual = df_ger[df_ger['ANO'] == _ANO_ATUAL]
    df_anterior = df_ger[df_ger['ANO'] == _ANO_ANTERIOR]
    base_cad_ger = sum(_base_cadastro_qtd.get(c, 0) for c in chaves_ger)
    pos_unicas_atual = int(df_atual['CODCLI_G'].nunique())
    gerentes.append({
        'gerente': gerente_nome,
        # Vendedor único (CPF), não CHAVE_RCA — sem isso, quem vende em 2
        # sistemas sob o mesmo gerente contava 2x aqui.
        'qtd_vendedores': sub_ger['CHAVE_VENDEDOR'].nunique(),
        'base_cadastro': base_cad_ger,
        'base_ativa': int(sum(_base_ativa_qtd.get(c, 0) for c in chaves_ger)),
        'vs_base_pct': round(100 * pos_unicas_atual / base_cad_ger, 1) if base_cad_ger else None,
        str(_ANO_ATUAL):    _bloco_periodo(df_atual),
        str(_ANO_ANTERIOR): _bloco_periodo(df_anterior),
    })
gerentes.sort(key=lambda g: g[str(_ANO_ATUAL)]['metricas']['faturamento'], reverse=True)

# ── Opções de filtro (indústria -> SKUs, sistemas, UFs) ────────────────────
_industrias_skus = {}
for industria, grp in vendas[vendas['ANO'] == _ANO_ATUAL].groupby('INDUSTRIA'):
    _industrias_skus[industria] = sorted(int(c) for c in grp['CODPROD'].dropna().unique())

payload = {
    'atualizado_em': datetime.now().strftime('%d/%m/%Y %H:%M'),
    'ano_atual': _ANO_ATUAL,
    'ano_anterior': _ANO_ANTERIOR,
    'limite_ano_anterior': _LIMITE_ANO_ANTERIOR.strftime('%d/%m/%Y'),
    'vendedores': vendedores,
    'gerentes': gerentes,
    'opcoes_filtro': {
        'sistemas': sorted(vendas['SISTEMA'].unique().tolist()),
        'ufs': sorted(vendas['ESTADO'].unique().tolist()),
        'industrias': sorted(_industrias_skus.keys()),
        'skus_por_industria': _industrias_skus,
    },
    'fontes_indisponiveis': fontes_indisponiveis,
}

out_path = Path(__file__).parent / "performance_equipe_data.js"
with open(out_path, 'w', encoding='utf-8') as f:
    f.write(f"// Gerado automaticamente\nconst PERFORMANCE_EQUIPE_DATA = {json.dumps(payload, ensure_ascii=False)};\n")

print(f"OK performance_equipe_data.js — {len(vendedores)} vendedor(es), {len(gerentes)} gerente(s), "
      f"período {_ANO_ANTERIOR} (até {_LIMITE_ANO_ANTERIOR.strftime('%d/%m')}) vs {_ANO_ATUAL} (até hoje)")
if fontes_indisponiveis:
    print(f"[AVISO] Fontes indisponíveis: {fontes_indisponiveis}")

repo_dir = str(Path(__file__).parent)
subprocess.run(["git", "-C", repo_dir, "add", "performance_equipe_data.js", "performance_equipe.html"], check=False)
subprocess.run(["git", "-C", repo_dir, "commit", "-m",
                f"Atualiza performance_equipe_data.js - {date.today().strftime('%d/%m/%Y')}"])
subprocess.run(["git", "-C", repo_dir, "push", "origin", "master"], check=False)
print("OK GitHub Pages atualizado.")
