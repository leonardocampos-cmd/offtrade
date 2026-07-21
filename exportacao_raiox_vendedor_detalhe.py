"""
Gera raiox_vendedor_detalhe_data.js — ficha individual de cada vendedor OFF
TRADE (RJ): faturamento mensal, ranking de indústrias que ele mais vende e
ranking de melhores clientes. Alimenta raiox_vendedor_detalhe.html
(acessível a partir dos botões "Ver detalhe" em raiox_vendedores.html).
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

# RCA -> (nome, time). Mesma fonte de exportacao_raiox_vendedores.py.
TIMES = {
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
TIME_LABEL = {
    "KEY_ACCOUNT": "Key Account",
    "ATACAREJO": "Atacarejo (Pequeno e médio varejo)",
    "CONVENIENCE": "Convenience (Varejo tradicional e Conveniência)",
    "OUTROS": "Outros / sem time definido",
}

vendedores_off_trade = carregar_dados("""
    SELECT CODUSUR, NOME FROM CRC.PCUSUARI WHERE NOME LIKE '%OFF TRADE%' AND ESTADO = 'RJ'
""", engine, "raiox_venddet_vendedores")
vendedores_off_trade.columns = vendedores_off_trade.columns.str.upper()
_nome_por_rca = {
    int(r['CODUSUR']): r['NOME'].replace('- OFF TRADE', '').replace('-OFF TRADE', '').strip()
    for _, r in vendedores_off_trade.iterrows()
}
todos_rcas = sorted(_nome_por_rca)

vendas = carregar_dados(f"""
    SELECT M.CODUSUR, M.CODCLI, COALESCE(C.FANTASIA, C.CLIENTE) NOME_CLIENTE,
           COALESCE(F.FANTASIA,'SEM FANTASIA') AS FORNECEDOR,
           TRUNC(M.DTMOV,'MM') AS MES, SUM(M.PUNIT*M.QT) AS FATURAMENTO
    FROM CRC.PCMOV M
    JOIN CRC.PCCLIENT C ON M.CODCLI = C.CODCLI
    JOIN CRC.PCFORNEC F ON M.CODFORNEC = F.CODFORNEC
    JOIN CRC.PCUSUARI U ON M.CODUSUR = U.CODUSUR
    WHERE U.NOME LIKE '%OFF TRADE%' AND U.ESTADO = 'RJ'
      AND M.CODFILIAL IN (2,4)
      AND M.CODOPER = 'S'
      AND M.NUMNOTADEV IS NULL
      AND M.DTCANCEL IS NULL
      AND TRUNC(M.DTMOV) >= TO_DATE('{MES_INI}','YYYY-MM-DD')
      AND TRUNC(M.DTMOV) <= TO_DATE('{MES_FIM}','YYYY-MM-DD')
    GROUP BY M.CODUSUR, M.CODCLI, COALESCE(C.FANTASIA, C.CLIENTE),
             COALESCE(F.FANTASIA,'SEM FANTASIA'), TRUNC(M.DTMOV,'MM')
""", engine, "raiox_venddet_vendas")
vendas.columns = vendas.columns.str.upper()
vendas['MES'] = pd.to_datetime(vendas['MES'])
vendas['FORNECEDOR'] = vendas['FORNECEDOR'].fillna('SEM FANTASIA').str.strip()
vendas['NOME_CLIENTE'] = vendas['NOME_CLIENTE'].fillna('').str.strip()

meses_com_dado = sorted(vendas['MES'].dt.strftime('%Y-%m').unique())

vendedores = []
for rca in todos_rcas:
    nome, time_key = TIMES.get(rca, (_nome_por_rca.get(rca, f"RCA {rca}"), "OUTROS"))
    v_rca = vendas[vendas['CODUSUR'] == rca]

    fat_ytd = float(v_rca['FATURAMENTO'].sum())
    n_meses = v_rca['MES'].nunique() or 1
    media_mensal = fat_ytd / n_meses if not v_rca.empty else 0.0

    por_mes = {
        d.strftime('%Y-%m'): round(float(f), 2)
        for d, f in v_rca.groupby('MES')['FATURAMENTO'].sum().items()
    }

    top_industrias_s = v_rca.groupby('FORNECEDOR')['FATURAMENTO'].sum().sort_values(ascending=False)
    top_industrias = [
        {'fantasia': f, 'faturamento': round(float(v), 2),
         'pct': round(float(v) / fat_ytd * 100, 1) if fat_ytd else 0.0}
        for f, v in top_industrias_s.head(15).items()
    ]

    top_clientes_s = (
        v_rca.groupby(['CODCLI', 'NOME_CLIENTE'])['FATURAMENTO'].sum()
        .sort_values(ascending=False)
    )
    top_clientes = [
        {'codcli': int(codcli), 'nome': nome_cli or f"Cliente {codcli}", 'faturamento': round(float(v), 2)}
        for (codcli, nome_cli), v in top_clientes_s.head(15).items()
    ]

    vendedores.append({
        'rca': int(rca),
        'nome': nome,
        'time': time_key,
        'time_label': TIME_LABEL[time_key],
        'total_clientes_ativos': int(v_rca['CODCLI'].nunique()),
        'faturamento_ytd': round(fat_ytd, 2),
        'media_mensal': round(media_mensal, 2),
        'por_mes': por_mes,
        'top_industrias': top_industrias,
        'top_clientes': top_clientes,
    })

vendedores.sort(key=lambda v: v['faturamento_ytd'], reverse=True)

payload = {
    'atualizado_em': datetime.now().strftime('%d/%m/%Y %H:%M'),
    'meses_com_dado': meses_com_dado,
    'vendedores': vendedores,
}

out_path = Path(__file__).parent / "raiox_vendedor_detalhe_data.js"
with open(out_path, 'w', encoding='utf-8') as f:
    f.write(f"// Gerado automaticamente\nconst RAIOX_VENDEDOR_DETALHE_DATA = {json.dumps(payload, ensure_ascii=False, indent=2)};\n")

print(f"OK raiox_vendedor_detalhe_data.js — {len(vendedores)} vendedores")

repo_dir = str(Path(__file__).parent)
subprocess.run(["git", "-C", repo_dir, "add", "raiox_vendedor_detalhe_data.js", "raiox_vendedor_detalhe.html"], check=False)
subprocess.run(["git", "-C", repo_dir, "commit", "-m",
                f"Atualiza raiox_vendedor_detalhe_data.js - {date.today().strftime('%d/%m/%Y')}"])
subprocess.run(["git", "-C", repo_dir, "push", "origin", "master"], check=False)
print("OK GitHub Pages atualizado.")
