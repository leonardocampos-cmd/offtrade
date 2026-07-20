"""
Gera raiox_clientes_data.js — visão agregada da base de clientes OFF TRADE
(RJ, filiais 2 e 4) por canal (RAMO): total na base, cobertura mensal,
faturamento YTD e média mensal, distribuição por cidade, e o mesmo
recorte por vendedor (para o filtro na página).
"""
import json
import subprocess
from datetime import date, datetime
from pathlib import Path

import pandas as pd

from meta import engine, carregar_dados

ANO = 2026
MES_INI = f"{ANO}-01-01"
MES_FIM = f"{ANO}-07-31"

vendedores_off_trade = carregar_dados("""
    SELECT CODUSUR, NOME FROM CRC.PCUSUARI WHERE NOME LIKE '%OFF TRADE%' AND ESTADO = 'RJ'
""", engine, "raiox_clientes_vendedores")
vendedores_off_trade.columns = vendedores_off_trade.columns.str.upper()
_nome_por_rca = {
    int(r['CODUSUR']): r['NOME'].replace('- OFF TRADE', '').replace('-OFF TRADE', '').strip()
    for _, r in vendedores_off_trade.iterrows()
}

clientes = carregar_dados("""
    SELECT C.CODCLI, COALESCE(C.MUNICENT,'') CIDADE, COALESCE(A.RAMO,'OUTROS') RAMO,
           C.CODUSUR1, C.CODUSUR2
    FROM CRC.PCCLIENT C
    JOIN CRC.PCATIVI A ON C.CODATV1 = A.CODATIV
    LEFT JOIN CRC.PCUSUARI U1 ON C.CODUSUR1 = U1.CODUSUR
    LEFT JOIN CRC.PCUSUARI U2 ON C.CODUSUR2 = U2.CODUSUR
    WHERE (U1.NOME LIKE '%OFF TRADE%' OR U2.NOME LIKE '%OFF TRADE%')
""", engine, "raiox_clientes_base")
clientes.columns = clientes.columns.str.upper()
clientes['CIDADE'] = clientes['CIDADE'].fillna('').str.strip()
clientes['RAMO']   = clientes['RAMO'].fillna('OUTROS').str.strip()
for col in ('CODUSUR1', 'CODUSUR2'):
    clientes[col] = clientes[col].apply(lambda v: int(v) if str(v).strip().replace('.0', '').isdigit() else None)

vendas = carregar_dados(f"""
    SELECT M.CODCLI, COALESCE(A.RAMO,'OUTROS') RAMO, TRUNC(M.DTMOV,'MM') AS MES,
           SUM(M.PUNIT*M.QT) AS FATURAMENTO
    FROM CRC.PCMOV M
    JOIN CRC.PCCLIENT C ON M.CODCLI = C.CODCLI
    JOIN CRC.PCATIVI A ON C.CODATV1 = A.CODATIV
    LEFT JOIN CRC.PCUSUARI U1 ON C.CODUSUR1 = U1.CODUSUR
    LEFT JOIN CRC.PCUSUARI U2 ON C.CODUSUR2 = U2.CODUSUR
    WHERE (U1.NOME LIKE '%OFF TRADE%' OR U2.NOME LIKE '%OFF TRADE%')
      AND M.CODFILIAL IN (2,4)
      AND M.CODOPER = 'S'
      AND M.NUMNOTADEV IS NULL
      AND M.DTCANCEL IS NULL
      AND TRUNC(M.DTMOV) >= TO_DATE('{MES_INI}','YYYY-MM-DD')
      AND TRUNC(M.DTMOV) <= TO_DATE('{MES_FIM}','YYYY-MM-DD')
    GROUP BY M.CODCLI, COALESCE(A.RAMO,'OUTROS'), TRUNC(M.DTMOV,'MM')
""", engine, "raiox_clientes_vendas")
vendas.columns = vendas.columns.str.upper()
vendas['MES'] = pd.to_datetime(vendas['MES'])
vendas['RAMO'] = vendas['RAMO'].fillna('OUTROS').str.strip()
# CODUSUR1 do cliente (para poder filtrar vendas por vendedor)
vendas = vendas.merge(clientes[['CODCLI', 'CODUSUR1', 'CODUSUR2']].drop_duplicates('CODCLI'), on='CODCLI', how='left')

mes_atual = vendas['MES'].max()
meses_com_dado = sorted(vendas['MES'].dt.strftime('%Y-%m').unique())


def _metricas(base_clientes, vendas_recorte):
    """Calcula os KPIs de um recorte (canal, ou canal+vendedor)."""
    total_base = base_clientes['CODCLI'].nunique()
    v_mes_atual = vendas_recorte[vendas_recorte['MES'] == mes_atual]
    clientes_ativos_mes = int(v_mes_atual['CODCLI'].nunique())
    cobertura_pct = round(clientes_ativos_mes / total_base * 100, 1) if total_base else 0.0

    fat_ytd = float(vendas_recorte['FATURAMENTO'].sum())
    n_meses = vendas_recorte['MES'].nunique() or 1
    media_mensal = fat_ytd / n_meses if vendas_recorte.shape[0] else 0.0

    por_mes = {
        d.strftime('%Y-%m'): round(float(f), 2)
        for d, f in vendas_recorte.groupby('MES')['FATURAMENTO'].sum().items()
    }
    top_cidades = (
        base_clientes.groupby('CIDADE')['CODCLI'].nunique()
        .sort_values(ascending=False)
        .head(10)
    )
    return {
        'total_clientes': int(total_base),
        'clientes_ativos_mes': clientes_ativos_mes,
        'cobertura_pct': cobertura_pct,
        'faturamento_ytd': round(fat_ytd, 2),
        'media_mensal': round(media_mensal, 2),
        'por_mes': por_mes,
        'top_cidades': [
            {'cidade': c or 'N/D', 'clientes': int(n)}
            for c, n in top_cidades.items()
        ],
    }


rcas_com_cliente = sorted(
    (set(clientes['CODUSUR1'].dropna()) | set(clientes['CODUSUR2'].dropna()))
    & set(_nome_por_rca)
)

canais = []
for ramo, base_ramo in clientes.groupby('RAMO'):
    v_ramo = vendas[vendas['RAMO'] == ramo]
    canal = {'ramo': ramo, **_metricas(base_ramo, v_ramo)}

    por_vendedor = {}
    for rca in rcas_com_cliente:
        base_rca = base_ramo[(base_ramo['CODUSUR1'] == rca) | (base_ramo['CODUSUR2'] == rca)]
        if base_rca.empty:
            continue
        v_rca = v_ramo[(v_ramo['CODUSUR1'] == rca) | (v_ramo['CODUSUR2'] == rca)]
        por_vendedor[str(rca)] = _metricas(base_rca, v_rca)
    canal['por_vendedor'] = por_vendedor
    canais.append(canal)

canais.sort(key=lambda c: c['faturamento_ytd'], reverse=True)

vendedores_lista = sorted(
    ({'rca': rca, 'nome': _nome_por_rca.get(rca, f"RCA {rca}")} for rca in rcas_com_cliente),
    key=lambda v: v['nome']
)

payload = {
    'atualizado_em': datetime.now().strftime('%d/%m/%Y %H:%M'),
    'mes_atual': mes_atual.strftime('%m/%Y'),
    'meses_com_dado': meses_com_dado,
    'vendedores': vendedores_lista,
    'canais': canais,
}

out_path = Path(__file__).parent / "raiox_clientes_data.js"
with open(out_path, 'w', encoding='utf-8') as f:
    f.write(f"// Gerado automaticamente\nconst RAIOX_CLIENTES_DATA = {json.dumps(payload, ensure_ascii=False, indent=2)};\n")

print(f"OK raiox_clientes_data.js — {len(canais)} canais, {len(vendedores_lista)} vendedores, mes atual {mes_atual.strftime('%m/%Y')}")

repo_dir = str(Path(__file__).parent)
subprocess.run(["git", "-C", repo_dir, "add", "raiox_clientes_data.js", "raiox_clientes.html"], check=False)
subprocess.run(["git", "-C", repo_dir, "commit", "-m",
                f"Atualiza raiox_clientes_data.js - {date.today().strftime('%d/%m/%Y')}"])
subprocess.run(["git", "-C", repo_dir, "push", "origin", "master"], check=False)
print("OK GitHub Pages atualizado.")
