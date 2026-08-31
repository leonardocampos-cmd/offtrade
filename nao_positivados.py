# NAO POSITIVADOS - Clientes que não compraram no mês atual
import time
import pandas as pd
from datetime import datetime
from pathlib import Path
from meta import (
    engine, engine_theking, engine_castas, engine_garrido, engine_spon, engine_blended,
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
    # C vem de PCCLIENT (não PBI_PCCLIENT) por causa de CODUSUR3: a view
    # PBI_PCCLIENT só expõe RCA/RCA2 com código (CODUSUR) — RCA3 só existe lá
    # como nome solto (RCA3_NOME), sem código pra casar com PCUSUARI. PCCLIENT
    # (tabela base) tem CODUSUR1/2/3 com código de verdade, confirmado batendo
    # com RCA3_NOME via join por CODUSUR3 (pedido do usuário em 2026-08-31:
    # "não positivados" tem que considerar cliente cadastrado como RCA 1, 2 ou 3
    # do vendedor, não só o primário).
    return f"""
        SELECT
            C.CODCLI,
            C.CLIENTE,
            C.BAIRROENT,
            C.CODUSUR1 AS RCA,
            C.CODUSUR2 AS RCA2,
            C.CODUSUR3 AS RCA3,
            LV.DTULTCOMP,
            M.CODPROD,
            M.CODUSUR,
            (M.PUNIT * M.QT) AS FATURAMENTO,
            P.DESCRICAO,
            P.CODFORNEC,
            F.FORNECEDOR,
            F.FANTASIA,
            U1.NOME AS NOME_RCA,
            U2.NOME AS NOME_RCA2,
            U3.NOME AS NOME_RCA3
        FROM {s}.PCCLIENT C
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
        LEFT JOIN {s}.PCUSUARI U1 ON C.CODUSUR1 = U1.CODUSUR
        LEFT JOIN {s}.PCUSUARI U2 ON C.CODUSUR2 = U2.CODUSUR
        LEFT JOIN {s}.PCUSUARI U3 ON C.CODUSUR3 = U3.CODUSUR
        WHERE (U1.NOME LIKE '%OFF TRADE%' OR U2.NOME LIKE '%OFF TRADE%' OR U3.NOME LIKE '%OFF TRADE%'){extra_estent}
        ORDER BY LV.DTULTCOMP DESC
"""

_parts_cli = []
for _s, _e, _fe in [
    ("CRC",      engine,         None),
    ("thekings", engine_theking, None),
    ("CASTAS",   engine_castas,  None),
    ("GARRIDO",  engine_garrido, None),
    ("SPON",     engine_spon,    None),
    # BLENDED (SP, operação nova — ver meta.py::engine_blended) faltava aqui
    # (achado pelo usuário em 2026-08-31): cliente cadastrado só em BLENDED
    # nunca entrava na carteira, e — pior — comprar SÓ em BLENDED nunca era
    # visto por "positivados" abaixo (tabela_vendas do meta.py exige
    # PCUSUARI.ESTADO='RJ' incondicionalmente, e BLENDED nunca tem ESTADO
    # preenchido — mesma ausência documentada em exportacao_meta.py), gerando
    # falso "não positivado" pra quem já tinha comprado via BLENDED.
    ("BLENDED",  engine_blended, None),
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
# .astype(str): tabela_vendas.CODCLI vem como int (lido sem dtype=str no
# meta.py), mas clientes.CODCLI aqui vem como str (_read_sql_retry usa
# dtype=str por padrão) — sem esse cast, "95840" (str) nunca batia com
# 95840 (int) no .isin() abaixo, silenciosamente deixando de filtrar quem
# já tinha comprado (achado pelo usuário em 2026-08-31, investigando o
# porquê de clientes que já compraram ainda aparecerem como "não positivado").
positivados = set(tabela_vendas['CODCLI'].dropna().astype(str).unique())

# BLENDED não entra em tabela_vendas (meta.py exige PCUSUARI.ESTADO='RJ'
# incondicionalmente, e BLENDED nunca tem ESTADO preenchido) — sem isso,
# cliente que só compra via BLENDED nunca positivava aqui mesmo já tendo
# comprado (achado pelo usuário em 2026-08-31, RCA Leandro Souza: 14 de 26
# "não positivados" já tinham comprado via BLENDED). Query própria, só
# CODCLI — não precisa atribuir a vendedor, isso já é resolvido à parte por
# exportacao_meta.py::_vh.
try:
    _df_blended_vendas = _read_sql_retry(
        """
        SELECT DISTINCT CODCLI
        FROM BLENDED.PCMOV
        WHERE TRUNC(DTMOV, 'MM') = TRUNC(SYSDATE, 'MM')
          AND CODOPER IN ('S', 'SB')
          AND NUMNOTADEV IS NULL
          AND DTCANCEL IS NULL
        """,
        engine_blended, engine_blended, "nao_pos_positivados_BLENDED",
    )
    _df_blended_vendas.columns = _df_blended_vendas.columns.str.upper()
    positivados |= set(_df_blended_vendas['CODCLI'].dropna().unique())
except Exception as _ex:
    print(f"[AVISO] nao_pos_positivados_BLENDED falhou ({str(_ex)[:80]}) — ignorado (positivação via BLENDED fica de fora)")
    FONTES_INDISPONIVEIS.append("nao_pos_positivados_BLENDED")

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

nao_positivados[['CODCLI', 'CLIENTE','BAIRROENT','DESCRICAO','NOME_RCA', 'NOME_RCA2', 'NOME_RCA3', 'DTULTCOMP']].to_excel(
    output_path, index=False
)

print(f"Clientes nao positivados em {datetime.now().strftime('%m/%Y')}: {len(nao_positivados)}")
print(f"Arquivo salvo em: {output_path}")
