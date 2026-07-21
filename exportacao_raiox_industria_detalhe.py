"""
Gera raiox_industria_detalhe_data.js — ficha individual de cada indústria/
fornecedor OFF TRADE (RJ, filiais 2 e 4): faturamento mensal, ranking de
melhores clientes e ranking de vendedores que mais vendem essa indústria.
Alimenta raiox_industria_detalhe.html (acessível a partir dos botões
"Ver detalhe" em raiox_industrias.html).
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

vendas = carregar_dados(f"""
    SELECT COALESCE(F.FANTASIA,'SEM FANTASIA') AS FORNECEDOR,
           M.CODCLI, COALESCE(C.FANTASIA, C.CLIENTE) NOME_CLIENTE,
           M.CODUSUR, U.NOME NOME_VENDEDOR,
           TRUNC(M.DTMOV,'MM') AS MES, SUM(M.PUNIT*M.QT) AS FATURAMENTO
    FROM CRC.PCMOV M
    JOIN CRC.PCCLIENT C ON M.CODCLI = C.CODCLI
    JOIN CRC.PCFORNEC F ON M.CODFORNEC = F.CODFORNEC
    JOIN CRC.PCUSUARI U ON M.CODUSUR = U.CODUSUR
    WHERE U.NOME LIKE '%OFF TRADE%'
      AND M.CODFILIAL IN (2,4)
      AND M.CODOPER = 'S'
      AND M.NUMNOTADEV IS NULL
      AND M.DTCANCEL IS NULL
      AND TRUNC(M.DTMOV) >= TO_DATE('{MES_INI}','YYYY-MM-DD')
      AND TRUNC(M.DTMOV) <= TO_DATE('{MES_FIM}','YYYY-MM-DD')
    GROUP BY COALESCE(F.FANTASIA,'SEM FANTASIA'), M.CODCLI, COALESCE(C.FANTASIA, C.CLIENTE),
             M.CODUSUR, U.NOME, TRUNC(M.DTMOV,'MM')
""", engine, "raiox_induddet_vendas")
vendas.columns = vendas.columns.str.upper()
vendas['MES'] = pd.to_datetime(vendas['MES'])
vendas['FORNECEDOR'] = vendas['FORNECEDOR'].fillna('SEM FANTASIA').str.strip()
vendas['NOME_CLIENTE'] = vendas['NOME_CLIENTE'].fillna('').str.strip()
vendas['NOME_VENDEDOR'] = vendas['NOME_VENDEDOR'].fillna('').str.replace('- OFF TRADE', '', regex=False).str.replace('-OFF TRADE', '', regex=False).str.strip()

meses_com_dado = sorted(vendas['MES'].dt.strftime('%Y-%m').unique())
faturamento_total = float(vendas['FATURAMENTO'].sum())

fornecedores = []
for fantasia, grp in vendas.groupby('FORNECEDOR'):
    fat_ytd = float(grp['FATURAMENTO'].sum())
    n_meses = grp['MES'].nunique() or 1
    media_mensal = fat_ytd / n_meses if not grp.empty else 0.0

    por_mes = {
        d.strftime('%Y-%m'): round(float(f), 2)
        for d, f in grp.groupby('MES')['FATURAMENTO'].sum().items()
    }

    top_clientes_s = (
        grp.groupby(['CODCLI', 'NOME_CLIENTE'])['FATURAMENTO'].sum()
        .sort_values(ascending=False)
    )
    top_clientes = [
        {'codcli': int(codcli), 'nome': nome_cli or f"Cliente {codcli}", 'faturamento': round(float(v), 2)}
        for (codcli, nome_cli), v in top_clientes_s.head(15).items()
    ]

    top_vendedores_s = (
        grp.groupby(['CODUSUR', 'NOME_VENDEDOR'])['FATURAMENTO'].sum()
        .sort_values(ascending=False)
    )
    top_vendedores = [
        {'rca': int(rca), 'nome': nome_v or f"RCA {int(rca)}", 'faturamento': round(float(v), 2)}
        for (rca, nome_v), v in top_vendedores_s.head(15).items() if pd.notna(rca)
    ]

    fornecedores.append({
        'fantasia': fantasia,
        'faturamento_ytd': round(fat_ytd, 2),
        'participacao_pct': round(fat_ytd / faturamento_total * 100, 2) if faturamento_total else 0.0,
        'media_mensal': round(media_mensal, 2),
        'clientes_positivados': int(grp['CODCLI'].nunique()),
        'por_mes': por_mes,
        'top_clientes': top_clientes,
        'top_vendedores': top_vendedores,
    })

fornecedores.sort(key=lambda f: f['faturamento_ytd'], reverse=True)
for i, f in enumerate(fornecedores, start=1):
    f['posicao'] = i

payload = {
    'atualizado_em': datetime.now().strftime('%d/%m/%Y %H:%M'),
    'meses_com_dado': meses_com_dado,
    'faturamento_total_ytd': round(faturamento_total, 2),
    'fornecedores': fornecedores,
}

out_path = Path(__file__).parent / "raiox_industria_detalhe_data.js"
with open(out_path, 'w', encoding='utf-8') as f:
    f.write(f"// Gerado automaticamente\nconst RAIOX_INDUSTRIA_DETALHE_DATA = {json.dumps(payload, ensure_ascii=False, indent=2)};\n")

print(f"OK raiox_industria_detalhe_data.js — {len(fornecedores)} fornecedores")

repo_dir = str(Path(__file__).parent)
subprocess.run(["git", "-C", repo_dir, "add", "raiox_industria_detalhe_data.js", "raiox_industria_detalhe.html"], check=False)
subprocess.run(["git", "-C", repo_dir, "commit", "-m",
                f"Atualiza raiox_industria_detalhe_data.js - {date.today().strftime('%d/%m/%Y')}"])
subprocess.run(["git", "-C", repo_dir, "push", "origin", "master"], check=False)
print("OK GitHub Pages atualizado.")
