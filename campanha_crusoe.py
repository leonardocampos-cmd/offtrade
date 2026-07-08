# ─────────────────────────────────────────────────────────────────────────
# TIME 1 - KEY ACCOUNT (ROBSON CRUSOE) - geração de crusoe_data.js
# ─────────────────────────────────────────────────────────────────────────
import json
import subprocess
import pandas as pd
from datetime import datetime, timedelta
from pathlib import Path
from meta import engine, carregar_dados

DT_INI = "2026-07-01"
DT_FIM = "2026-08-31"
LOOKBACK_DIAS = 60  # janela para "novo SKU" e "reativação"
PREMIO_1 = 800

# RJ = filiais 2 e 4 na base CRC (mesma convenção de exportacao_amarula.py)
FILIAIS_RJ = "(2, 4)"

RCAS = {
    275: "Maria Luiza",
    158: "Jose Marcelo Cardoso",
}

# TODO: preencher quando a meta de faturamento (jul+ago) for definida para
# cada RCA. Enquanto None, o critério "atingimento de meta" fica zerado / N/D.
META_FATURAMENTO = {
    275: None,
    158: None,
}

_dt_ini = datetime.strptime(DT_INI, "%Y-%m-%d")
_dt_fim = datetime.strptime(DT_FIM, "%Y-%m-%d")
_dt_lookback_ini = (_dt_ini - timedelta(days=LOOKBACK_DIAS)).strftime("%Y-%m-%d")

def _query(schema=(275, 158), filtro_filial=FILIAIS_RJ):
    return f"""
        SELECT
            M.CODUSUR    AS CODUSUR,
            M.CODCLI     AS CODCLI,
            M.CODPROD    AS CODPROD,
            M.NUMNOTA    AS NUMNOTA,
            M.DTMOV      AS DTMOV,
            M.QT         AS QT,
            M.PUNIT      AS PUNIT
        FROM CRC.PCMOV M
        WHERE M.CODUSUR IN {schema}
          AND TRUNC(M.DTMOV) >= TO_DATE('{_dt_lookback_ini}', 'YYYY-MM-DD')
          AND TRUNC(M.DTMOV) <= TO_DATE('{DT_FIM}', 'YYYY-MM-DD')
          AND M.CODOPER = 'S'
          AND M.NUMNOTADEV IS NULL
          AND M.DTCANCEL IS NULL
          AND M.CODFILIAL IN {filtro_filial}
    """

hist = carregar_dados(_query(), engine, "crusoe_CRC")
hist['DTMOV'] = pd.to_datetime(hist['DTMOV'])
hist['QT'] = pd.to_numeric(hist['QT'], errors='coerce').fillna(0)
hist['PUNIT'] = pd.to_numeric(hist['PUNIT'], errors='coerce').fillna(0)
hist['FATURAMENTO'] = hist['QT'] * hist['PUNIT']
hist['CODUSUR'] = pd.to_numeric(hist['CODUSUR'], errors='coerce')

camp = hist[(hist['DTMOV'] >= _dt_ini) & (hist['DTMOV'] <= _dt_fim)].copy()

def _teve_venda_antes(codusur, chave_col, chave_val, antes_de):
    """True se existe venda de `codusur` (mesma chave, ex.: cliente ou cliente+produto)
    na janela [antes_de - LOOKBACK_DIAS, antes_de)."""
    janela_ini = antes_de - timedelta(days=LOOKBACK_DIAS)
    sub = hist[(hist['CODUSUR'] == codusur) & (hist[chave_col] == chave_val)]
    sub = sub[(sub['DTMOV'] >= janela_ini) & (sub['DTMOV'] < antes_de)]
    return not sub.empty

resultado = []
for rca, nome in RCAS.items():
    camp_rca = camp[camp['CODUSUR'] == rca]

    pedidos = int(camp_rca['NUMNOTA'].nunique())
    faturamento = float(camp_rca['FATURAMENTO'].sum())

    # Novo SKU: primeira venda de (CODCLI, CODPROD) na campanha sem venda
    # do mesmo par nos LOOKBACK_DIAS anteriores.
    novos_skus = 0
    if not camp_rca.empty:
        primeiras_skus = (
            camp_rca.groupby(['CODCLI', 'CODPROD'])['DTMOV'].min().reset_index()
        )
        for _, r in primeiras_skus.iterrows():
            sub = hist[(hist['CODUSUR'] == rca) & (hist['CODCLI'] == r['CODCLI']) & (hist['CODPROD'] == r['CODPROD'])]
            janela_ini = r['DTMOV'] - timedelta(days=LOOKBACK_DIAS)
            teve_antes = not sub[(sub['DTMOV'] >= janela_ini) & (sub['DTMOV'] < r['DTMOV'])].empty
            if not teve_antes:
                novos_skus += 1

    # Reativação: primeiro pedido do cliente na campanha sem NENHUM pedido
    # (qualquer produto) nos LOOKBACK_DIAS anteriores.
    reativacoes = 0
    if not camp_rca.empty:
        primeiros_cli = camp_rca.groupby('CODCLI')['DTMOV'].min().reset_index()
        for _, r in primeiros_cli.iterrows():
            if not _teve_venda_antes(rca, 'CODCLI', r['CODCLI'], r['DTMOV']):
                reativacoes += 1

    meta_valor = META_FATURAMENTO.get(rca)
    meta_definida = meta_valor is not None
    meta_atingida = bool(meta_definida and faturamento >= meta_valor)

    pontos_pedidos = pedidos
    pontos_novos_skus = novos_skus * 5
    pontos_reativacoes = reativacoes * 5
    pontos_meta = 5 if meta_atingida else 0
    pontos_total = pontos_pedidos + pontos_novos_skus + pontos_reativacoes + pontos_meta

    resultado.append({
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

resultado.sort(key=lambda r: (r['pontos_total'], r['faturamento']), reverse=True)

payload = {
    'atualizado_em': datetime.now().strftime('%d/%m/%Y %H:%M'),
    'periodo': {'ini': '01/07/2026', 'fim': '31/08/2026'},
    'premio': PREMIO_1,
    'vendedores': resultado,
}

output_path = Path(__file__).parent / "crusoe_data.js"
with open(output_path, 'w', encoding='utf-8') as f:
    f.write(f"const CRUSOE_DATA = {json.dumps(payload, ensure_ascii=False, indent=2)};\n")

print(f"OK crusoe_data.js — {len(resultado)} vendedores, {len(camp)} linhas no período")

repo_dir = str(Path(__file__).parent)
subprocess.run(["git", "-C", repo_dir, "add", "crusoe_data.js"], check=False)
subprocess.run(["git", "-C", repo_dir, "commit", "-m",
                f"Atualiza crusoe_data.js - {datetime.now().strftime('%d/%m/%Y')}"])
subprocess.run(["git", "-C", repo_dir, "push", "origin", "master"], check=False)
print("OK GitHub Pages atualizado.")
