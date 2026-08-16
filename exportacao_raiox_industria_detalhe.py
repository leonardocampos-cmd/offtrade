"""
Gera raiox_industria_detalhe_data.js — ficha individual de cada indústria/
fornecedor OFF TRADE em todas as bases (RJ e ES via CRC, SP via SPON, MG via
MGON): faturamento mensal, ranking de melhores clientes e ranking de
vendedores que mais vendem essa indústria (agregado entre estados por nome
de fornecedor; cliente e vendedor identificados com o estado, já que CODCLI
e CODUSUR não são únicos entre bases). Alimenta raiox_industria_detalhe.html
(acessível a partir dos botões "Ver detalhe" em raiox_industrias.html).
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
        SELECT COALESCE(F.FANTASIA,'SEM FANTASIA') AS FORNECEDOR,
               M.CODCLI, COALESCE(C.FANTASIA, C.CLIENTE) NOME_CLIENTE,
               M.CODUSUR, U.NOME NOME_VENDEDOR,
               COALESCE(M.DESCRICAO, 'Produto ' || M.CODPROD) AS PRODUTO,
               TRUNC(M.DTMOV,'MM') AS MES, SUM(M.PUNIT*M.QT) AS FATURAMENTO, SUM(M.QT) AS QTD
        FROM {schema}.PCMOV M
        JOIN {schema}.PCCLIENT C ON M.CODCLI = C.CODCLI
        JOIN {schema}.PCFORNEC F ON M.CODFORNEC = F.CODFORNEC
        JOIN {schema}.PCUSUARI U ON M.CODUSUR = U.CODUSUR
        WHERE U.NOME LIKE '%OFF TRADE%'
          {fil_clause}
          AND M.CODOPER = 'S'
          AND M.NUMNOTADEV IS NULL
          AND M.DTCANCEL IS NULL
          AND TRUNC(M.DTMOV) >= TO_DATE('{MES_INI}','YYYY-MM-DD')
          AND TRUNC(M.DTMOV) <= TO_DATE('{MES_FIM}','YYYY-MM-DD')
        GROUP BY COALESCE(F.FANTASIA,'SEM FANTASIA'), M.CODCLI, COALESCE(C.FANTASIA, C.CLIENTE),
                 M.CODUSUR, U.NOME, COALESCE(M.DESCRICAO, 'Produto ' || M.CODPROD), TRUNC(M.DTMOV,'MM')
    """


_partes = []
fontes_indisponiveis = []

for base in BASES:
    estado, eng, schema, filiais = base["estado"], base["engine"], base["schema"], base["filiais"]
    try:
        d = carregar_dados(_query(schema, filiais), eng, f"raiox_induddet_vendas_{estado}")
        d.columns = d.columns.str.upper()
        d['ESTADO'] = estado
        _partes.append(d)
        print(f"  OK {estado}: {len(d)} linhas")
    except Exception as e:
        print(f"  [AVISO] {estado} falhou ({str(e)[:150]}) — ignorado")
        fontes_indisponiveis.append(estado)

vendas = pd.concat(_partes, ignore_index=True) if _partes else pd.DataFrame(
    columns=['FORNECEDOR', 'CODCLI', 'NOME_CLIENTE', 'CODUSUR', 'NOME_VENDEDOR', 'PRODUTO', 'MES', 'FATURAMENTO', 'QTD', 'ESTADO'])
vendas['MES'] = pd.to_datetime(vendas['MES'])
vendas['FORNECEDOR'] = vendas['FORNECEDOR'].fillna('SEM FANTASIA').str.strip()
vendas['NOME_CLIENTE'] = vendas['NOME_CLIENTE'].fillna('').str.strip()
vendas['PRODUTO'] = vendas['PRODUTO'].fillna('').str.strip()
vendas['NOME_VENDEDOR'] = vendas['NOME_VENDEDOR'].fillna('').str.replace('- OFF TRADE', '', regex=False).str.replace('-OFF TRADE', '', regex=False).str.strip()
vendas['CLIENTE_KEY'] = vendas['ESTADO'] + '-' + vendas['CODCLI'].astype(str)
vendas['VENDEDOR_KEY'] = vendas['ESTADO'] + '-' + vendas['CODUSUR'].astype(str)

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
        grp.groupby(['CLIENTE_KEY', 'ESTADO', 'NOME_CLIENTE'])['FATURAMENTO'].sum()
        .sort_values(ascending=False)
    )
    top_clientes = [
        {'codcli': chave.split('-', 1)[1], 'estado': est, 'nome': nome_cli or f"Cliente {chave}", 'faturamento': round(float(v), 2)}
        for (chave, est, nome_cli), v in top_clientes_s.head(15).items()
    ]

    top_vendedores_s = (
        grp.groupby(['VENDEDOR_KEY', 'ESTADO', 'NOME_VENDEDOR'])['FATURAMENTO'].sum()
        .sort_values(ascending=False)
    )
    top_vendedores = [
        {'rca': chave.split('-', 1)[1], 'estado': est, 'nome': nome_v or f"RCA {chave}", 'faturamento': round(float(v), 2)}
        for (chave, est, nome_v), v in top_vendedores_s.head(15).items()
    ]

    top_produtos_s = (
        grp.groupby('PRODUTO')[['FATURAMENTO', 'QTD']].sum()
        .sort_values('FATURAMENTO', ascending=False)
    )
    top_produtos = [
        {'produto': produto or 'N/D', 'faturamento': round(float(row['FATURAMENTO']), 2), 'quantidade': round(float(row['QTD']), 2)}
        for produto, row in top_produtos_s.head(15).iterrows()
    ]

    fornecedores.append({
        'fantasia': fantasia,
        'faturamento_ytd': round(fat_ytd, 2),
        'participacao_pct': round(fat_ytd / faturamento_total * 100, 2) if faturamento_total else 0.0,
        'media_mensal': round(media_mensal, 2),
        'clientes_positivados': int(grp['CLIENTE_KEY'].nunique()),
        'por_mes': por_mes,
        'top_clientes': top_clientes,
        'top_vendedores': top_vendedores,
        'top_produtos': top_produtos,
    })

fornecedores.sort(key=lambda f: f['faturamento_ytd'], reverse=True)
for i, f in enumerate(fornecedores, start=1):
    f['posicao'] = i

payload = {
    'atualizado_em': datetime.now().strftime('%d/%m/%Y %H:%M'),
    'meses_com_dado': meses_com_dado,
    'faturamento_total_ytd': round(faturamento_total, 2),
    'fornecedores': fornecedores,
    'fontes_indisponiveis': fontes_indisponiveis,
}

out_path = Path(__file__).parent / "raiox_industria_detalhe_data.js"
with open(out_path, 'w', encoding='utf-8') as f:
    f.write(f"// Gerado automaticamente\nconst RAIOX_INDUSTRIA_DETALHE_DATA = {json.dumps(payload, ensure_ascii=False, indent=2)};\n")

print(f"OK raiox_industria_detalhe_data.js — {len(fornecedores)} fornecedores")
if fontes_indisponiveis:
    print(f"[AVISO] Fontes indisponíveis: {fontes_indisponiveis}")

repo_dir = str(Path(__file__).parent)
subprocess.run(["git", "-C", repo_dir, "add", "raiox_industria_detalhe_data.js", "raiox_industria_detalhe.html"], check=False)
subprocess.run(["git", "-C", repo_dir, "commit", "-m",
                f"Atualiza raiox_industria_detalhe_data.js - {date.today().strftime('%d/%m/%Y')}"])
subprocess.run(["git", "-C", repo_dir, "push", "origin", "master"], check=False)
print("OK GitHub Pages atualizado.")
