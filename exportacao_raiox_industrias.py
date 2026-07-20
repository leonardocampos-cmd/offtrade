"""
Gera raiox_industrias_data.js — ranking de fornecedores (indústrias) OFF
TRADE (RJ, filiais 2 e 4): faturamento YTD, participação % no total,
evolução mensal e clientes positivados por fornecedor.
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

dados = carregar_dados(f"""
    SELECT F.FANTASIA, TRUNC(M.DTMOV,'MM') AS MES,
           SUM(M.PUNIT*M.QT) AS FATURAMENTO,
           COUNT(DISTINCT M.CODCLI) AS CLIENTES
    FROM CRC.PCMOV M
    JOIN CRC.PCUSUARI U ON M.CODUSUR = U.CODUSUR
    JOIN CRC.PCFORNEC F ON M.CODFORNEC = F.CODFORNEC
    WHERE U.NOME LIKE '%OFF TRADE%'
      AND M.CODFILIAL IN (2,4)
      AND M.CODOPER = 'S'
      AND M.NUMNOTADEV IS NULL
      AND M.DTCANCEL IS NULL
      AND TRUNC(M.DTMOV) >= TO_DATE('{MES_INI}','YYYY-MM-DD')
      AND TRUNC(M.DTMOV) <= TO_DATE('{MES_FIM}','YYYY-MM-DD')
    GROUP BY F.FANTASIA, TRUNC(M.DTMOV,'MM')
""", engine, "raiox_industrias")
dados.columns = dados.columns.str.upper()
dados['FANTASIA'] = dados['FANTASIA'].fillna('SEM FANTASIA').str.strip()
dados['MES'] = pd.to_datetime(dados['MES'])

faturamento_total = float(dados['FATURAMENTO'].sum())
mes_atual = dados['MES'].max()

# Clientes positivados por fornecedor precisa ser distinto no periodo todo,
# nao soma dos meses (cliente que compra todo mes nao pode contar 7x)
clientes_periodo = carregar_dados(f"""
    SELECT F.FANTASIA, COUNT(DISTINCT M.CODCLI) AS CLIENTES_PERIODO
    FROM CRC.PCMOV M
    JOIN CRC.PCUSUARI U ON M.CODUSUR = U.CODUSUR
    JOIN CRC.PCFORNEC F ON M.CODFORNEC = F.CODFORNEC
    WHERE U.NOME LIKE '%OFF TRADE%'
      AND M.CODFILIAL IN (2,4)
      AND M.CODOPER = 'S'
      AND M.NUMNOTADEV IS NULL
      AND M.DTCANCEL IS NULL
      AND TRUNC(M.DTMOV) >= TO_DATE('{MES_INI}','YYYY-MM-DD')
      AND TRUNC(M.DTMOV) <= TO_DATE('{MES_FIM}','YYYY-MM-DD')
    GROUP BY F.FANTASIA
""", engine, "raiox_industrias_clientes_periodo")
clientes_periodo.columns = clientes_periodo.columns.str.upper()
clientes_periodo['FANTASIA'] = clientes_periodo['FANTASIA'].fillna('SEM FANTASIA').str.strip()
_clientes_map = dict(zip(clientes_periodo['FANTASIA'], clientes_periodo['CLIENTES_PERIODO']))

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
}

out_path = Path(__file__).parent / "raiox_industrias_data.js"
with open(out_path, 'w', encoding='utf-8') as f:
    f.write(f"// Gerado automaticamente\nconst RAIOX_INDUSTRIAS_DATA = {json.dumps(payload, ensure_ascii=False, indent=2)};\n")

print(f"OK raiox_industrias_data.js — {len(fornecedores)} fornecedores, faturamento total R$ {faturamento_total:,.2f}")

repo_dir = str(Path(__file__).parent)
subprocess.run(["git", "-C", repo_dir, "add", "raiox_industrias_data.js", "raiox_industrias.html"], check=False)
subprocess.run(["git", "-C", repo_dir, "commit", "-m",
                f"Atualiza raiox_industrias_data.js - {date.today().strftime('%d/%m/%Y')}"])
subprocess.run(["git", "-C", repo_dir, "push", "origin", "master"], check=False)
print("OK GitHub Pages atualizado.")
