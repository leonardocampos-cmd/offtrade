"""
Gera pedidos_data.js: visão única (todos os vendedores OFF TRADE juntos) dos
pedidos dos últimos 90 dias, separados em Pedidos Feitos (ainda sem nota),
Faturados (com nota, não cancelados) e Cancelados.

Mesma fonte/filtro do entregas.py (crc.PBI_PCPEDI, CODFILIAL IN (2,4), NOME
LIKE '%OFF TRADE%'), mas sem o corte por mês corrente nem o cruzamento com a
planilha de logística — aqui é só o retrato dos pedidos em si.
"""
import json
from datetime import datetime
from pathlib import Path

import pandas as pd

from meta import engine, carregar_dados

DIAS_JANELA = 90

tabela_pedidos = carregar_dados(f"""
    SELECT NUMPED, NUMNOTA, NOME, DATA, CODUSUR, CLIENTE, STATUS,
           DESCRICAO, PVENDA, QT, TOTAL, OBSENTREGA1
    FROM crc.PBI_PCPEDI
    WHERE CODFILIAL IN (2,4)
      AND NOME LIKE '%OFF TRADE%'
      AND DATA >= SYSDATE - {DIAS_JANELA}
    ORDER BY DATA DESC
""", engine, "pedidos")

tabela_pedidos.columns = tabela_pedidos.columns.str.upper()
tabela_pedidos['TOTAL']       = pd.to_numeric(tabela_pedidos['TOTAL'], errors='coerce').fillna(0)
tabela_pedidos['QT']          = pd.to_numeric(tabela_pedidos['QT'],    errors='coerce').fillna(0).astype(int)
tabela_pedidos['NUMNOTA_NUM'] = pd.to_numeric(tabela_pedidos['NUMNOTA'], errors='coerce')
tabela_pedidos['DATA_DT']     = pd.to_datetime(tabela_pedidos['DATA'], errors='coerce')
tabela_pedidos['DATA']        = tabela_pedidos['DATA_DT'].dt.strftime('%d/%m/%Y')
tabela_pedidos['STATUS']      = tabela_pedidos['STATUS'].fillna('').astype(str).str.strip()

# ── Mapeia Oracle name → display name (igual ao entregas.py) ──────────────────

try:
    from meta import arquivo as _arquivo_meta
    _arq = _arquivo_meta[['RCA', 'VENDEDOR']].copy()
    _arq['RCA'] = pd.to_numeric(_arq['RCA'], errors='coerce')
    _arq = _arq.dropna(subset=['RCA', 'VENDEDOR']).drop_duplicates('RCA')
    _rca_to_display = dict(zip(_arq['RCA'], _arq['VENDEDOR'].str.strip()))
except Exception as e:
    print(f"Aviso: mapeamento de nomes falhou ({e}), usando nomes Oracle.")
    _rca_to_display = {}

tabela_pedidos['CODUSUR_NUM'] = pd.to_numeric(tabela_pedidos['CODUSUR'], errors='coerce')
tabela_pedidos['NOME'] = (
    tabela_pedidos['CODUSUR_NUM']
    .map(_rca_to_display)
    .fillna(tabela_pedidos['NOME'].str.strip())
)

# ── Separação em 3 baldes mutuamente exclusivos ────────────────────────────────

_cancelados = tabela_pedidos[tabela_pedidos['STATUS'] == 'CANCELADA']
_faturados  = tabela_pedidos[
    tabela_pedidos['NUMNOTA_NUM'].notna() & (tabela_pedidos['STATUS'] != 'CANCELADA')
]
_feitos     = tabela_pedidos[
    tabela_pedidos['NUMNOTA_NUM'].isna() & (tabela_pedidos['STATUS'] != 'CANCELADA')
]


def _s(v):
    return '' if pd.isna(v) else str(v).strip()


def _nf_clean(numnota):
    if pd.isna(numnota) or not numnota:
        return ''
    try:
        return str(int(float(numnota)))
    except (TypeError, ValueError):
        return str(numnota).strip()


def _agrupar(df):
    result = []
    for numped, grp in df.groupby('NUMPED', sort=False):
        r0 = grp.iloc[0]
        result.append({
            'numped':     _s(numped),
            'numnota':    _nf_clean(r0.get('NUMNOTA', '')),
            'data':       _s(r0['DATA']),
            'nome':       _s(r0['NOME']),
            'cliente':    _s(r0['CLIENTE']),
            'status_ped': _s(r0['STATUS']),
            'obs':        _s(r0['OBSENTREGA1']),
            'total':      round(float(grp['TOTAL'].sum()), 2),
            'itens': [
                {
                    'desc': _s(row['DESCRICAO']),
                    'qt':   int(row['QT']),
                    'val':  round(float(row['TOTAL']), 2),
                }
                for _, row in grp.iterrows()
            ],
        })
    result.sort(key=lambda p: p['data'], reverse=True)
    return result


payload = {
    'atualizado_em':  datetime.now().strftime('%d/%m/%Y %H:%M'),
    'periodo_dias':   DIAS_JANELA,
    'pedidos_feitos': _agrupar(_feitos),
    'faturados':      _agrupar(_faturados),
    'cancelados':     _agrupar(_cancelados),
}

out = Path(__file__).parent / 'pedidos_data.js'
with open(out, 'w', encoding='utf-8') as f:
    f.write(f"const PEDIDOS_DATA = {json.dumps(payload, ensure_ascii=False, indent=2)};\n")

print(
    f"OK - {len(payload['pedidos_feitos'])} pedido(s) feito(s), "
    f"{len(payload['faturados'])} faturado(s), "
    f"{len(payload['cancelados'])} cancelado(s) -> {out}"
)

import subprocess

repo_dir = str(Path(__file__).parent)
try:
    subprocess.run(["git", "-C", repo_dir, "add", "pedidos_data.js"], check=True)
    subprocess.run(["git", "-C", repo_dir, "commit", "-m",
                    f"Atualiza pedidos_data.js - {datetime.now().strftime('%d/%m/%Y')}"])
    subprocess.run(["git", "-C", repo_dir, "push", "origin", "master"], check=True)
    print("OK pedidos_data.js enviado ao GitHub Pages.")
except subprocess.CalledProcessError:
    print("[AVISO] git push falhou — ignorado, pipeline continua.")
