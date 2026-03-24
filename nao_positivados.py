# NAO POSITIVADOS - Clientes que não compraram no mês atual
import pandas as pd
from datetime import datetime
from pathlib import Path
from meta import engine, engine_theking, tabela_vendas

def _query_clientes(schema):
    s = schema.upper()
    return f"""
        SELECT
            C.CODCLI,
            C.CLIENTE,
            C.RCA,
            C.RCA2,
            C.DTULTCOMP,
            C.NOTA_ULT_VENDA,
            M.CODPROD,
            M.CODUSUR,
            (M.PUNIT * M.QT) AS FATURAMENTO,
            P.DESCRICAO,
            P.CODFORNEC,
            F.FORNECEDOR,
            F.FANTASIA,
            U1.NOME AS NOME_RCA,
            U2.NOME AS NOME_RCA2
        FROM {s}.PBI_PCCLIENT C
        LEFT JOIN {s}.PCMOV M ON (C.CODCLI = M.CODCLI AND C.NOTA_ULT_VENDA = M.NUMNOTA)
        LEFT JOIN {s}.PCPRODUT P ON (M.CODPROD = P.CODPROD)
        LEFT JOIN {s}.PCFORNEC F ON (P.CODFORNEC = F.CODFORNEC)
        LEFT JOIN {s}.PCUSUARI U1 ON (C.RCA = U1.CODUSUR)
        LEFT JOIN {s}.PCUSUARI U2 ON (C.RCA2 = U2.CODUSUR)
        WHERE C.DTULTCOMP IS NOT NULL
        AND (U1.NOME LIKE '%OFF TRADE%' OR U2.NOME LIKE '%OFF TRADE%')
        ORDER BY C.DTULTCOMP DESC
"""

clientes = pd.concat([
    pd.read_sql(_query_clientes("CRC"),      con=engine,         dtype=str),
    pd.read_sql(_query_clientes("thekings"), con=engine_theking, dtype=str),
], ignore_index=True)

clientes.columns = clientes.columns.str.upper()

# Clientes que já compraram esse mês
positivados = set(tabela_vendas['CODCLI'].dropna().unique())

# Remove positivados
nao_positivados = clientes[~clientes['CODCLI'].isin(positivados)].copy()

# Formata data
nao_positivados['DTULTCOMP'] = pd.to_datetime(nao_positivados['DTULTCOMP'], errors='coerce').dt.strftime('%d/%m/%Y')

# Ordena por vendedor e cliente
nao_positivados = nao_positivados.sort_values(['NOME_RCA', 'CLIENTE']).reset_index(drop=True)

# Remove duplicatas (cliente pode estar nos dois schemas)
nao_positivados = nao_positivados.drop_duplicates(subset=['CODCLI'])

mes_atual = datetime.now().strftime('%m-%Y')
output_path = str(Path(__file__).parent / f"nao_positivados_{mes_atual}.xlsx")

nao_positivados[['CODCLI', 'CLIENTE','DESCRICAO','NOME_RCA', 'NOME_RCA2', 'DTULTCOMP']].to_excel(
    output_path, index=False
)

print(f"Clientes nao positivados em {datetime.now().strftime('%m/%Y')}: {len(nao_positivados)}")
print(f"Arquivo salvo em: {output_path}")
