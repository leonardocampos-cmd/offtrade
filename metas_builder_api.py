"""
Construção de metas (RJ/SP) — backend (Flask).

Vai do racional por TIME (o que o gestor decide, ex: "Allan: 2.8MM") até uma
proposta de meta por RCA (o que METAS RJ.xlsx/METAS SP.xlsx precisa — uma
linha por RCA), via rateio proporcional ao histórico de cada RCA. Não
escreve no Excel do Drive (sem permissão de escrita configurada, arquivo é
compartilhado/editado à mão por outras pessoas) — só gera um plano (JSON
local) e exporta XLSX com as MESMAS colunas de METAS RJ.xlsx/METAS SP.xlsx,
pronto pra colar manualmente. Pedido do usuário em 2026-09-04.

Uso local: python metas_builder_api.py  (abre em http://localhost:5057)
Na VPS roda atrás do nginx em /api/metas-builder/ (ver
deploy_metas_builder_vps.py). Ao contrário de pedidos_mercos_api.py (porta
5056, venv leve), este serviço já É consulta a Oracle no seu núcleo —
bundla oracledb/sqlalchemy/pandas direto (ver requirements.txt gerado por
deploy_metas_builder_vps.py) em vez do workaround de subprocess.
"""
import io
import json
import os
import statistics
from datetime import date, datetime
from pathlib import Path

import pandas as pd
from dateutil.relativedelta import relativedelta
from dotenv import load_dotenv
from flask import Flask, Blueprint, request, send_file

load_dotenv()

RUNTIME = os.getenv("OFFTRADE_RUNTIME", "local")
HERE = Path(__file__).parent

# meta.py já chama oracledb.init_oracle_client() — importar antes de
# qualquer outra coisa evita "Oracle Client library has already been
# initialized" (mesmo padrão de report_diario_vendedor.py).
import meta  # noqa: E402

app = Flask(__name__)
bp = Blueprint("metas_builder", __name__, url_prefix="/api/metas-builder")

DATA_FILE = HERE / "metas_builder_planos.json"

# ── Caminho das planilhas de meta (mesmas que meta.py/exportacao_meta.py/
# exportacao_sp.py já leem pra produção — aqui só LEMOS, nunca escrevemos
# de volta) ──────────────────────────────────────────────────────────────
_METAS_RJ_PATH = r"G:\Drives compartilhados\Off Trade\Campanhas e Metas\METAS\METAS RJ.xlsx"
_METAS_SP_PATH = r"G:\Drives compartilhados\Off Trade\Campanhas e Metas\METAS\METAS SP.xlsx"


def _caminho_planilha(nome_arquivo, fallback):
    try:
        import baixar_planilhas_drive as _bpd
        fn = _bpd.caminho_metas_rj if nome_arquivo == "METAS RJ.xlsx" else _bpd.caminho_metas_sp
        return _bpd.com_fallback(fn, fallback)
    except Exception:
        return fallback


# RJ Interior / SP W.S: listas fixas de RCA — não dá pra derivar de
# CONTRATO (Kessya Ourique está cadastrada como "RCA RJ - CAPITAL" na
# planilha, mas pedido explícito do usuário em 2026-09-04 é ela entrar no
# grupo Interior mesmo assim). W.S (RCA 588, schema SPON) não tem NENHUMA
# linha em METAS SP.xlsx ainda (vendedor ativo sem meta cadastrada, mesma
# situação já documentada em exportacao_meta.py:113-121 pro RJ) — entra
# como roster extra.
RJ_INTERIOR_RCAS = {159, 155, 241, 460, 283}
SP_WS_RCAS = {588}

# RJ: RCA fora do rateio de time por pedido explícito do usuário em
# 2026-09-04 ("tirar os 4 do grupo Allan pra sempre") — Allan Paes (174,
# provavelmente o próprio coordenador, não deveria receber fatia pessoal
# da meta do time que ele gerencia), Jose Marcelo Cardoso (158) e Viviani
# Alves (91). Continuam ativos no Oracle (BLOQUEIO='N') e com linha em
# METAS RJ.xlsx — só não entram nesta ferramenta de construção de metas por
# time. Kelly Ramos (420) tirada dessa lista a pedido do usuário em
# 2026-09-08 ("considera a Kelly") — volta a entrar no rateio do grupo Allan.
RJ_EXCLUIDOS_RCAS = {174, 158, 91}

COLUNAS_METAS_RJ = [
    'CONTRATO', 'VENDEDOR', 'RCA', 'MÊS', 'META FATURAMENTO', 'PESO FATURAMENTO',
    'META FATURAMENTO CASTAS', 'PESO FATURAMENTO CASTAS', 'META FATURAMENTO AZEITE',
    'PESO FATURAMENTO AZEITE', 'META POSITIVAÇÃO HOB + AZEITE', 'PESO POSITIVAÇÃO HOB + AZEITE',
    'META POSITIVAÇÃO', 'PESO POSITIVAÇÃO', 'META POSITIVAÇÃO RECKIT', 'PESO POSITIVAÇÃO RECKIT',
    'META POSITIVAÇÃO TIAL', 'PESO POSITIVAÇÃO TIAL', 'META POSITIVAÇÃO TATUZINHO',
    'PESO POSITIVAÇÃO TATUZINHO', 'META POSITIVAÇÃO RED BULL', 'PESO POSITIVAÇÃO RED BULL',
    'META POSITIVAÇÃO PINATTI', 'PESO POSITIVAÇÃO PINATTI', 'META POSITIVAÇÃO ESSENZA',
    'PESO POSITIVAÇÃO ESSENZA', 'META FATURAMENTO PERNOD', 'PESO FATURAMENTO PERNOD',
    'META FATURAMENTO HOB + AZEITE', 'PESO FATURAMENTO HOB + AZEITE', 'META POSITIVAÇÃO ESSENZA+HOB',
    'PESO POSITIVAÇÃO ESSENZA+HOB', 'META POSITIVAÇÃO ROBINSON CRUSOE', 'PESO POSITIVAÇÃO ROBINSON CRUSOE',
]

COLUNAS_METAS_SP = [
    'FILIAL', 'CONTRATO', 'VENDEDOR', 'RCA', 'PESO FATURAMENTO', 'META FATURAMENTO',
    'PESO POSITIVAÇÃO', 'META POSITIVAÇÃO', 'PESO FATURAMENTO PERNOD', 'META FATURAMENTO PERNOD',
    'PESO FATURAMENTO CRS', 'META FATURAMENTO CRS', 'PESO FATURAMENTO ESSENZA', 'META FATURAMENTO ESSENZA',
    'PESO FATURAMENTO BACARDI', 'META FATURAMENTO BACARDI', 'PESO FATURAMENTO CASTAS', 'META FATURAMENTO CASTAS',
    'PESO FATURAMENTO ESSENZA+HOB', 'META FATURAMENTO ESSENZA+HOB', 'MÊS',
]

# Categoria (id usado no formulário) -> coluna de META na planilha oficial
# que ela alimenta, mais se o peso/rateio é por FATURAMENTO ou POSITIVACAO
# histórico. coluna_meta=None: categoria sem coluna oficial confirmada —
# fica só no plano salvo/exportado como informação extra, NÃO escreve em
# nenhuma coluna da planilha (evita chutar mapeamento errado: ex. "Brown-
# Forman" não é o mesmo que "Bacardi", mesmo sendo a única coluna de
# destilado avulsa parecida em METAS SP.xlsx — não presumido).
# unidade "numero": categoria conta clientes/positivação — inteiro, sem
# formatar como moeda (pedido do usuário em 2026-09-04). Faltando = "moeda".
CATEGORIAS_RJ = {
    "faturamento": {"coluna_meta": "META FATURAMENTO",                 "peso": "fat", "rotulo": "Faturamento",
                     "hint": "Meta mensal de faturamento"},
    "positivacao": {"coluna_meta": "META POSITIVAÇÃO",                 "peso": "pos", "rotulo": "Positivação", "unidade": "numero",
                     "hint": "Meta mensal de clientes positivados"},
    "espumantes":  {"coluna_meta": None,                               "peso": "fat", "rotulo": "Espumantes (Perini/LVMH/Alud)",
                     "hint": "Sem coluna própria em METAS RJ.xlsx ainda — fica só no plano"},
    "castas":      {"coluna_meta": "META FATURAMENTO CASTAS",          "peso": "fat", "rotulo": "Castas",
                     "hint": "A definir — deixe em branco se ainda não tem valor"},
    # META POSITIVAÇÃO ROBINSON CRUSOE é positivação (contagem de clientes),
    # não faturamento — corrigido em 2026-09-04 (estava usando peso "fat"
    # por engano, incompatível com a coluna real de destino).
    "crusoe":      {"coluna_meta": "META POSITIVAÇÃO ROBINSON CRUSOE", "peso": "pos", "rotulo": "Crusoe", "unidade": "numero",
                     "hint": "Sugestão: média mai-ago + 20%",
                     "media_default": {"meses": 4, "pct": 20, "fornecedor": "%CRUSOE%"}},
    "hob_azeite":  {"coluna_meta": "META FATURAMENTO HOB + AZEITE",    "peso": "fat", "rotulo": "Hob + Azeite",
                     "hint": "Meta mensal"},
}

CATEGORIAS_SP = {
    "faturamento":   {"coluna_meta": "META FATURAMENTO",       "peso": "fat", "rotulo": "Faturamento",
                       "hint": "Meta mensal de faturamento"},
    "positivacao":   {"coluna_meta": "META POSITIVAÇÃO",       "peso": "pos", "rotulo": "Positivação", "unidade": "numero",
                       "hint": "Meta mensal de clientes positivados"},
    "espumantes":    {"coluna_meta": None,                     "peso": "fat", "rotulo": "Espumantes (Perini/LVMH/Alud/CRS)",
                       "hint": "Sem coluna própria confirmada em METAS SP.xlsx — fica só no plano"},
    "castas":        {"coluna_meta": "META FATURAMENTO CASTAS","peso": "fat", "rotulo": "Castas",
                       "hint": "A definir — deixe em branco se ainda não tem valor"},
    "pernod":        {"coluna_meta": "META FATURAMENTO PERNOD","peso": "fat", "rotulo": "Pernod",
                       "hint": "Meta mensal"},
    "brown_forman":  {"coluna_meta": None,                     "peso": "fat", "rotulo": "Brown-Forman",
                       "hint": "Sem coluna própria confirmada em METAS SP.xlsx (não é o mesmo que Bacardi) — fica só no plano"},
    "hob_azeite":    {"coluna_meta": None,                     "peso": "fat", "rotulo": "Hob + Azeite",
                       "hint": "Sem coluna própria confirmada em METAS SP.xlsx — fica só no plano"},
}

ESTADOS = {
    "RJ": {
        "categorias": CATEGORIAS_RJ,
        "colunas_export": COLUNAS_METAS_RJ,
        "grupos": ("allan", "interior"),
        "hist_schemas": [
            ("CRC", "engine", "(1,2,4)"),
            ("thekings", "engine_theking", "(1,2,4)"),
            ("CASTAS", "engine_castas", None),
            ("GARRIDO", "engine_garrido", None),
        ],
    },
    "SP": {
        "categorias": CATEGORIAS_SP,
        "colunas_export": COLUNAS_METAS_SP,
        "grupos": ("marcus", "ws"),
        "hist_schemas": [
            ("SPON", "engine_spon", None),
            ("thekings", "engine_theking", None),
            ("CASTAS", "engine_castas", None),
            ("MGON", "engine_mgon", None),
            ("BLENDED", "engine_blended", None),
        ],
    },
}


def _roster(estado: str) -> pd.DataFrame:
    """RCA, VENDEDOR, CONTRATO, GRUPO — uma linha por RCA ativo na planilha
    oficial do estado (+ W.S pro SP, que não tem linha lá ainda)."""
    if estado == "RJ":
        df = pd.read_excel(_caminho_planilha("METAS RJ.xlsx", _METAS_RJ_PATH))
        df.columns = df.columns.str.strip()
        df = df.dropna(subset=["RCA"])
        roster = df[["RCA", "VENDEDOR", "CONTRATO"]].drop_duplicates(subset=["RCA"]).copy()
        roster["RCA"] = roster["RCA"].astype(int)
        roster = roster[~roster["RCA"].isin(RJ_EXCLUIDOS_RCAS)]
        roster["GRUPO"] = roster["RCA"].apply(lambda r: "interior" if r in RJ_INTERIOR_RCAS else "allan")
    elif estado == "SP":
        df = pd.read_excel(_caminho_planilha("METAS SP.xlsx", _METAS_SP_PATH))
        df.columns = df.columns.str.strip()
        df = df.dropna(subset=["RCA"])
        roster = df[["RCA", "VENDEDOR", "CONTRATO"]].drop_duplicates(subset=["RCA"]).copy()
        roster["RCA"] = roster["RCA"].astype(int)
        if not (roster["RCA"] == 588).any():
            extra = pd.DataFrame([{"RCA": 588, "VENDEDOR": "W.S", "CONTRATO": ""}])
            roster = pd.concat([roster, extra], ignore_index=True)
        roster["GRUPO"] = roster["RCA"].apply(lambda r: "ws" if r in SP_WS_RCAS else "marcus")
    else:
        raise ValueError(f"Estado inválido: {estado}")

    roster["NOME_CURTO"] = roster["VENDEDOR"].str.replace(r"\s*-\s*OFF TRADE\s*$", "", regex=True).str.strip()
    roster = _filtrar_bloqueados(estado, roster)
    return roster.sort_values("NOME_CURTO").reset_index(drop=True)


# Só RCA ativo (PCUSUARI.BLOQUEIO = 'N') — pedido do usuário em 2026-09-04.
# Casa por (CODUSUR, NOME), não só CODUSUR: o mesmo número de RCA já
# representou pessoas diferentes em schemas diferentes (ex: 378 é Fabio
# Valotti no CRC e outra pessoa no MGON, achado documentado em
# exportacao_vendedores_auth.py) — casar só por número arriscaria excluir
# alguém por causa do bloqueio de um homônimo numérico em outro schema. RCA
# sem nenhuma linha de PCUSUARI encontrada (falha de rede/schema fora do ar)
# fica de fora por precaução — mais seguro que assumir "ativo" às cegas.
def _filtrar_bloqueados(estado: str, roster: pd.DataFrame) -> pd.DataFrame:
    rcas = roster["RCA"].tolist()
    rcas_sql = ",".join(str(r) for r in rcas)
    ativos = set()
    for nome_schema, engine_attr, _ in ESTADOS[estado]["hist_schemas"]:
        eng = getattr(meta, engine_attr)
        try:
            df = meta.carregar_dados(
                f"SELECT CODUSUR, NOME, BLOQUEIO FROM {nome_schema.upper()}.PCUSUARI WHERE CODUSUR IN ({rcas_sql})",
                eng, f"metas_builder_bloqueio_{estado}_{nome_schema}",
            )
            df.columns = df.columns.str.upper()
            for _, row in df.iterrows():
                if str(row["BLOQUEIO"]).strip().upper() == "N":
                    ativos.add((int(row["CODUSUR"]), str(row["NOME"]).strip().upper()))
        except Exception as e:
            print(f"[AVISO] metas_builder: checar BLOQUEIO em {nome_schema} falhou ({str(e)[:100]}) — ignorado")

    def _esta_ativo(row):
        return (int(row["RCA"]), str(row["VENDEDOR"]).strip().upper()) in ativos

    return roster[roster.apply(_esta_ativo, axis=1)].reset_index(drop=True)


# ── Meses considerados no histórico ─────────────────────────────────────────
# Últimos N meses FECHADOS (não conta o mês corrente, ainda parcial) — base
# da média/peso do rateio. O mesmo mês do ano anterior (pedido do usuário em
# 2026-09-04: "levar em consideração... o mês de setembro de 2025") fica
# SEPARADO, só como referência exibida ao lado — testado com um caso real
# (Adeilson Gonçalvez, RCA 431: zero venda em set/25 por ainda não estar
# ativo) que mostrou que misturar esse mês no MESMO divisor da média diluía
# injustamente quem não tinha histórico ainda naquele mês específico
# (achado e corrigido em 2026-09-04, confirmado com o usuário).
def _meses_rolantes(n_meses_atras: int = 3) -> list[date]:
    mes_atual = date.today().replace(day=1)
    return [mes_atual - relativedelta(months=i) for i in range(1, n_meses_atras + 1)]


def _mes_referencia_ano_anterior(n_meses_atras: int = 3) -> date | None:
    mes_atual = date.today().replace(day=1)
    candidato = mes_atual - relativedelta(years=1)
    return None if candidato in _meses_rolantes(n_meses_atras) else candidato


# ── Histórico (faturamento/positivação por RCA, meses específicos) ─────────
# Sempre via query direta no Oracle (líquida) — nunca metas_data.js::historico
# nem vendas_sp_data.js. Já existiu uma versão que preferia esses arquivos
# prontos (mais rápida, sem query nova), mas achado real em 2026-09-08
# ("Allan 2.8MM é alcançável?"): metas_data.js::historico soma PCMOV +
# cancelados (PBI_PCPEDI) + cortes pré-NF (view PEDIDOS_CANCELADOS) de
# propósito, só pro gráfico "Faturamento por Mês" de metas.html (demanda
# bruta) — NÃO é o "realizado" que bate meta (esse é
# por_mes.fat_tt.realizado, de _vh em exportacao_meta.py). Usar aquele como
# base de crescimento inflava a média histórica em 16-27% (testado no grupo
# do Allan), fazendo a meta calculada parecer mais alcançável do que é.
# Versão enxuta de exportacao_meta.py::_query_historico/exportacao_sp.py —
# SÓ pros RCAs do estado pedido (não os ~350 vendedores de todo o Brasil) e
# SÓ os meses pedidos (lista explícita, não "últimos N corridos" — permite
# combinar meses não-contíguos tipo "3 últimos + set/25"), pra não pagar o
# custo da consulta completa só pra abrir essa tela. CODUSUR já restringe a
# um RCA específico, então dispensa o filtro extra de CODFILIAL/ESTADO que
# exportacao_meta.py/exportacao_sp.py precisam (eles varrem TODOS os
# vendedores do schema, aqui não).
def _query_historico(estado: str, rcas: list[int], meses: list[date], fornecedor_like: str | None = None,
                      filtro_sql: str | None = None):
    """`fornecedor_like` filtra por F.FANTASIA (fornecedor) — categoria tipo
    Crusoe. `filtro_sql` é uma condição SQL crua (pode referenciar M.DESCRICAO
    e/ou F.FANTASIA, mesmas colunas de exportacao_meta.py::_realizado_mes) pra
    categoria com regra composta que não é um fornecedor só — ex: Hob+Azeite
    é `M.DESCRICAO LIKE '%AZEITE%' OR F.FANTASIA LIKE '%HOB%'`, não dá pra
    expressar com fornecedor_like sozinho. Confundir os dois (usar peso "fat"
    genérico pra uma categoria assim) foi um bug real, achado em 2026-09-08:
    Hob+Azeite saiu com os MESMOS números de Faturamento total no plano do
    RJ, porque caiu no fallback sem filtro nenhum.

    NUMNOTADEV IS NULL exclui venda depois devolvida — mesmo critério de
    DEVOLVIDO em exportacao_meta.py::_realizado_mes (lá é `CODOPER='ED' OR
    NUMNOTADEV IS NOT NULL`; CODOPER='ED' já fica de fora pelo IN ('S','SB')
    acima). Faltava aqui — achado em 2026-09-08 comparando o grupo Interior
    (Giselle/Raphael/Zeinaldo) com o realizado oficial: inflava a média em
    ~3%, pequeno mas real."""
    rcas_sql = ",".join(str(r) for r in rcas)
    meses_sql = ",".join(f"DATE '{m.isoformat()}'" for m in meses)
    precisa_join = bool(fornecedor_like or filtro_sql)
    join_produto_fornec = "\n          JOIN {s}.PCPRODUT P ON M.CODPROD = P.CODPROD\n          JOIN {s}.PCFORNEC F ON P.CODFORNEC = F.CODFORNEC" if precisa_join else ""
    condicoes_extra = []
    if fornecedor_like:
        condicoes_extra.append(f"F.FANTASIA LIKE '{fornecedor_like}'")
    if filtro_sql:
        condicoes_extra.append(f"({filtro_sql})")
    extra_where = ("\n              AND " + " AND ".join(condicoes_extra)) if condicoes_extra else ""

    def _sql(schema, filtro_filial):
        s = schema.upper()
        extra_filial = f"\n          AND M.CODFILIAL IN {filtro_filial}" if filtro_filial else ""
        return f"""
            SELECT TRUNC(M.DTMOV,'MM') AS MES, M.CODUSUR AS CODUSUR,
                   SUM(M.PUNIT*M.QT) AS FATURAMENTO, COUNT(DISTINCT M.CODCLI) AS POSITIVACAO
            FROM {s}.PCMOV M{join_produto_fornec.format(s=s)}
            WHERE TRUNC(M.DTMOV,'MM') IN ({meses_sql})
              AND M.CODOPER IN ('S','SB')
              AND M.NUMNOTADEV IS NULL
              AND M.CODUSUR IN ({rcas_sql}){extra_filial}{extra_where}
            GROUP BY TRUNC(M.DTMOV,'MM'), M.CODUSUR
        """

    partes = []
    for nome, engine_attr, filtro_filial in ESTADOS[estado]["hist_schemas"]:
        eng = getattr(meta, engine_attr)
        try:
            df = meta.carregar_dados(_sql(nome, filtro_filial), eng, f"metas_builder_hist_{estado}_{nome}")
            df.columns = df.columns.str.upper()
            partes.append(df)
        except Exception as e:
            print(f"[AVISO] metas_builder: histórico {estado}/{nome} falhou ({str(e)[:100]}) — ignorado")
    if not partes:
        return pd.DataFrame(columns=["MES", "CODUSUR", "FATURAMENTO", "POSITIVACAO"])
    full = pd.concat(partes, ignore_index=True)
    full["MES"] = pd.to_datetime(full["MES"])
    full["CODUSUR"] = pd.to_numeric(full["CODUSUR"], errors="coerce")
    full["FATURAMENTO"] = pd.to_numeric(full["FATURAMENTO"], errors="coerce").fillna(0)
    full["POSITIVACAO"] = pd.to_numeric(full["POSITIVACAO"], errors="coerce").fillna(0).astype(int)
    return full


def _media_mensal_por_rca(hist: pd.DataFrame, rcas: list[int], n_meses_base: int):
    """{RCA: {'fat': média mensal, 'pos': média mensal}} — divide sempre por
    n_meses_base (o tamanho da janela pedida), não por quantos meses tiveram
    venda de fato, senão um RCA com venda em só 1 dos 4 meses pedidos
    pareceria ter média igual a quem vendeu nos 4. RCA sem venda nenhuma
    entra com 0 (não fica de fora do rateio, só recebe fatia pequena)."""
    agg = hist.groupby("CODUSUR").agg(fat=("FATURAMENTO", "sum"), pos=("POSITIVACAO", "sum"))
    out = {}
    for rca in rcas:
        if rca in agg.index:
            out[rca] = {"fat": float(agg.loc[rca, "fat"]) / n_meses_base, "pos": float(agg.loc[rca, "pos"]) / n_meses_base}
        else:
            out[rca] = {"fat": 0.0, "pos": 0.0}
    return out


# ── Cobertura da base cadastrada ────────────────────────────────────────────
# % de clientes da carteira do RCA (CODUSUR1/2/3 em PCCLIENT, qualquer um
# dos 3 papéis — mesmo critério de _total_carteira em exportacao_meta.py)
# que já têm OFFTRADE='S' (cadastro completo pro programa). Métrica nova,
# não existe pronta em nenhum export hoje (exportacao_meta.py só tem
# cadastros NOVOS do mês corrente, não cobertura acumulada) — ver plano.
# Roda em TODOS os schemas de histórico do estado (não só CRC) — achado
# real em 2026-09-04 testando SP na tela: RCA de SP não tem cliente
# nenhum em CRC.PCCLIENT (a carteira dele mora em SPON.PCCLIENT), então
# assumir "sempre CRC" deixava a cobertura de SP inteira vazia.
def _cobertura_base(estado: str, rcas: list[int]):
    rcas_sql = ",".join(str(r) for r in rcas)
    carteira_por_rca: dict[int, set] = {}
    cadastrados_por_rca: dict[int, set] = {}
    for nome_schema, engine_attr, _ in ESTADOS[estado]["hist_schemas"]:
        s = nome_schema.upper()
        eng = getattr(meta, engine_attr)
        sql = f"""
            SELECT RCA, CODCLI, OFFTRADE FROM (
                SELECT C.CODUSUR1 AS RCA, C.CODCLI, C.OFFTRADE FROM {s}.PCCLIENT C WHERE C.CODUSUR1 IN ({rcas_sql})
                UNION ALL
                SELECT C.CODUSUR2, C.CODCLI, C.OFFTRADE FROM {s}.PCCLIENT C WHERE C.CODUSUR2 IN ({rcas_sql})
                UNION ALL
                SELECT C.CODUSUR3, C.CODCLI, C.OFFTRADE FROM {s}.PCCLIENT C WHERE C.CODUSUR3 IN ({rcas_sql})
            )
        """
        try:
            df = meta.carregar_dados(sql, eng, f"metas_builder_cobertura_{estado}_{nome_schema}")
            df.columns = df.columns.str.upper()
            for _, row in df.iterrows():
                rca = int(row["RCA"])
                chave_cliente = (nome_schema, row["CODCLI"])  # mesmo CODCLI pode existir em schemas diferentes
                carteira_por_rca.setdefault(rca, set()).add(chave_cliente)
                if str(row["OFFTRADE"]).strip().upper() == "S":
                    cadastrados_por_rca.setdefault(rca, set()).add(chave_cliente)
        except Exception as e:
            print(f"[AVISO] metas_builder: cobertura {estado}/{nome_schema} falhou ({str(e)[:100]}) — ignorado")

    out = {}
    for rca in rcas:
        carteira = len(carteira_por_rca.get(rca, ()))
        cadastrados = len(cadastrados_por_rca.get(rca, ()))
        if carteira:
            out[rca] = {"carteira": carteira, "cadastrados": cadastrados, "cobertura_pct": round(100 * cadastrados / carteira, 1)}
    return out


# ── Faturamento projetado por mediana de cliente ────────────────────────────
# Pra cada cliente da carteira do RCA (CODUSUR1/2/3 em PCCLIENT, mesmo
# critério de _cobertura_base — carteira inteira, não só cadastrados), pega a
# MEDIANA das compras desse cliente nos últimos 3 meses fechados (0 nos meses
# sem compra) e soma por RCA. Pedido do usuário em 2026-09-08: "com base nos
# clientes cadastrados... faturamento de acordo com mediana de vendas dos
# últimos 3 meses" — mediana em vez de média pra não deixar 1 pedido isolado
# grande distorcer o número (mesmo problema que o rateio por ticket médio
# individual tinha, testado antes nesta mesma conversa).
# EXISTS contra a lista de RCAs (pequena) em vez de filtrar PCMOV por lista
# de milhares de CODCLI — Oracle limita IN-list a 1000 itens, e a carteira de
# SP sozinha já passa disso.
def _mediana_por_cliente(estado: str, roster: pd.DataFrame) -> dict:
    rcas = roster["RCA"].tolist()
    rcas_sql = ",".join(str(r) for r in rcas)
    meses = _meses_rolantes(3)
    meses_sql = ",".join(f"DATE '{m.isoformat()}'" for m in meses)
    meses_keys = [m.strftime("%Y-%m") for m in meses]

    carteira_por_rca: dict[int, set] = {}
    fat_por_cliente: dict[tuple, dict] = {}

    for nome_schema, engine_attr, _ in ESTADOS[estado]["hist_schemas"]:
        s = nome_schema.upper()
        eng = getattr(meta, engine_attr)
        sql_carteira = f"""
            SELECT RCA, CODCLI FROM (
                SELECT C.CODUSUR1 AS RCA, C.CODCLI FROM {s}.PCCLIENT C WHERE C.CODUSUR1 IN ({rcas_sql})
                UNION ALL
                SELECT C.CODUSUR2, C.CODCLI FROM {s}.PCCLIENT C WHERE C.CODUSUR2 IN ({rcas_sql})
                UNION ALL
                SELECT C.CODUSUR3, C.CODCLI FROM {s}.PCCLIENT C WHERE C.CODUSUR3 IN ({rcas_sql})
            )
        """
        try:
            df_cart = meta.carregar_dados(sql_carteira, eng, f"metas_builder_mediana_carteira_{estado}_{nome_schema}")
            df_cart.columns = df_cart.columns.str.upper()
        except Exception as e:
            print(f"[AVISO] metas_builder: mediana/carteira {estado}/{nome_schema} falhou ({str(e)[:100]}) — ignorado")
            continue
        for _, row in df_cart.iterrows():
            rca = int(row["RCA"])
            carteira_por_rca.setdefault(rca, set()).add((nome_schema, row["CODCLI"]))

        sql_mov = f"""
            SELECT M.CODCLI, TRUNC(M.DTMOV,'MM') AS MES, SUM(M.PUNIT*M.QT) AS FAT
            FROM {s}.PCMOV M
            WHERE TRUNC(M.DTMOV,'MM') IN ({meses_sql})
              AND M.CODOPER IN ('S','SB')
              AND EXISTS (
                SELECT 1 FROM {s}.PCCLIENT C
                WHERE C.CODCLI = M.CODCLI
                  AND (C.CODUSUR1 IN ({rcas_sql}) OR C.CODUSUR2 IN ({rcas_sql}) OR C.CODUSUR3 IN ({rcas_sql}))
              )
            GROUP BY M.CODCLI, TRUNC(M.DTMOV,'MM')
        """
        try:
            df_mov = meta.carregar_dados(sql_mov, eng, f"metas_builder_mediana_mov_{estado}_{nome_schema}")
            df_mov.columns = df_mov.columns.str.upper()
        except Exception as e:
            print(f"[AVISO] metas_builder: mediana/movimento {estado}/{nome_schema} falhou ({str(e)[:100]}) — ignorado")
            continue
        for _, row in df_mov.iterrows():
            chave = (nome_schema, row["CODCLI"])
            mes_key = pd.Timestamp(row["MES"]).strftime("%Y-%m")
            fat_por_cliente.setdefault(chave, {})[mes_key] = float(row["FAT"])

    mediana_por_cliente = {}
    for chave, meses_fat in fat_por_cliente.items():
        valores = [meses_fat.get(mk, 0.0) for mk in meses_keys]
        mediana_por_cliente[chave] = statistics.median(valores)

    out = {}
    for rca in rcas:
        clientes = carteira_por_rca.get(rca, set())
        total = sum(mediana_por_cliente.get(c, 0.0) for c in clientes)
        out[rca] = {"faturamento_mediana": round(total, 2), "n_clientes": len(clientes)}
    return out, [m.strftime("%m/%Y") for m in meses]


# ── Rateio ──────────────────────────────────────────────────────────────────
def _ratear(valor_total: float, pesos: dict, unidade: str = "moeda") -> dict:
    """Distribui valor_total entre as chaves de `pesos` (RCA -> peso),
    proporcional ao peso de cada um. Se todo mundo tem peso zero, cai pra
    divisão igual — mesmo espírito de não deixar ninguém de fora do rateio.
    unidade='numero' (positivação/contagem de clientes): arredonda pra
    inteiro em vez de 2 casas decimais — pedido do usuário em 2026-09-04
    ("Positivação é número inteiro")."""
    casas = 0 if unidade == "numero" else 2
    rcas_alvo = list(pesos.keys())
    soma_pesos = sum(max(v, 0.0) for v in pesos.values())
    if soma_pesos <= 0:
        fatia = valor_total / len(rcas_alvo) if rcas_alvo else 0.0
        return {rca: round(fatia, casas) for rca in rcas_alvo}
    return {rca: round(valor_total * max(pesos[rca], 0.0) / soma_pesos, casas) for rca in rcas_alvo}


# Teto de crescimento por RCA (opcional, por categoria — parâmetro
# `teto_pct`) — pedido do usuário em 2026-09-08: com criterio="cobertura70"
# (rateio 100% por potencial de carteira), um RCA com carteira muito maior
# que o resto do time mas baixa cobertura de cadastro (ex: Marilena Tragel,
# RJ: 377 clientes na carteira contra ~70-160 do resto do grupo, só 33.7%
# cadastrados) recebia uma fatia calculada 4,4× a própria média histórica —
# meta provavelmente inatingível numa tacada só, o oposto de "justa" pra
# quem vai recebê-la. `_ratear_com_teto` trava quem estouraria
# `media_historica[rca] × teto_pct/100` nesse valor e redistribui o
# excedente pelos RCAs ainda livres, na mesma proporção de peso — repete em
# cascata porque redistribuir pode empurrar outro RCA acima do próprio teto.
def _ratear_com_teto(valor_total: float, pesos: dict, tetos: dict, unidade: str = "moeda") -> dict:
    casas = 0 if unidade == "numero" else 2
    rcas_alvo = list(pesos.keys())
    travado: dict[int, float] = {}
    livres = set(rcas_alvo)
    restante = valor_total

    while livres:
        soma_pesos = sum(max(pesos.get(rca, 0.0), 0.0) for rca in livres)
        if soma_pesos <= 0:
            provisorio = {rca: restante / len(livres) for rca in livres}
        else:
            provisorio = {rca: restante * max(pesos.get(rca, 0.0), 0.0) / soma_pesos for rca in livres}

        estourou = [rca for rca in livres if rca in tetos and provisorio[rca] > tetos[rca] + 1e-9]
        if not estourou:
            travado.update(provisorio)
            break
        for rca in estourou:
            travado[rca] = tetos[rca]
            restante -= tetos[rca]
            livres.discard(rca)
        # Todo mundo travou e ainda sobrou valor (soma dos tetos < valor_total)
        # — não tem mais quem redistribuir; deixa a sobra de fora em vez de
        # estourar teto de alguém à força.
        if not livres:
            break

    return {rca: round(travado.get(rca, 0.0), casas) for rca in rcas_alvo}


# Piso de meta por RCA (opcional, por categoria — parâmetro `piso`, valor
# fixo em R$, não relativo a histórico como o teto) — pedido do usuário em
# 2026-09-08: "para o time do Allan, não pode ter meta abaixo de 100k".
# Espelha _ratear_com_teto só que na direção oposta: quem ficaria abaixo do
# piso é travado nele, e o que falta pra cobrir isso sai proporcionalmente
# de quem está acima (reduz a fatia deles, mantendo o total do rateio fixo
# em valor_total) — cascata pelo mesmo motivo do teto (reduzir os de cima
# pode empurrar outro pra baixo do piso). Se a soma dos pisos de quem nunca
# sai da lista de "abaixo" superar valor_total, não tem mais ninguém acima
# pra tirar — o total pago fica maior que valor_total pedido (avisado no
# retorno de /calcular, não escondido).
def _ratear_com_piso(valor_total: float, pesos: dict, piso: float, unidade: str = "moeda") -> tuple[dict, float]:
    casas = 0 if unidade == "numero" else 2
    rcas_alvo = list(pesos.keys())
    travado: dict[int, float] = {}
    livres = set(rcas_alvo)
    restante = valor_total

    while livres:
        soma_pesos = sum(max(pesos.get(rca, 0.0), 0.0) for rca in livres)
        if soma_pesos <= 0:
            provisorio = {rca: restante / len(livres) for rca in livres}
        else:
            provisorio = {rca: restante * max(pesos.get(rca, 0.0), 0.0) / soma_pesos for rca in livres}

        abaixo = [rca for rca in livres if provisorio[rca] < piso - 1e-9]
        if not abaixo:
            travado.update(provisorio)
            break
        for rca in abaixo:
            travado[rca] = piso
            restante -= piso
            livres.discard(rca)
        if not livres:
            break

    total_pago = sum(travado.values())
    return {rca: round(travado.get(rca, 0.0), casas) for rca in rcas_alvo}, round(total_pago, 2)


def _pesos_fat_pos(medias: dict, rcas_alvo: list[int], peso: str) -> dict:
    return {rca: medias.get(rca, {}).get(peso, 0.0) for rca in rcas_alvo}


# Critério "coberturaNN" (ex: cobertura70, cobertura80): peso = carteira do
# RCA × NN% × ticket médio — mas usando a MEDIANA do ticket médio do
# próprio grupo (rcas_alvo), não o ticket individual de cada um. Pedido do
# usuário em 2026-09-04, depois de ver que o ticket PRÓPRIO de quem tem
# poucochíssimas positivações (ex: Danielle Moura, 2-3/mês) é uma amostra
# pequena demais e instável — inflava o peso dela sozinha (quase 47% do
# orçamento). Com a mediana do grupo, a diferença entre vendedores vem só
# do tamanho da carteira, não de um ticket individual estatisticamente
# frágil. Percentual generalizado (era só 70% fixo) em 2026-09-08, pedido
# do usuário: "vamos focar em 80% base compradora" — NN vem do próprio
# nome do critério (parseado em /calcular), não precisa de campo novo.
def _pesos_cobertura(medias_gerais: dict, cobertura: dict, rcas_alvo: list[int], pct: float) -> dict:
    tickets = []
    for rca in rcas_alvo:
        pos_medio = medias_gerais.get(rca, {}).get("pos", 0.0)
        if pos_medio:
            tickets.append(medias_gerais[rca]["fat"] / pos_medio)
    ticket_mediano = statistics.median(tickets) if tickets else 0.0
    return {
        rca: cobertura.get(rca, {}).get("carteira", 0) * (pct / 100.0) * ticket_mediano
        for rca in rcas_alvo
    }


def _rcas_do_escopo(estado: str, roster: pd.DataFrame, escopo: str) -> list[int]:
    if escopo == "estado":
        return roster["RCA"].tolist()
    if escopo in ESTADOS[estado]["grupos"]:
        return roster[roster["GRUPO"] == escopo]["RCA"].tolist()
    raise ValueError(f"Escopo inválido pra {estado}: {escopo}")


def _estado_valido(estado):
    return estado in ESTADOS


# ── Rotas ────────────────────────────────────────────────────────────────────
@bp.route("/rcas", methods=["GET"])
def listar_rcas():
    estado = request.args.get("estado", "RJ").upper()
    if not _estado_valido(estado):
        return {"ok": False, "motivo": f"Estado inválido: {estado}"}, 400

    meses_atras = int(request.args.get("meses", 3))
    meses = _meses_rolantes(meses_atras)
    mes_ref = _mes_referencia_ano_anterior(meses_atras)
    roster = _roster(estado)
    rcas = roster["RCA"].tolist()

    # Query direta no Oracle (líquida) — não metas_data.js::historico, que
    # soma cancelados+cortes de propósito pro gráfico "Faturamento por Mês"
    # (demanda bruta, não entrega). Ver comentário em /calcular — mesmo
    # achado real de 2026-09-08, aplicado aqui pra tela não mostrar um
    # número diferente do que o cálculo usa por baixo.
    hist = _query_historico(estado, rcas, meses)
    medias = _media_mensal_por_rca(hist, rcas, len(meses))
    fat_referencia = {}
    if mes_ref:
        hist_ref = _query_historico(estado, rcas, [mes_ref])
        medias_ref = _media_mensal_por_rca(hist_ref, rcas, 1)
        fat_referencia = {rca: medias_ref[rca]["fat"] for rca in rcas}
    cobertura = _cobertura_base(estado, rcas)

    linhas = []
    for _, r in roster.iterrows():
        rca = int(r["RCA"])
        linhas.append({
            "rca": rca,
            "nome": r["NOME_CURTO"],
            "contrato": r["CONTRATO"],
            "grupo": r["GRUPO"],
            "media_fat_mensal": round(medias[rca]["fat"], 2),
            "media_pos_mensal": round(medias[rca]["pos"], 1),
            "fat_mes_referencia": round(float(fat_referencia.get(rca, 0.0)), 2) if mes_ref else None,
            "cobertura": cobertura.get(rca),
        })
    return {
        "ok": True,
        "meses_considerados": [m.strftime("%m/%Y") for m in meses],
        "mes_referencia_ano_anterior": mes_ref.strftime("%m/%Y") if mes_ref else None,
        "grupos": list(ESTADOS[estado]["grupos"]),
        "rcas": linhas,
    }


@bp.route("/calcular", methods=["POST"])
def calcular():
    dados = request.get_json(silent=True) or {}
    estado = str(dados.get("estado", "RJ")).upper()
    if not _estado_valido(estado):
        return {"ok": False, "motivo": f"Estado inválido: {estado}"}, 400

    categorias_input = dados.get("categorias") or []
    if not categorias_input:
        return {"ok": False, "motivo": "Nenhuma categoria informada."}, 400

    categorias_def = ESTADOS[estado]["categorias"]
    roster = _roster(estado)
    todos_rcas = roster["RCA"].tolist()

    # Descobre a maior janela de meses pedida (modo média_pct) pra buscar o
    # histórico geral uma vez só, cobrindo todas as categorias — cada
    # categoria em média_pct com janela/fornecedor diferente busca a sua
    # própria por baixo (ver loop abaixo).
    max_meses_atras = 3
    for c in categorias_input:
        if c.get("modo") == "media_pct":
            max_meses_atras = max(max_meses_atras, int(c.get("meses_janela", 3)))
    meses_gerais = _meses_rolantes(max_meses_atras)
    # Query direta no Oracle (líquida — sem cancelados/cortes), não
    # metas_data.js::historico. Achado real em 2026-09-08 perguntando "Allan
    # 2.8MM é alcançável": aquele campo soma PCMOV + cancelados (PBI_PCPEDI)
    # + cortes pré-NF (view PEDIDOS_CANCELADOS) — são somados ali de
    # propósito só pro gráfico "Faturamento por Mês" de metas.html (mostra
    # demanda bruta, não entrega), NÃO é o "realizado" que bate meta (esse é
    # `por_mes.fat_tt.realizado`, vindo de _vh em exportacao_meta.py). Rodar
    # essa "média histórica" inflada como base de crescimento dava um número
    # 16-27% maior que a receita líquida real do grupo do Allan nos últimos
    # 3 meses fechados — tornava a meta parecer mais alcançável do que é.
    # Trocado pra usar sempre _query_historico (mesma fonte já usada em
    # Castas/Crusoe/Hob+Azeite/Espumantes, que nunca teve esse viés).
    hist_geral = _query_historico(estado, todos_rcas, meses_gerais)
    medias_gerais = _media_mensal_por_rca(hist_geral, todos_rcas, len(meses_gerais))

    # Critério alternativo de rateio "coberturaNN" (opcional, por categoria)
    # — pedido do usuário em 2026-09-04 ("a ideia é atingir 70% da base
    # compradora... precisa bater o valor fracionado"), percentual
    # generalizado em 2026-09-08 ("vamos focar em 80% base compradora").
    # Peso = carteira do RCA × NN% × ticket médio — mas o ticket usa a
    # MEDIANA do grupo (rcas_alvo), não o ticket individual (ver
    # _pesos_cobertura: ticket próprio de quem tem pouquíssima positivação
    # é amostra pequena demais — inflava sozinho o peso de quem tinha só
    # 2-3 positivações/mês, achado real testando com Danielle Moura,
    # corrigido a pedido do usuário). Carteira só é buscada se algum
    # criterio pedir de fato.
    cobertura_geral = None
    if any((str(c.get("criterio") or "").startswith("cobertura")) for c in categorias_input):
        cobertura_geral = _cobertura_base(estado, todos_rcas)

    resultado_categorias = []
    por_rca = {rca: {} for rca in todos_rcas}

    for cat_input in categorias_input:
        cat_id = cat_input.get("id")
        cat_def = categorias_def.get(cat_id)
        if not cat_def:
            return {"ok": False, "motivo": f"Categoria desconhecida pra {estado}: {cat_id}"}, 400

        escopo = cat_input.get("escopo", "estado")
        try:
            rcas_alvo = _rcas_do_escopo(estado, roster, escopo)
        except ValueError as e:
            return {"ok": False, "motivo": str(e)}, 400

        modo = cat_input.get("modo", "fixo")
        medias_cat = None
        if modo == "fixo":
            valor_total = float(cat_input.get("valor") or 0)
            base_media = None
        elif modo == "media_pct":
            meses_janela = int(cat_input.get("meses_janela", 3))
            pct = float(cat_input.get("percentual") or 0)
            fornecedor_like = cat_input.get("fornecedor_like") or None  # ex: "%CRUSOE%"
            filtro_sql = cat_input.get("filtro_sql") or None  # ex: Hob+Azeite, condição composta
            meses_cat = _meses_rolantes(meses_janela)
            if fornecedor_like or filtro_sql:
                # Filtro de marca/fornecedor (ou condição composta tipo
                # Hob+Azeite) não tem equivalente pronto em
                # metas_data.js/vendas_sp_data.js — só dá pra saber
                # consultando o Oracle direto (mesmo padrão de
                # campanha_crusoe.py::_query, F.FANTASIA LIKE).
                hist_cat = _query_historico(estado, rcas_alvo, meses_cat, fornecedor_like, filtro_sql)
                medias_cat = _media_mensal_por_rca(hist_cat, rcas_alvo, len(meses_cat))
            elif meses_cat == meses_gerais:
                medias_cat = medias_gerais
            else:
                hist_cat = _query_historico(estado, rcas_alvo, meses_cat)
                medias_cat = _media_mensal_por_rca(hist_cat, rcas_alvo, len(meses_cat))
            base_media = sum(medias_cat[r][cat_def["peso"]] for r in rcas_alvo)
            casas = 0 if cat_def.get("unidade") == "numero" else 2
            valor_total = round(base_media * (1 + pct / 100), casas)
        else:
            return {"ok": False, "motivo": f"Modo inválido: {modo}"}, 400

        unidade = cat_def.get("unidade", "moeda")
        # criterio de RATEIO (como a fatia é dividida) é independente do
        # peso usado pra CALCULAR o valor_total em modo média_pct — pedido
        # do usuário em 2026-09-04: quer ratear por "70% da base × ticket
        # médio" em vez de por faturamento histórico bruto.
        criterio = cat_input.get("criterio") or cat_def["peso"]
        sufixo_pct = criterio[len("cobertura"):] if criterio.startswith("cobertura") else ""
        if sufixo_pct.isdigit():
            pesos = _pesos_cobertura(medias_gerais, cobertura_geral or {}, rcas_alvo, float(sufixo_pct))
        else:
            pesos = _pesos_fat_pos(medias_gerais, rcas_alvo, criterio)

        teto_pct = cat_input.get("teto_pct")
        piso = cat_input.get("piso")
        total_pago_piso = None
        if piso:
            # Piso é valor fixo em R$/unidade, não relativo a histórico —
            # não precisa de base_teto/medias_cat, só do peso pra saber como
            # ratear o que sobra entre quem fica acima dele.
            rateio, total_pago_piso = _ratear_com_piso(valor_total, pesos, float(piso), unidade)
        elif teto_pct:
            # Base do teto é a média histórica DESSA categoria (medias_cat —
            # já respeita janela/filtro de fornecedor próprios, ex: Crusoe
            # usa só 4 meses e só produtos %CRUSOE%), não medias_gerais —
            # senão o teto de uma categoria filtrada comparava com a média
            # de faturamento/positivação GERAL do RCA, escala incompatível.
            # modo="fixo" não tem média própria calculada; cai pra
            # medias_gerais como aproximação (ainda assim no peso certo,
            # fat ou pos, só não filtrada por categoria).
            base_teto = medias_cat if medias_cat is not None else medias_gerais
            tetos = {rca: base_teto.get(rca, {}).get(cat_def["peso"], 0.0) * float(teto_pct) / 100.0 for rca in rcas_alvo}
            rateio = _ratear_com_teto(valor_total, pesos, tetos, unidade)
        else:
            rateio = _ratear(valor_total, pesos, unidade)
        for rca, valor in rateio.items():
            por_rca[rca][cat_id] = valor

        resultado_categorias.append({
            "id": cat_id, "rotulo": cat_def["rotulo"], "escopo": escopo, "modo": modo, "unidade": unidade,
            "valor_total": valor_total, "base_media": base_media, "criterio": criterio, "teto_pct": teto_pct,
            "piso": piso, "total_pago_piso": total_pago_piso,
        })

    linhas = []
    for _, r in roster.iterrows():
        rca = int(r["RCA"])
        linhas.append({"rca": rca, "nome": r["NOME_CURTO"], "grupo": r["GRUPO"], **por_rca[rca]})

    return {"ok": True, "estado": estado, "categorias": resultado_categorias, "rateio_por_rca": linhas}


@bp.route("/salvar", methods=["POST"])
def salvar():
    dados = request.get_json(silent=True) or {}
    if not dados.get("categorias") or not dados.get("rateio_por_rca"):
        return {"ok": False, "motivo": "Plano incompleto — rode /calcular antes de salvar."}, 400

    estado = str(dados.get("estado", "RJ")).upper()
    planos = _carregar_planos()
    # Resolução de segundo colidia quando dois /salvar chegavam no mesmo
    # segundo (achado real em 2026-09-08: salvei RJ e SP em sequência rápida
    # via script, o segundo sobrescreveu o primeiro silenciosamente — mesma
    # chave no dict). Sufixo de microssegundo resolve, e ainda incrementa
    # se por acaso colidir de novo (nunca deveria, mas não custa garantir).
    plano_id = datetime.now().strftime("%Y%m%d%H%M%S%f")
    while plano_id in planos:
        plano_id = str(int(plano_id) + 1)
    planos[plano_id] = {
        "id": plano_id,
        "estado": estado,
        "mes_referencia": dados.get("mes_referencia", ""),
        # Rótulo de cenário (ex: "Conservador", "Meta diretoria 2,8MM") —
        # opcional, pedido do usuário em 2026-09-08 ("crie cenários, e
        # coloca um filtro pra eu escolher"). Vazio = plano sem rótulo,
        # cai no criado_em como identificação (comportamento de antes).
        "rotulo": dados.get("rotulo", ""),
        "criado_em": datetime.now().strftime("%d/%m/%Y %H:%M"),
        "categorias": dados["categorias"],
        "rateio_por_rca": dados["rateio_por_rca"],
    }
    _salvar_planos(planos)
    return {"ok": True, "plano_id": plano_id}


@bp.route("/planos", methods=["GET"])
def listar_planos():
    estado = request.args.get("estado")
    planos = _carregar_planos()
    valores = planos.values()
    if estado:
        valores = [p for p in valores if p.get("estado") == estado.upper()]
    resumo = [
        {"id": p["id"], "estado": p["estado"], "mes_referencia": p["mes_referencia"],
         "criado_em": p["criado_em"], "rotulo": p.get("rotulo", "")}
        for p in sorted(valores, key=lambda p: p["id"], reverse=True)
    ]
    return {"ok": True, "planos": resumo}


@bp.route("/plano", methods=["GET"])
def plano_por_id():
    """Busca um plano salvo específico por id — usado pelo filtro de
    cenários em metas_builder.html (GET /plano-atual só devolve o mais
    recente; esse endpoint deixa escolher qualquer um da lista de
    /planos)."""
    plano_id = request.args.get("plano_id", "")
    planos = _carregar_planos()
    plano = planos.get(plano_id)
    if not plano:
        return {"ok": False, "motivo": "Plano não encontrado."}, 404
    return {"ok": True, "plano": plano}


@bp.route("/plano-atual", methods=["GET"])
def plano_atual():
    """Plano mais recente salvo pro estado — a página agora só EXIBE a
    última meta já calculada (pedido do usuário em 2026-09-04: "mostrar as
    metas já com esse estudo"), sem formulário de categoria na tela pra
    montar uma nova."""
    estado = request.args.get("estado", "RJ").upper()
    planos = _carregar_planos()
    candidatos = [p for p in planos.values() if p.get("estado") == estado]
    if not candidatos:
        return {"ok": True, "plano": None}
    plano = max(candidatos, key=lambda p: p["id"])
    return {"ok": True, "plano": plano}


@bp.route("/exportar", methods=["GET"])
def exportar():
    plano_id = request.args.get("plano_id", "")
    planos = _carregar_planos()
    plano = planos.get(plano_id)
    if not plano:
        return {"ok": False, "motivo": "Plano não encontrado."}, 404

    estado = plano["estado"]
    categorias_def = ESTADOS[estado]["categorias"]
    colunas = ESTADOS[estado]["colunas_export"]
    roster = _roster(estado).set_index("RCA")
    mes_ref = plano.get("mes_referencia") or datetime.now().strftime("%b/%y")

    linhas = []
    for r in plano["rateio_por_rca"]:
        rca = int(r["rca"])
        info = roster.loc[rca] if rca in roster.index else None
        linha = {c: "" for c in colunas}
        linha["RCA"] = rca
        linha["VENDEDOR"] = info["VENDEDOR"] if info is not None else r.get("nome", "")
        linha["CONTRATO"] = info["CONTRATO"] if info is not None else ""
        linha["MÊS"] = mes_ref
        for cat_id, valor in r.items():
            if cat_id in ("rca", "nome", "grupo"):
                continue
            cat_def = categorias_def.get(cat_id)
            if cat_def and cat_def["coluna_meta"]:
                linha[cat_def["coluna_meta"]] = valor
        linhas.append(linha)

    df_out = pd.DataFrame(linhas, columns=colunas)
    buf = io.BytesIO()
    df_out.to_excel(buf, index=False)
    buf.seek(0)
    nome_arquivo = f"metas_{estado.lower()}_proposta_{plano_id}.xlsx"
    return send_file(buf, as_attachment=True, download_name=nome_arquivo,
                      mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


@bp.route("/mediana-vendas", methods=["GET"])
def mediana_vendas():
    """Faturamento projetado por RCA = soma, pra cada cliente da carteira
    (CODUSUR1/2/3 em PCCLIENT), da mediana das compras dele nos últimos 3
    meses fechados. Página separada (mediana_vendas.html) — não mexe em
    plano/meta salva, só mostra a projeção."""
    estado = request.args.get("estado", "RJ").upper()
    if not _estado_valido(estado):
        return {"ok": False, "motivo": f"Estado inválido: {estado}"}, 400

    roster = _roster(estado)
    resultado, meses_considerados = _mediana_por_cliente(estado, roster)

    linhas = []
    for _, r in roster.iterrows():
        rca = int(r["RCA"])
        dado = resultado.get(rca, {"faturamento_mediana": 0.0, "n_clientes": 0})
        linhas.append({
            "rca": rca, "nome": r["NOME_CURTO"], "grupo": r["GRUPO"],
            "n_clientes": dado["n_clientes"], "faturamento_mediana": dado["faturamento_mediana"],
        })
    linhas.sort(key=lambda x: -x["faturamento_mediana"])
    return {
        "ok": True, "estado": estado, "meses_considerados": meses_considerados,
        "total_geral": round(sum(l["faturamento_mediana"] for l in linhas), 2),
        "rcas": linhas,
    }


@bp.route("/categorias", methods=["GET"])
def listar_categorias():
    """Pro front montar o formulário dinamicamente conforme o estado
    selecionado, sem hardcodar a lista de categorias duas vezes."""
    estado = request.args.get("estado", "RJ").upper()
    if not _estado_valido(estado):
        return {"ok": False, "motivo": f"Estado inválido: {estado}"}, 400
    cats = [
        {"id": cid, "rotulo": c["rotulo"], "hint": c.get("hint", ""), "unidade": c.get("unidade", "moeda"), "media_default": c.get("media_default")}
        for cid, c in ESTADOS[estado]["categorias"].items()
    ]
    return {"ok": True, "estado": estado, "grupos": list(ESTADOS[estado]["grupos"]), "categorias": cats}


def _carregar_planos() -> dict:
    if not DATA_FILE.exists():
        return {}
    return json.loads(DATA_FILE.read_text(encoding="utf-8"))


def _salvar_planos(planos: dict):
    tmp = DATA_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(planos, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, DATA_FILE)


app.register_blueprint(bp)

if __name__ == "__main__":
    debug = RUNTIME != "vps"
    host = "127.0.0.1" if RUNTIME == "vps" else "0.0.0.0"
    app.run(host=host, port=5057, debug=debug, threaded=True)
