# ─────────────────────────────────────────────────────────────────────────
# CAMPANHA "AÇÃO AMARULA OFF TRADE" — geração de acao_amarula_data.js
# ─────────────────────────────────────────────────────────────────────────
# Pedido do usuário em 19/08/2026. Diferente da campanha Amarula anterior
# (exportacao_amarula.py / amarula.html, encerrada em 25/06/2026): essa é
# item único (SKU 7702), com premiação em degraus e meta mínima por
# vendedor, restrita a:
#   - Time RJ: base CRC, filiais 2 e 4 (mesma convenção de FILIAIS_RJ usada
#     em exportacao_amarula.py / exportacao_metas_gerais.py).
#   - Só vendedores com U.TIPOVEND = 'E' (Externo) — confirmado
#     explicitamente com o usuário em 19/08/2026 que o filtro é literal no
#     campo TIPOVEND do Oracle, mesmo que isso exclua vendedores que atuam
#     como externo na prática mas estão cadastrados como outro tipo (ex.:
#     Kessya Ourique, RCA 275, cadastrada como REPRESENTANTE).
#
# Item: Amarula Cream 750ml + 1 Copo (CODPROD 7702), R$ 98,90.
# Meta mínima: 50 unidades no período p/ concorrer à premiação.
# Prêmios (só para quem bate a meta mínima): 1º R$800 · 2º R$600 ·
# 3º R$300 · 4º R$200.
import json
import subprocess
import re
import pandas as pd
from datetime import datetime
from pathlib import Path
from meta import engine, carregar_dados

DT_INI = "2026-08-19"
DT_FIM = "2026-09-19"
FILIAIS_RJ = "(2, 4)"

CODPROD = 7702
ITEM_DESCRICAO = "Amarula Cream 750ml + 1 Copo"
ITEM_VALOR = 98.90

META_MINIMA = 50
PREMIOS = [800, 600, 300, 200]  # 1º, 2º, 3º, 4º lugar

# Contas técnicas/genéricas que não são vendedor de verdade — mesma lista de
# exportacao_amarula.py.
EXCLUIR_VENDEDORES = {"RC", "VENDEDOR 09", "BEES", "VENDEDOR 02", "KELLY RAMOS - OFF TRADE", "RQ", "LOJA", "BEBIDA IN BOX"}

# Extrai multiplicador de pack de descrições tipo "12X50ML" — QT do ERP pode
# contar caixa em vez de unidade. Mesma regra de exportacao_amarula.py;
# CODPROD 7702 é um kit unitário (garrafa + copo), então na prática o
# multiplicador deve sair sempre 1, mas mantemos a checagem por segurança.
_PACK_RE = re.compile(r'(\d+)\s*[xX]\s*\d')


def _pack_multiplier(descricao) -> int:
    if not descricao:
        return 1
    m = _PACK_RE.search(str(descricao))
    if not m:
        return 1
    try:
        mult = int(m.group(1))
        return mult if mult > 0 else 1
    except ValueError:
        return 1


# Lista completa de vendedores elegíveis (time RJ + Externo), independente
# de terem vendido o item ou não — pedido do usuário em 19/08/2026 pra
# aparecerem todos no ranking desde já, com 0 unidades, em vez de só quem já
# vendeu.
QUERY_VENDEDORES = f"""
    SELECT DISTINCT U.NOME AS VENDEDOR
    FROM CRC.PCUSUARI U
    WHERE U.NOME LIKE '%OFF TRADE%'
      AND U.CODFILIAL IN {FILIAIS_RJ}
      AND UPPER(TRIM(U.TIPOVEND)) = 'E'
"""

QUERY_VENDAS = f"""
    SELECT
        U.NOME       AS VENDEDOR,
        M.DESCRICAO  AS DESCRICAO,
        SUM(M.QT)           AS QT,
        SUM(M.PUNIT * M.QT) AS FATURAMENTO
    FROM CRC.PCMOV M
    JOIN CRC.PCUSUARI U ON M.CODUSUR = U.CODUSUR
    WHERE M.CODPROD = {CODPROD}
      AND M.CODFILIAL IN {FILIAIS_RJ}
      AND TRUNC(M.DTMOV) >= TO_DATE('{DT_INI}', 'YYYY-MM-DD')
      AND TRUNC(M.DTMOV) <= TO_DATE('{DT_FIM}', 'YYYY-MM-DD')
      AND M.CODOPER = 'S'
      AND M.NUMNOTADEV IS NULL
      AND M.DTCANCEL IS NULL
      AND U.NOME LIKE '%OFF TRADE%'
      AND UPPER(TRIM(U.TIPOVEND)) = 'E'
    GROUP BY U.NOME, M.DESCRICAO
"""

FONTES_INDISPONIVEIS: list[str] = []


def _nome_exibicao(serie):
    return serie.str.replace(' - OFF TRADE', '', regex=False).str.replace('-OFF TRADE', '', regex=False).str.strip()


try:
    base = carregar_dados(QUERY_VENDEDORES, engine, "acao_amarula_vendedores_CRC")
    vendas = carregar_dados(QUERY_VENDAS, engine, "acao_amarula_vendas_CRC")
except Exception as _ex:
    print(f"[AVISO] CRC falhou ({str(_ex)[:100]}) — sem dados disponíveis para a Ação Amarula.")
    FONTES_INDISPONIVEIS.append("CRC")
    base = None
    vendas = None

if base is not None and not base.empty:
    base['NOME_EXIBICAO'] = _nome_exibicao(base['VENDEDOR'])
    base = base[~base['NOME_EXIBICAO'].isin(EXCLUIR_VENDEDORES) & ~base['VENDEDOR'].isin(EXCLUIR_VENDEDORES)].copy()
    base = base.drop_duplicates(subset=['NOME_EXIBICAO'])[['NOME_EXIBICAO']]

    if vendas is not None and not vendas.empty:
        vendas['QT'] = vendas['QT'].astype(float).fillna(0)
        vendas['FATURAMENTO'] = vendas['FATURAMENTO'].astype(float).fillna(0)
        vendas['MULTIPLICADOR'] = vendas['DESCRICAO'].apply(_pack_multiplier)
        vendas['VOLUME'] = vendas['QT'] * vendas['MULTIPLICADOR']
        vendas['NOME_EXIBICAO'] = _nome_exibicao(vendas['VENDEDOR'])
        agrup_vendas = (
            vendas.groupby('NOME_EXIBICAO')
                  .agg(volume=('VOLUME', 'sum'), faturamento=('FATURAMENTO', 'sum'))
                  .reset_index()
        )
    else:
        agrup_vendas = pd.DataFrame(columns=['NOME_EXIBICAO', 'volume', 'faturamento'])

    agrup = (
        base.merge(agrup_vendas, on='NOME_EXIBICAO', how='left')
            .fillna({'volume': 0, 'faturamento': 0.0})
            .sort_values(['volume', 'NOME_EXIBICAO'], ascending=[False, True])
    )

    ranking = []
    premio_idx = 0
    for _, r in agrup.iterrows():
        volume = int(r['volume'])
        elegivel = volume >= META_MINIMA
        premio = None
        if elegivel and premio_idx < len(PREMIOS):
            premio = PREMIOS[premio_idx]
            premio_idx += 1
        ranking.append({
            'vendedor': r['NOME_EXIBICAO'],
            'volume': volume,
            'faturamento': round(float(r['faturamento']), 2),
            'elegivel': elegivel,
            'premio': premio,
        })

    total_vendedores = int(agrup.shape[0])
    total_volume = int(agrup['volume'].sum())
    total_faturamento = float(agrup['faturamento'].sum())
else:
    ranking = []
    total_vendedores = 0
    total_volume = 0
    total_faturamento = 0.0

payload = {
    'atualizado_em': datetime.now().strftime('%d/%m/%Y %H:%M'),
    'periodo': {'ini': '19/08/2026', 'fim': '19/09/2026'},
    'escopo': 'Time RJ · Vendedores Externo',
    'item': {'codigo': CODPROD, 'descricao': ITEM_DESCRICAO, 'valor': ITEM_VALOR},
    'meta_minima': META_MINIMA,
    'premios': PREMIOS,
    'total_vendedores': total_vendedores,
    'total_volume': total_volume,
    'total_faturamento': round(total_faturamento, 2),
    'ranking': ranking,
    'fontes_indisponiveis': FONTES_INDISPONIVEIS,
}

output_path = Path(__file__).parent / "acao_amarula_data.js"
tmp_path = output_path.with_suffix(".js.tmp")
with open(tmp_path, 'w', encoding='utf-8') as f:
    f.write(f"const ACAO_AMARULA_DATA = {json.dumps(payload, ensure_ascii=False, indent=2)};\n")
import os
os.replace(tmp_path, output_path)

print(f"OK acao_amarula_data.js — {total_vendedores} vendedor(es), {total_volume} un totais do item {CODPROD}")

repo_dir = str(Path(__file__).parent)
subprocess.run(["git", "-C", repo_dir, "add", "acao_amarula_data.js"], check=False)
subprocess.run(["git", "-C", repo_dir, "commit", "-m",
                f"Atualiza acao_amarula_data.js - {datetime.now().strftime('%d/%m/%Y')}"])
subprocess.run(["git", "-C", repo_dir, "push", "origin", "master"], check=False)
print("OK GitHub Pages atualizado.")
