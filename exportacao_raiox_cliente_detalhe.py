"""
Gera raiox_cliente_detalhe_data.js — ficha individual de cada cliente OFF
TRADE (RJ, filiais 2 e 4): dados cadastrais, histórico mensal de compras,
faturamento por vendedor (quem efetivamente vendeu, via PCMOV.CODUSUR) e
por indústria/fornecedor. Alimenta raiox_cliente_detalhe.html (busca por
nome, acessível a partir dos botões "Ver clientes" de cada ramo em
raiox_clientes.html).
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
""", engine, "raiox_clidet_vendedores")
vendedores_off_trade.columns = vendedores_off_trade.columns.str.upper()
_nome_por_rca = {
    int(r['CODUSUR']): r['NOME'].replace('- OFF TRADE', '').replace('-OFF TRADE', '').strip()
    for _, r in vendedores_off_trade.iterrows()
}

clientes = carregar_dados("""
    SELECT C.CODCLI, C.CLIENTE, COALESCE(C.FANTASIA, C.CLIENTE) FANTASIA,
           COALESCE(C.MUNICENT,'') CIDADE, COALESCE(A.RAMO,'OUTROS') RAMO,
           C.CODUSUR1, C.CODUSUR2, C.DTULTCOMP
    FROM CRC.PCCLIENT C
    JOIN CRC.PCATIVI A ON C.CODATV1 = A.CODATIV
    LEFT JOIN CRC.PCUSUARI U1 ON C.CODUSUR1 = U1.CODUSUR
    LEFT JOIN CRC.PCUSUARI U2 ON C.CODUSUR2 = U2.CODUSUR
    WHERE (U1.NOME LIKE '%OFF TRADE%' OR U2.NOME LIKE '%OFF TRADE%')
""", engine, "raiox_clidet_base")
clientes.columns = clientes.columns.str.upper()
clientes['CIDADE']   = clientes['CIDADE'].fillna('').str.strip()
clientes['RAMO']     = clientes['RAMO'].fillna('OUTROS').str.strip()
clientes['FANTASIA'] = clientes['FANTASIA'].fillna('').str.strip()
clientes['CLIENTE']  = clientes['CLIENTE'].fillna('').str.strip()
for col in ('CODUSUR1', 'CODUSUR2'):
    clientes[col] = clientes[col].apply(lambda v: int(v) if str(v).strip().replace('.0', '').isdigit() else None)

vendas = carregar_dados(f"""
    SELECT M.CODCLI, TRUNC(M.DTMOV,'MM') AS MES,
           COALESCE(F.FANTASIA,'SEM FANTASIA') AS FORNECEDOR,
           M.CODUSUR, SUM(M.PUNIT*M.QT) AS FATURAMENTO
    FROM CRC.PCMOV M
    JOIN CRC.PCCLIENT C ON M.CODCLI = C.CODCLI
    JOIN CRC.PCFORNEC F ON M.CODFORNEC = F.CODFORNEC
    LEFT JOIN CRC.PCUSUARI U1 ON C.CODUSUR1 = U1.CODUSUR
    LEFT JOIN CRC.PCUSUARI U2 ON C.CODUSUR2 = U2.CODUSUR
    WHERE (U1.NOME LIKE '%OFF TRADE%' OR U2.NOME LIKE '%OFF TRADE%')
      AND M.CODFILIAL IN (2,4)
      AND M.CODOPER = 'S'
      AND M.NUMNOTADEV IS NULL
      AND M.DTCANCEL IS NULL
      AND TRUNC(M.DTMOV) >= TO_DATE('{MES_INI}','YYYY-MM-DD')
      AND TRUNC(M.DTMOV) <= TO_DATE('{MES_FIM}','YYYY-MM-DD')
    GROUP BY M.CODCLI, TRUNC(M.DTMOV,'MM'), COALESCE(F.FANTASIA,'SEM FANTASIA'), M.CODUSUR
""", engine, "raiox_clidet_vendas")
vendas.columns = vendas.columns.str.upper()
vendas['MES'] = pd.to_datetime(vendas['MES'])
vendas['FORNECEDOR'] = vendas['FORNECEDOR'].fillna('SEM FANTASIA').str.strip()
vendas['CODUSUR'] = vendas['CODUSUR'].apply(lambda v: int(v) if str(v).strip().replace('.0', '').isdigit() else None)

meses_com_dado = sorted(vendas['MES'].dt.strftime('%Y-%m').unique())

clientes_com_venda = set(vendas['CODCLI'].unique())

registros = []
for _, c in clientes.iterrows():
    codcli = c['CODCLI']
    v_cli = vendas[vendas['CODCLI'] == codcli]

    fat_ytd = float(v_cli['FATURAMENTO'].sum())
    n_meses = v_cli['MES'].nunique() or 1
    media_mensal = fat_ytd / n_meses if not v_cli.empty else 0.0

    por_mes = {
        d.strftime('%Y-%m'): round(float(f), 2)
        for d, f in v_cli.groupby('MES')['FATURAMENTO'].sum().items()
    }

    por_industria = (
        v_cli.groupby('FORNECEDOR')['FATURAMENTO'].sum()
        .sort_values(ascending=False)
    )
    top_industrias = [
        {'fantasia': f, 'faturamento': round(float(v), 2),
         'pct': round(float(v) / fat_ytd * 100, 1) if fat_ytd else 0.0}
        for f, v in por_industria.items()
    ]

    por_vendedor_s = v_cli.groupby('CODUSUR')['FATURAMENTO'].sum().sort_values(ascending=False)
    top_vendedores = [
        {'rca': int(rca), 'nome': _nome_por_rca.get(int(rca), f"RCA {int(rca)}"), 'faturamento': round(float(v), 2)}
        for rca, v in por_vendedor_s.items() if pd.notna(rca)
    ]

    vendedores_cadastro = [
        {'rca': rca, 'nome': _nome_por_rca[rca]}
        for rca in (c['CODUSUR1'], c['CODUSUR2'])
        if rca in _nome_por_rca
    ]

    ultima_compra = ''
    if pd.notna(c.get('DTULTCOMP')):
        try:
            ultima_compra = pd.to_datetime(c['DTULTCOMP']).strftime('%d/%m/%Y')
        except Exception:
            ultima_compra = ''

    registros.append({
        'codcli': int(codcli),
        'nome': c['FANTASIA'] or c['CLIENTE'] or f"Cliente {codcli}",
        'razao_social': c['CLIENTE'],
        'cidade': c['CIDADE'] or 'N/D',
        'ramo': c['RAMO'],
        'vendedores_cadastro': vendedores_cadastro,
        'ultima_compra': ultima_compra,
        'ativo_periodo': codcli in clientes_com_venda,
        'faturamento_ytd': round(fat_ytd, 2),
        'media_mensal': round(media_mensal, 2),
        'por_mes': por_mes,
        'top_industrias': top_industrias,
        'top_vendedores': top_vendedores,
    })

registros.sort(key=lambda r: r['nome'])

payload = {
    'atualizado_em': datetime.now().strftime('%d/%m/%Y %H:%M'),
    'meses_com_dado': meses_com_dado,
    'clientes': registros,
}

out_path = Path(__file__).parent / "raiox_cliente_detalhe_data.js"
with open(out_path, 'w', encoding='utf-8') as f:
    f.write(f"// Gerado automaticamente\nconst RAIOX_CLIENTE_DETALHE_DATA = {json.dumps(payload, ensure_ascii=False, indent=2)};\n")

print(f"OK raiox_cliente_detalhe_data.js — {len(registros)} clientes")

repo_dir = str(Path(__file__).parent)
subprocess.run(["git", "-C", repo_dir, "add", "raiox_cliente_detalhe_data.js", "raiox_cliente_detalhe.html"], check=False)
subprocess.run(["git", "-C", repo_dir, "commit", "-m",
                f"Atualiza raiox_cliente_detalhe_data.js - {date.today().strftime('%d/%m/%Y')}"])
subprocess.run(["git", "-C", repo_dir, "push", "origin", "master"], check=False)
print("OK GitHub Pages atualizado.")
