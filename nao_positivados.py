# NAO POSITIVADOS - Clientes que não compraram no mês atual
import time
import pandas as pd
from datetime import datetime
from pathlib import Path
from meta import engine, engine_theking, tabela_vendas

def _read_sql_retry(query, con, engine_obj, nome, dtype=str, max_tentativas=5):
    for tentativa in range(1, max_tentativas + 1):
        try:
            print(f"-> Lendo {nome} (Tentativa {tentativa}/{max_tentativas})...")
            df = pd.read_sql(query, con=con, dtype=dtype)
            print(f"OK {nome} carregada!")
            return df
        except Exception as e:
            print(f"Erro na {nome}: {str(e)[:100]}")
            engine_obj.dispose()
            if tentativa < max_tentativas:
                time.sleep(10)
            else:
                raise e

def _query_clientes(schema):
    s = schema.upper()
    return f"""
        SELECT
            C.CODCLI,
            C.CLIENTE,
            C.BAIRROENT,
            C.RCA,
            C.RCA2,
            LV.DTULTCOMP,
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
        JOIN (
            SELECT
                CODCLI,
                MAX(DTMOV) AS DTULTCOMP,
                MAX(NUMNOTA) KEEP (DENSE_RANK LAST ORDER BY DTMOV) AS NUMNOTA,
                MAX(CODUSUR) KEEP (DENSE_RANK LAST ORDER BY DTMOV) AS CODUSUR
            FROM {s}.PCMOV
            WHERE CODOPER = 'S'
              AND NUMNOTADEV IS NULL
              AND DTCANCEL IS NULL
            GROUP BY CODCLI
        ) LV ON C.CODCLI = LV.CODCLI
        LEFT JOIN {s}.PCMOV M ON (M.CODCLI = LV.CODCLI AND M.NUMNOTA = LV.NUMNOTA
                                   AND M.CODOPER = 'S' AND M.NUMNOTADEV IS NULL AND M.DTCANCEL IS NULL)
        LEFT JOIN {s}.PCPRODUT P ON M.CODPROD = P.CODPROD
        LEFT JOIN {s}.PCFORNEC F ON P.CODFORNEC = F.CODFORNEC
        LEFT JOIN {s}.PCUSUARI U1 ON C.RCA = U1.CODUSUR
        LEFT JOIN {s}.PCUSUARI U2 ON C.RCA2 = U2.CODUSUR
        WHERE (U1.NOME LIKE '%OFF TRADE%' OR U2.NOME LIKE '%OFF TRADE%')
        ORDER BY LV.DTULTCOMP DESC
"""

clientes = pd.concat([
    _read_sql_retry(_query_clientes("CRC"),      engine,         engine,         "nao_pos_CRC"),
    _read_sql_retry(_query_clientes("thekings"), engine_theking, engine_theking, "nao_pos_thekings"),
], ignore_index=True)

clientes.columns = clientes.columns.str.upper()

# Clientes que já compraram esse mês
positivados = set(tabela_vendas['CODCLI'].dropna().unique())

# Remove positivados
nao_positivados = clientes[~clientes['CODCLI'].isin(positivados)].copy()

# Versão completa (todos os produtos, DTULTCOMP como datetime) para uso no dashboard
nao_positivados_full = nao_positivados.copy()
nao_positivados_full['DTULTCOMP'] = pd.to_datetime(nao_positivados_full['DTULTCOMP'], errors='coerce')

# Formata data
nao_positivados['DTULTCOMP'] = pd.to_datetime(nao_positivados['DTULTCOMP'], errors='coerce').dt.strftime('%d/%m/%Y')

# Ordena por vendedor e cliente
nao_positivados = nao_positivados.sort_values(['NOME_RCA', 'CLIENTE']).reset_index(drop=True)

# Remove duplicatas (cliente pode estar nos dois schemas)
nao_positivados = nao_positivados.drop_duplicates(subset=['CODCLI'])

mes_atual = datetime.now().strftime('%m-%Y')
output_path = str(Path(__file__).parent / f"nao_positivados_{mes_atual}.xlsx")

nao_positivados[['CODCLI', 'CLIENTE','BAIRROENT','DESCRICAO','NOME_RCA', 'NOME_RCA2', 'DTULTCOMP']].to_excel(
    output_path, index=False
)

print(f"Clientes nao positivados em {datetime.now().strftime('%m/%Y')}: {len(nao_positivados)}")
print(f"Arquivo salvo em: {output_path}")
