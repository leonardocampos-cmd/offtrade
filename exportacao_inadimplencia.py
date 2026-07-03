"""
Gera inadimplencia_data.js com titulos em aberto (vencidos, sem pagamento)
por vendedor OFF TRADE (RJ/ES, filiais 2 e 4).
"""
import json
import pandas as pd
from datetime import datetime, date
from pathlib import Path

from meta import engine, carregar_dados

tabela_pedidos = carregar_dados("""
    SELECT P.NUMPED, P.DUPLIC, P.DTPAG, P.CODUSUR, U.NOME,
           S.NOME AS NOME_SUPERVISOR, G.NOMEGERENTE,
           P.CODCLI, C.CLIENTE, COALESCE(C.FANTASIA, '') AS FANTASIA,
           P.VALOR, P.DTVENC, P.CODCOB, P.VPAGO, P.CODFILIAL
    FROM crc.PCPREST P
    JOIN crc.PCUSUARI  U ON U.CODUSUR = P.CODUSUR
    LEFT JOIN crc.PCCLIENT  C ON C.CODCLI        = P.CODCLI
    LEFT JOIN crc.PCSUPERV  S ON U.CODSUPERVISOR = S.CODSUPERVISOR
    LEFT JOIN crc.PCGERENTE G ON S.CODGERENTE    = G.CODGERENTE
    WHERE P.CODFILIAL IN (2,4)
      AND U.NOME LIKE '%OFF TRADE%'
      AND P.DTPAG IS NULL
      AND P.DTVENC < TRUNC(SYSDATE)
    ORDER BY P.DTVENC ASC
""", engine, "inadimplencia")

tabela_pedidos.columns = tabela_pedidos.columns.str.upper()
tabela_pedidos['NOME_SUPERVISOR'] = tabela_pedidos['NOME_SUPERVISOR'].fillna('').astype(str).str.strip().replace('', 'Sem Supervisor')
tabela_pedidos['NOMEGERENTE']     = tabela_pedidos['NOMEGERENTE'].fillna('').astype(str).str.strip().replace('', 'Sem Gerente')

tabela_pedidos['CODUSUR_NUM'] = pd.to_numeric(tabela_pedidos['CODUSUR'], errors='coerce')
tabela_pedidos['CODCLI_NUM']  = pd.to_numeric(tabela_pedidos['CODCLI'],  errors='coerce')
tabela_pedidos['VALOR']       = pd.to_numeric(tabela_pedidos['VALOR'], errors='coerce').fillna(0)
tabela_pedidos['VPAGO']       = pd.to_numeric(tabela_pedidos['VPAGO'], errors='coerce').fillna(0)
tabela_pedidos['VALOR_ABERTO']= (tabela_pedidos['VALOR'] - tabela_pedidos['VPAGO']).round(2)
tabela_pedidos['DTVENC_DT']   = pd.to_datetime(tabela_pedidos['DTVENC'], errors='coerce')
tabela_pedidos['DTVENC_STR']  = tabela_pedidos['DTVENC_DT'].dt.strftime('%d/%m/%Y')
tabela_pedidos['DIAS_ATRASO'] = (pd.Timestamp(date.today()) - tabela_pedidos['DTVENC_DT']).dt.days
tabela_pedidos['CLIENTE']     = tabela_pedidos['CLIENTE'].fillna('').astype(str).str.strip()
tabela_pedidos['FANTASIA']    = tabela_pedidos['FANTASIA'].fillna('').astype(str).str.strip()
tabela_pedidos['CODCOB']      = tabela_pedidos['CODCOB'].fillna('').astype(str).str.strip()
tabela_pedidos['DUPLIC']      = tabela_pedidos['DUPLIC'].fillna('').astype(str).str.strip()

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

tabela_pedidos['NOME'] = (
    tabela_pedidos['CODUSUR_NUM']
    .map(_rca_to_display)
    .fillna(tabela_pedidos['NOME'].str.strip())
)


def _s(v):
    return '' if pd.isna(v) else str(v).strip()


def _s_num(v):
    """Formata numero sem casas decimais espurias (ex: 153000070.0 -> 153000070)."""
    if pd.isna(v):
        return ''
    n = pd.to_numeric(v, errors='coerce')
    return str(int(n)) if pd.notna(n) else _s(v)


vendedores_out = []
for nome, grp in tabela_pedidos.groupby('NOME'):
    titulos = []
    for _, r in grp.iterrows():
        titulos.append({
            'numped':       _s_num(r['NUMPED']),
            'duplic':       r['DUPLIC'],
            'codcli':       str(int(r['CODCLI_NUM'])) if pd.notna(r['CODCLI_NUM']) else '',
            'cliente':      r['CLIENTE'] or r['FANTASIA'],
            'fantasia':     r['FANTASIA'],
            'valor':        round(float(r['VALOR']), 2),
            'vpago':        round(float(r['VPAGO']), 2),
            'valor_aberto': round(float(r['VALOR_ABERTO']), 2),
            'dtvenc':       _s(r['DTVENC_STR']),
            'dias_atraso':  int(r['DIAS_ATRASO']) if pd.notna(r['DIAS_ATRASO']) else 0,
            'codcob':       r['CODCOB'],
            'codfilial':    _s(r['CODFILIAL']),
            'supervisor':   r['NOME_SUPERVISOR'],
            'gerente':      r['NOMEGERENTE'],
        })
    titulos.sort(key=lambda t: -t['dias_atraso'])
    total_aberto = round(sum(t['valor_aberto'] for t in titulos), 2)
    vendedores_out.append({
        'nome':         _s(nome),
        'supervisor':   grp['NOME_SUPERVISOR'].iloc[0],
        'gerente':      grp['NOMEGERENTE'].iloc[0],
        'qtd_titulos':  len(titulos),
        'total_aberto': total_aberto,
        'titulos':      titulos,
    })

vendedores_out.sort(key=lambda v: -v['total_aberto'])

total_geral  = round(sum(v['total_aberto'] for v in vendedores_out), 2)
qtd_geral    = sum(v['qtd_titulos'] for v in vendedores_out)
qtd_clientes = int(tabela_pedidos['CODCLI_NUM'].nunique())

payload = {
    'atualizado_em': datetime.now().strftime('%d/%m/%Y %H:%M'),
    'total_geral':   total_geral,
    'qtd_titulos':   qtd_geral,
    'qtd_clientes':  qtd_clientes,
    'vendedores':    vendedores_out,
}

out_path = Path(__file__).parent / "inadimplencia_data.js"
with open(out_path, 'w', encoding='utf-8') as f:
    f.write(f"// Gerado automaticamente\nconst INADIMPLENCIA_DATA = {json.dumps(payload, ensure_ascii=False, indent=2)};\n")

print(f"OK inadimplencia_data.js — {qtd_geral} titulo(s) em aberto, R$ {total_geral:,.2f}")

# Dado sensivel (nome de cliente + divida): NAO commitar/publicar no GitHub
# Pages publico. Fica so local + VPS, via deploy_static_vps.py.
