"""
Gera raiox_industrias_data.js — ranking de fornecedores (indústrias) OFF
TRADE em todas as bases (RJ e ES via CRC, SP via SPON, MG via MGON):
faturamento YTD, participação % no total, evolução mensal e clientes
positivados por fornecedor.
"""
import json
import subprocess
from datetime import date, datetime
from pathlib import Path

import pandas as pd

from meta import engine, engine_spon, engine_mgon, carregar_dados

ANO = 2026
MES_INI = f"{ANO}-01-01"
MES_FIM = f"{ANO}-07-31"

BASES = [
    {"estado": "RJ", "engine": engine,      "schema": "CRC",  "filiais": ["2", "4"]},
    {"estado": "ES", "engine": engine,      "schema": "CRC",  "filiais": ["1"]},
    {"estado": "SP", "engine": engine_spon, "schema": "SPON", "filiais": ["1", "2"]},
    {"estado": "MG", "engine": engine_mgon, "schema": "MGON", "filiais": ["1", "2"]},
]


def _query(schema, filiais):
    fil_clause = f"AND M.CODFILIAL IN ({','.join(filiais)})" if filiais else ""
    return f"""
        SELECT F.FANTASIA, TRUNC(M.DTMOV,'MM') AS MES,
               SUM(M.PUNIT*M.QT) AS FATURAMENTO,
               COUNT(DISTINCT M.CODCLI) AS CLIENTES
        FROM {schema}.PCMOV M
        JOIN {schema}.PCUSUARI U ON M.CODUSUR = U.CODUSUR
        JOIN {schema}.PCFORNEC F ON M.CODFORNEC = F.CODFORNEC
        WHERE U.NOME LIKE '%OFF TRADE%'
          {fil_clause}
          AND M.CODOPER = 'S'
          AND M.NUMNOTADEV IS NULL
          AND M.DTCANCEL IS NULL
          AND TRUNC(M.DTMOV) >= TO_DATE('{MES_INI}','YYYY-MM-DD')
          AND TRUNC(M.DTMOV) <= TO_DATE('{MES_FIM}','YYYY-MM-DD')
        GROUP BY F.FANTASIA, TRUNC(M.DTMOV,'MM')
    """


def _query_clientes_periodo(schema, filiais):
    fil_clause = f"AND M.CODFILIAL IN ({','.join(filiais)})" if filiais else ""
    return f"""
        SELECT F.FANTASIA, M.CODCLI
        FROM {schema}.PCMOV M
        JOIN {schema}.PCUSUARI U ON M.CODUSUR = U.CODUSUR
        JOIN {schema}.PCFORNEC F ON M.CODFORNEC = F.CODFORNEC
        WHERE U.NOME LIKE '%OFF TRADE%'
          {fil_clause}
          AND M.CODOPER = 'S'
          AND M.NUMNOTADEV IS NULL
          AND M.DTCANCEL IS NULL
          AND TRUNC(M.DTMOV) >= TO_DATE('{MES_INI}','YYYY-MM-DD')
          AND TRUNC(M.DTMOV) <= TO_DATE('{MES_FIM}','YYYY-MM-DD')
        GROUP BY F.FANTASIA, M.CODCLI
    """


_partes, _clientes_partes = [], []
fontes_indisponiveis = []

for base in BASES:
    estado, eng, schema, filiais = base["estado"], base["engine"], base["schema"], base["filiais"]
    try:
        d = carregar_dados(_query(schema, filiais), eng, f"raiox_industrias_{estado}")
        d.columns = d.columns.str.upper()
        d['ESTADO'] = estado
        _partes.append(d)

        # Clientes positivados por fornecedor precisa ser distinto no período todo
        # (não soma dos meses) e distinto entre estados (CODCLI não é único entre bases).
        cp = carregar_dados(_query_clientes_periodo(schema, filiais), eng, f"raiox_industrias_clientes_{estado}")
        cp.columns = cp.columns.str.upper()
        cp['CLIENTE_KEY'] = estado + '-' + cp['CODCLI'].astype(str)
        _clientes_partes.append(cp[['FANTASIA', 'CLIENTE_KEY']])
        print(f"  OK {estado}: {len(d)} linhas")
    except Exception as e:
        print(f"  [AVISO] {estado} falhou ({str(e)[:150]}) — ignorado")
        fontes_indisponiveis.append(estado)

dados = pd.concat(_partes, ignore_index=True) if _partes else pd.DataFrame(columns=['FANTASIA', 'MES', 'FATURAMENTO', 'CLIENTES', 'ESTADO'])
dados['FANTASIA'] = dados['FANTASIA'].fillna('SEM FANTASIA').str.strip()
dados['MES'] = pd.to_datetime(dados['MES'])

faturamento_total = float(dados['FATURAMENTO'].sum())
mes_atual = dados['MES'].max()

clientes_periodo = pd.concat(_clientes_partes, ignore_index=True) if _clientes_partes else pd.DataFrame(columns=['FANTASIA', 'CLIENTE_KEY'])
clientes_periodo['FANTASIA'] = clientes_periodo['FANTASIA'].fillna('SEM FANTASIA').str.strip()
_clientes_map = clientes_periodo.groupby('FANTASIA')['CLIENTE_KEY'].nunique().to_dict()

fornecedores = []
for fantasia, grp in dados.groupby('FANTASIA'):
    fat_ytd = float(grp['FATURAMENTO'].sum())
    por_mes = {
        d.strftime('%Y-%m'): round(float(f), 2)
        for d, f in grp.groupby('MES')['FATURAMENTO'].sum().items()
    }
    fat_mes_atual = float(grp[grp['MES'] == mes_atual]['FATURAMENTO'].sum())
    fornecedores.append({
        'fantasia': fantasia,
        'faturamento_ytd': round(fat_ytd, 2),
        'participacao_pct': round(fat_ytd / faturamento_total * 100, 2) if faturamento_total else 0.0,
        'faturamento_mes_atual': round(fat_mes_atual, 2),
        'clientes_positivados': int(_clientes_map.get(fantasia, 0)),
        'por_mes': por_mes,
    })

fornecedores.sort(key=lambda f: f['faturamento_ytd'], reverse=True)
for i, f in enumerate(fornecedores, start=1):
    f['posicao'] = i

payload = {
    'atualizado_em': datetime.now().strftime('%d/%m/%Y %H:%M'),
    'mes_atual': mes_atual.strftime('%m/%Y'),
    'faturamento_total_ytd': round(faturamento_total, 2),
    'fornecedores': fornecedores,
    'fontes_indisponiveis': fontes_indisponiveis,
}

out_path = Path(__file__).parent / "raiox_industrias_data.js"
with open(out_path, 'w', encoding='utf-8') as f:
    f.write(f"// Gerado automaticamente\nconst RAIOX_INDUSTRIAS_DATA = {json.dumps(payload, ensure_ascii=False, indent=2)};\n")

print(f"OK raiox_industrias_data.js — {len(fornecedores)} fornecedores, faturamento total R$ {faturamento_total:,.2f}")
if fontes_indisponiveis:
    print(f"[AVISO] Fontes indisponíveis: {fontes_indisponiveis}")

repo_dir = str(Path(__file__).parent)
subprocess.run(["git", "-C", repo_dir, "add", "raiox_industrias_data.js", "raiox_industrias.html"], check=False)
subprocess.run(["git", "-C", repo_dir, "commit", "-m",
                f"Atualiza raiox_industrias_data.js - {date.today().strftime('%d/%m/%Y')}"])
subprocess.run(["git", "-C", repo_dir, "push", "origin", "master"], check=False)
print("OK GitHub Pages atualizado.")
