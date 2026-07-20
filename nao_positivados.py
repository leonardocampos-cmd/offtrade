# NAO POSITIVADOS - Clientes que não compraram no mês atual
import time
import pandas as pd
from datetime import datetime
from pathlib import Path
from meta import (
    engine, engine_theking, engine_castas, engine_garrido, engine_spon,
    tabela_vendas, FONTES_INDISPONIVEIS, _com_timeout_forcado,
)

def _read_sql_retry(query, con, engine_obj, nome, dtype=str, max_tentativas=3, timeout_por_tentativa=90):
    for tentativa in range(1, max_tentativas + 1):
        try:
            print(f"-> Lendo {nome} (Tentativa {tentativa}/{max_tentativas})...")
            df = _com_timeout_forcado(lambda: pd.read_sql(query, con=con, dtype=dtype), timeout_por_tentativa)
            print(f"OK {nome} carregada!")
            return df
        except Exception as e:
            print(f"Erro na {nome}: {str(e)[:100]}")
            engine_obj.dispose()
            if tentativa < max_tentativas:
                time.sleep(10)
            else:
                raise e

def _query_clientes(schema, filtro_estent=None):
    s = schema.upper()
    extra_estent = f"\n          AND C.ESTENT = '{filtro_estent}'" if filtro_estent else ""
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
        WHERE (U1.NOME LIKE '%OFF TRADE%' OR U2.NOME LIKE '%OFF TRADE%'){extra_estent}
        ORDER BY LV.DTULTCOMP DESC
"""

_parts_cli = []
for _s, _e, _fe in [
    ("CRC",      engine,         None),
    ("thekings", engine_theking, None),
    ("CASTAS",   engine_castas,  None),
    ("GARRIDO",  engine_garrido, None),
    ("SPON",     engine_spon,    None),
]:
    try:
        _parts_cli.append(_read_sql_retry(_query_clientes(_s, filtro_estent=_fe), _e, _e, f"nao_pos_{_s}"))
    except Exception as _ex:
        print(f"[AVISO] nao_pos_{_s} falhou ({str(_ex)[:80]}) — ignorado")
        FONTES_INDISPONIVEIS.append(f"nao_pos_{_s}")
if not _parts_cli:
    raise RuntimeError("Nenhuma fonte de clientes disponível — todas as bases Oracle estão fora do ar.")
clientes = pd.concat(_parts_cli, ignore_index=True)

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
