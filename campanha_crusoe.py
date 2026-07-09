# ─────────────────────────────────────────────────────────────────────────
# CAMPANHA ROBINSON CRUSOE - geração de crusoe_data.js
# ─────────────────────────────────────────────────────────────────────────
#
# RCAs por time (CODUSUR / NOME / TIPO no Oracle, confirmado em 08/07/2026):
#   Time 1 - Key Account:  275 Maria Luiza · 158 Jose Marcelo Cardoso
#   Time 2 - Atacarejo:    144 Diogo Raposo · 153 Angelo Neves Suzart ·
#                          412 Barbara Cabral · 419 Natali de Oliveira ·
#                          439 Mateus Cardoso · 450 Leandro Souza ·
#                          471 Ana Clara Fassano
#   Time 3 - Convenience:  156 Marilena Tragel · 378 Fabio Valotti ·
#                          379 Jorge Maciel · 431 Adeilson Gonçalvez
#
# Os 3 times participam da campanha Robinson Crusoe, cada um com seu próprio
# ranking/vencedor e critério de pontuação (confirmado em 08/07/2026):
#
#   Key Account:
#     +1 pt por pedido · +5 pts novo SKU (60d) · +5 pts reativação (60d)
#     +4 pts meta de faturamento batida no mês
#
#   Atacarejo:
#     +5 pts positivação (cliente novo ou sem compra há 60 dias)
#     +4 pts por novo SKU introduzido (qualquer cliente)
#     Por pedido, conforme valor do pedido: R$500-1000 = +1 pt,
#     R$1001-3000 = +2 pts, acima de R$3001 = +3 pts (abaixo de R$500 = 0)
#
#   Convenience:
#     +1 pt por cliente que comprou algum produto da "Linha Gourmet" no
#     período (SALADA MEDITERRANEA, ATUM MY PROTEIN, ATUM AO AZEITE,
#     PATÊ DE ATUM). Vencedor = maior número de clientes positivados.
#
# Cada time premia R$ 800 para o 1º lugar do próprio ranking.
import json
import subprocess
import unicodedata
import pandas as pd
from datetime import datetime, timedelta
from pathlib import Path
from meta import engine, carregar_dados

DT_INI = "2026-07-01"
DT_FIM = "2026-08-31"
LOOKBACK_DIAS = 60  # janela para "novo SKU" e "reativação/positivação"
PREMIO_POR_TIME = {
    "KEY_ACCOUNT": 800,
    "ATACAREJO": 800,
    "CONVENIENCE": 800,
}

# rca -> (nome, time)
RCAS = {
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

# Chave de busca (sem acento, maiúscula) usada para casar contra U.NOME no
# Oracle — CODUSUR não é portável entre bases, por isso filtramos por nome
# (igual ao padrão de exportacao_amarula.py / meta.py).
NOMES_BUSCA = {rca: unicodedata.normalize('NFKD', nome).encode('ascii', 'ignore').decode().upper()
               for rca, (nome, _time) in RCAS.items()}

# TODO: preencher quando a meta de faturamento (jul+ago) for definida para
# cada RCA do Key Account. Enquanto None, o critério "meta batida" fica
# zerado / N/D. Times Atacarejo e Convenience não têm critério de meta.
META_FATURAMENTO = {
    275: None,
    158: None,
}

GOURMET_PRODUTOS = [
    "SALADA MEDITERRANEA",
    "ATUM MY PROTEIN",
    "ATUM AO AZEITE",
    "PATE DE ATUM",
]

_dt_ini = datetime.strptime(DT_INI, "%Y-%m-%d")
_dt_fim = datetime.strptime(DT_FIM, "%Y-%m-%d")
_dt_lookback_ini = (_dt_ini - timedelta(days=LOOKBACK_DIAS)).strftime("%Y-%m-%d")


def _strip_accents(s):
    return unicodedata.normalize('NFKD', str(s)).encode('ascii', 'ignore').decode().upper()


def _query(schema, filtro_filial=None, filtro_estado=None):
    s = schema.upper()
    extra_filial = f"\n          AND M.CODFILIAL IN {filtro_filial}" if filtro_filial else ""
    extra_estado = f"\n          AND U.ESTADO = '{filtro_estado}'" if filtro_estado else ""
    condicoes_nome = " OR ".join(
        f"UPPER(U.NOME) LIKE '%{nome}%'" for nome in NOMES_BUSCA.values()
    )
    return f"""
        SELECT
            U.NOME       AS NOME,
            M.CODCLI     AS CODCLI,
            M.CODPROD    AS CODPROD,
            M.DESCRICAO  AS DESCRICAO,
            M.NUMNOTA    AS NUMNOTA,
            M.DTMOV      AS DTMOV,
            M.QT         AS QT,
            M.PUNIT      AS PUNIT
        FROM {s}.PCMOV M
        JOIN {s}.PCUSUARI U ON M.CODUSUR = U.CODUSUR
        WHERE ({condicoes_nome})
          AND TRUNC(M.DTMOV) >= TO_DATE('{_dt_lookback_ini}', 'YYYY-MM-DD')
          AND TRUNC(M.DTMOV) <= TO_DATE('{DT_FIM}', 'YYYY-MM-DD')
          AND M.CODOPER = 'S'
          AND M.NUMNOTADEV IS NULL
          AND M.DTCANCEL IS NULL{extra_filial}{extra_estado}
    """

# Campanha Robinson Crusoe atende só a base CRC (RJ = filiais 2 e 4).
FONTES = [
    ("CRC", engine, "(2, 4)", None),
]

FONTES_INDISPONIVEIS: list[str] = []
_partes = []
for _nome_fonte, _engine_fonte, _filtro_filial, _filtro_estado in FONTES:
    try:
        _partes.append(carregar_dados(
            _query(_nome_fonte, _filtro_filial, _filtro_estado),
            _engine_fonte, f"crusoe_{_nome_fonte}"
        ))
    except Exception as _ex:
        print(f"[AVISO] {_nome_fonte} falhou ({str(_ex)[:80]}) — desconsiderado, cálculo segue com as demais bases.")
        FONTES_INDISPONIVEIS.append(_nome_fonte)

if not _partes:
    raise RuntimeError("Nenhuma fonte Oracle disponível para a campanha Crusoe — todas as bases estão fora do ar.")

hist = pd.concat(_partes, ignore_index=True)
hist['DTMOV'] = pd.to_datetime(hist['DTMOV'])
hist['QT'] = pd.to_numeric(hist['QT'], errors='coerce').fillna(0)
hist['PUNIT'] = pd.to_numeric(hist['PUNIT'], errors='coerce').fillna(0)
hist['FATURAMENTO'] = hist['QT'] * hist['PUNIT']


def _mapear_rca(nome_oracle):
    n = _strip_accents(nome_oracle)
    for rca, chave in NOMES_BUSCA.items():
        if chave in n:
            return rca
    return None


hist['RCA'] = hist['NOME'].apply(_mapear_rca)
hist = hist.dropna(subset=['RCA']).copy()
hist['RCA'] = hist['RCA'].astype(int)
hist['DESCRICAO_NORM'] = hist['DESCRICAO'].apply(_strip_accents)
hist['IS_GOURMET'] = hist['DESCRICAO_NORM'].apply(lambda d: any(p in d for p in GOURMET_PRODUTOS))

camp = hist[(hist['DTMOV'] >= _dt_ini) & (hist['DTMOV'] <= _dt_fim)].copy()


def _teve_venda_antes(rca, chave_col, chave_val, antes_de):
    """True se existe venda de `rca` (mesma chave, ex.: cliente ou cliente+produto)
    na janela [antes_de - LOOKBACK_DIAS, antes_de)."""
    janela_ini = antes_de - timedelta(days=LOOKBACK_DIAS)
    sub = hist[(hist['RCA'] == rca) & (hist[chave_col] == chave_val)]
    sub = sub[(sub['DTMOV'] >= janela_ini) & (sub['DTMOV'] < antes_de)]
    return not sub.empty


def _contar_novos_skus(rca, camp_rca):
    """Primeira venda de (CODCLI, CODPROD) na campanha sem venda do mesmo
    par nos LOOKBACK_DIAS anteriores."""
    if camp_rca.empty:
        return 0
    novos = 0
    primeiras_skus = camp_rca.groupby(['CODCLI', 'CODPROD'])['DTMOV'].min().reset_index()
    for _, r in primeiras_skus.iterrows():
        sub = hist[(hist['RCA'] == rca) & (hist['CODCLI'] == r['CODCLI']) & (hist['CODPROD'] == r['CODPROD'])]
        janela_ini = r['DTMOV'] - timedelta(days=LOOKBACK_DIAS)
        teve_antes = not sub[(sub['DTMOV'] >= janela_ini) & (sub['DTMOV'] < r['DTMOV'])].empty
        if not teve_antes:
            novos += 1
    return novos


def _contar_positivacoes(rca, camp_rca):
    """Primeiro pedido do cliente na campanha sem NENHUM pedido (qualquer
    produto) nos LOOKBACK_DIAS anteriores — cobre "cliente novo" e
    "reativação" com a mesma regra."""
    if camp_rca.empty:
        return 0
    positivacoes = 0
    primeiros_cli = camp_rca.groupby('CODCLI')['DTMOV'].min().reset_index()
    for _, r in primeiros_cli.iterrows():
        if not _teve_venda_antes(rca, 'CODCLI', r['CODCLI'], r['DTMOV']):
            positivacoes += 1
    return positivacoes


resultado_por_time: dict[str, list] = {"KEY_ACCOUNT": [], "ATACAREJO": [], "CONVENIENCE": []}

for rca, (nome, time_id) in RCAS.items():
    camp_rca = camp[camp['RCA'] == rca]
    pedidos = int(camp_rca['NUMNOTA'].nunique())
    faturamento = float(camp_rca['FATURAMENTO'].sum())

    if time_id == "KEY_ACCOUNT":
        novos_skus = _contar_novos_skus(rca, camp_rca)
        reativacoes = _contar_positivacoes(rca, camp_rca)

        meta_valor = META_FATURAMENTO.get(rca)
        meta_definida = meta_valor is not None
        meta_atingida = bool(meta_definida and faturamento >= meta_valor)

        pontos_pedidos = pedidos
        pontos_novos_skus = novos_skus * 5
        pontos_reativacoes = reativacoes * 5
        pontos_meta = 4 if meta_atingida else 0
        pontos_total = pontos_pedidos + pontos_novos_skus + pontos_reativacoes + pontos_meta

        resultado_por_time[time_id].append({
            'rca': rca,
            'vendedor': nome,
            'pedidos': pedidos,
            'novos_skus': novos_skus,
            'reativacoes': reativacoes,
            'faturamento': round(faturamento, 2),
            'meta_definida': meta_definida,
            'meta_valor': meta_valor,
            'meta_atingida': meta_atingida,
            'pontos_pedidos': pontos_pedidos,
            'pontos_novos_skus': pontos_novos_skus,
            'pontos_reativacoes': pontos_reativacoes,
            'pontos_meta': pontos_meta,
            'pontos_total': pontos_total,
        })

    elif time_id == "ATACAREJO":
        novos_skus = _contar_novos_skus(rca, camp_rca)
        positivacoes = _contar_positivacoes(rca, camp_rca)

        pontos_pedidos_valor = 0
        if not camp_rca.empty:
            valores_pedido = camp_rca.groupby('NUMNOTA')['FATURAMENTO'].sum()
            for v in valores_pedido:
                if v >= 3001:
                    pontos_pedidos_valor += 3
                elif v >= 1001:
                    pontos_pedidos_valor += 2
                elif v >= 500:
                    pontos_pedidos_valor += 1

        pontos_positivacao = positivacoes * 5
        pontos_novos_skus = novos_skus * 4
        pontos_total = pontos_positivacao + pontos_novos_skus + pontos_pedidos_valor

        resultado_por_time[time_id].append({
            'rca': rca,
            'vendedor': nome,
            'pedidos': pedidos,
            'positivacoes': positivacoes,
            'novos_skus': novos_skus,
            'faturamento': round(faturamento, 2),
            'pontos_positivacao': pontos_positivacao,
            'pontos_novos_skus': pontos_novos_skus,
            'pontos_pedidos_valor': pontos_pedidos_valor,
            'pontos_total': pontos_total,
        })

    elif time_id == "CONVENIENCE":
        camp_gourmet = camp_rca[camp_rca['IS_GOURMET']]
        clientes_gourmet = int(camp_gourmet['CODCLI'].nunique())
        faturamento_gourmet = float(camp_gourmet['FATURAMENTO'].sum())
        pontos_total = clientes_gourmet

        resultado_por_time[time_id].append({
            'rca': rca,
            'vendedor': nome,
            'clientes_gourmet': clientes_gourmet,
            'faturamento': round(faturamento, 2),
            'faturamento_gourmet': round(faturamento_gourmet, 2),
            'pontos_total': pontos_total,
        })

for _time_id in resultado_por_time:
    resultado_por_time[_time_id].sort(key=lambda r: (r['pontos_total'], r['faturamento']), reverse=True)

TIME_META = {
    "KEY_ACCOUNT": {
        "nome": "Key Account",
        "criterios": [
            {"label": "por pedido", "valor": "+1 pt"},
            {"label": "novo SKU (60d)", "valor": "+5 pts"},
            {"label": "reativação (60d)", "valor": "+5 pts"},
            {"label": "meta batida no mês", "valor": "+4 pts"},
        ],
    },
    "ATACAREJO": {
        "nome": "Atacarejo",
        "criterios": [
            {"label": "positivação (novo/reativação 60d)", "valor": "+5 pts"},
            {"label": "novo SKU", "valor": "+4 pts"},
            {"label": "pedido R$500-1000", "valor": "+1 pt"},
            {"label": "pedido R$1001-3000", "valor": "+2 pts"},
            {"label": "pedido acima de R$3001", "valor": "+3 pts"},
        ],
    },
    "CONVENIENCE": {
        "nome": "Convenience",
        "criterios": [
            {"label": "cliente positivado (Linha Gourmet)", "valor": "+1 pt"},
        ],
    },
}

payload = {
    'atualizado_em': datetime.now().strftime('%d/%m/%Y %H:%M'),
    'periodo': {'ini': '01/07/2026', 'fim': '31/08/2026'},
    'times': [
        {
            'id': time_id,
            'nome': TIME_META[time_id]['nome'],
            'premio': PREMIO_POR_TIME[time_id],
            'criterios': TIME_META[time_id]['criterios'],
            'vendedores': resultado_por_time[time_id],
        }
        for time_id in ('KEY_ACCOUNT', 'ATACAREJO', 'CONVENIENCE')
    ],
    'fontes_indisponiveis': FONTES_INDISPONIVEIS,
}

output_path = Path(__file__).parent / "crusoe_data.js"
with open(output_path, 'w', encoding='utf-8') as f:
    f.write(f"const CRUSOE_DATA = {json.dumps(payload, ensure_ascii=False, indent=2)};\n")

_total_vendedores = sum(len(v) for v in resultado_por_time.values())
print(f"OK crusoe_data.js — {_total_vendedores} vendedores em 3 times, {len(camp)} linhas no período")

repo_dir = str(Path(__file__).parent)
subprocess.run(["git", "-C", repo_dir, "add", "crusoe_data.js"], check=False)
subprocess.run(["git", "-C", repo_dir, "commit", "-m",
                f"Atualiza crusoe_data.js - {datetime.now().strftime('%d/%m/%Y')}"])
subprocess.run(["git", "-C", repo_dir, "push", "origin", "master"], check=False)
print("OK GitHub Pages atualizado.")
