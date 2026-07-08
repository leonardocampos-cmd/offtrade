# ─────────────────────────────────────────────────────────────────────────
# TIME 1 - KEY ACCOUNT (ROBSON CRUSOE) - geração de crusoe_data.js
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
# Esta campanha (Robson Crusoe) atende só o Time 1 (RCAs 275 e 158).
import json
import subprocess
import pandas as pd
from datetime import datetime, timedelta
from pathlib import Path
from meta import engine, engine_theking, engine_castas, engine_garrido, engine_spon, carregar_dados

DT_INI = "2026-07-01"
DT_FIM = "2026-08-31"
LOOKBACK_DIAS = 60  # janela para "novo SKU" e "reativação"
PREMIO_1 = 800

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

# CODUSUR não é portável entre bases Oracle — cada empresa tem sua própria
# numeração. Por isso filtramos por NOME do vendedor (igual ao padrão de
# exportacao_amarula.py / meta.py), não por CODUSUR.
def _query(schema, filtro_filial=None, filtro_estado=None):
    s = schema.upper()
    extra_filial = f"\n          AND M.CODFILIAL IN {filtro_filial}" if filtro_filial else ""
    extra_estado = f"\n          AND U.ESTADO = '{filtro_estado}'" if filtro_estado else ""
    return f"""
        SELECT
            U.NOME       AS NOME,
            M.CODCLI     AS CODCLI,
            M.CODPROD    AS CODPROD,
            M.NUMNOTA    AS NUMNOTA,
            M.DTMOV      AS DTMOV,
            M.QT         AS QT,
            M.PUNIT      AS PUNIT
        FROM {s}.PCMOV M
        JOIN {s}.PCUSUARI U ON M.CODUSUR = U.CODUSUR
        WHERE (UPPER(U.NOME) LIKE '%MARIA LUIZA%' OR UPPER(U.NOME) LIKE '%JOSE MARCELO CARDOSO%')
          AND TRUNC(M.DTMOV) >= TO_DATE('{_dt_lookback_ini}', 'YYYY-MM-DD')
          AND TRUNC(M.DTMOV) <= TO_DATE('{DT_FIM}', 'YYYY-MM-DD')
          AND M.CODOPER = 'S'
          AND M.NUMNOTADEV IS NULL
          AND M.DTCANCEL IS NULL{extra_filial}{extra_estado}
    """

# RJ = filiais 2 e 4 nas bases CRC/thekings; demais bases usam PCUSUARI.ESTADO
# (mesma convenção de meta.py::_query_vendas).
FONTES = [
    ("CRC",      engine,         "(2, 4)", None),
    ("thekings", engine_theking, "(2, 4)", None),
    ("CASTAS",   engine_castas,  None,     "RJ"),
    ("GARRIDO",  engine_garrido, None,     "RJ"),
    ("SPON",     engine_spon,    None,     "RJ"),
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
    n = str(nome_oracle).upper()
    if 'MARIA LUIZA' in n:
        return 275
    if 'JOSE MARCELO CARDOSO' in n:
        return 158
    return None

hist['RCA'] = hist['NOME'].apply(_mapear_rca)
hist = hist.dropna(subset=['RCA']).copy()
hist['RCA'] = hist['RCA'].astype(int)

camp = hist[(hist['DTMOV'] >= _dt_ini) & (hist['DTMOV'] <= _dt_fim)].copy()

def _teve_venda_antes(rca, chave_col, chave_val, antes_de):
    """True se existe venda de `rca` (mesma chave, ex.: cliente ou cliente+produto)
    na janela [antes_de - LOOKBACK_DIAS, antes_de)."""
    janela_ini = antes_de - timedelta(days=LOOKBACK_DIAS)
    sub = hist[(hist['RCA'] == rca) & (hist[chave_col] == chave_val)]
    sub = sub[(sub['DTMOV'] >= janela_ini) & (sub['DTMOV'] < antes_de)]
    return not sub.empty

resultado = []
for rca, nome in RCAS.items():
    camp_rca = camp[camp['RCA'] == rca]

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
            sub = hist[(hist['RCA'] == rca) & (hist['CODCLI'] == r['CODCLI']) & (hist['CODPROD'] == r['CODPROD'])]
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
    'fontes_indisponiveis': FONTES_INDISPONIVEIS,
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
