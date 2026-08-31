import os
import pandas as pd
import oracledb
from sqlalchemy import create_engine
from datetime import date
from dotenv import load_dotenv

load_dotenv()
oracledb.init_oracle_client(lib_dir=os.getenv("ORACLE_LIB", r"C:\instantclient"))
_user = os.environ["VPN_USER"]
_pass = os.environ["VPN_PASSWORD"]
engine        = create_engine(f'oracle+oracledb://{_user}:{_pass}@crc_oci')
engine_theking = create_engine(f'oracle+oracledb://{_user}:{_pass}@theking_oci')

def _query(schema):
    s = schema.upper()
    return f"""
        SELECT M.CODCLI, C.CLIENTE, M.DESCRICAO AS PRODUTO, F.FANTASIA,
               U.NOME AS NOME_ORACLE, M.QT, (M.PUNIT * M.QT) AS VALOR
        FROM {s}.PCMOV M
        JOIN {s}.PCUSUARI U ON M.CODUSUR = U.CODUSUR
        LEFT JOIN {s}.PCCLIENT C ON M.CODCLI = C.CODCLI
        JOIN {s}.PCPRODUT P ON M.CODPROD = P.CODPROD
        JOIN {s}.PCFORNEC F ON P.CODFORNEC = F.CODFORNEC
        WHERE TRUNC(M.DTMOV, 'MM') = TRUNC(SYSDATE, 'MM')
          AND M.CODOPER = 'S'
          AND M.CODFILIAL IN (1, 2, 4)
          AND M.NUMNOTADEV IS NULL
          AND M.DTCANCEL IS NULL
          AND U.NOME LIKE '%OFF TRADE%'
          AND UPPER(F.FANTASIA) LIKE '%PERNOD%'
    """

df = pd.concat([
    pd.read_sql(_query("CRC"),      con=engine,         dtype=str),
    pd.read_sql(_query("thekings"), con=engine_theking, dtype=str),
], ignore_index=True)

df.columns  = df.columns.str.upper()
df['PRODUTO']  = df['PRODUTO'].fillna('')
df['FANTASIA'] = df['FANTASIA'].fillna('')
df['CLIENTE']  = df['CLIENTE'].fillna(df['CODCLI'])

# Filtrar pelo Leandro (nome Oracle contém "LEANDRO")
leandro = df[df['NOME_ORACLE'].str.contains('LEANDRO', case=False, na=False)].copy()

if leandro.empty:
    print("Nenhuma venda Pernod encontrada para Leandro neste mês.")
else:
    pares_unicos  = leandro.drop_duplicates(subset=['CODCLI', 'PRODUTO'])
    mask_jamerson = pares_unicos['PRODUTO'].str.contains('JAMESON', case=False, na=False)
    jamerson_pares = pares_unicos[mask_jamerson]
    outros_pares   = pares_unicos[~mask_jamerson]

    print(f"\n{'='*60}")
    print(f"  CAMPANHA PERNOD — LEANDRO")
    print(f"{'='*60}")

    print(f"\nJAMERSON (x10) — {len(jamerson_pares)} par(es) cliente+produto")
    print(f"   Subtotal: R$ {len(jamerson_pares) * 10:.2f}")
    for _, r in jamerson_pares.iterrows():
        print(f"   • {r['CLIENTE'][:35]:<35} | {r['PRODUTO'][:35]}")

    print(f"\nPERNOD outros (x5) — {len(outros_pares)} par(es) cliente+produto")
    print(f"   Subtotal: R$ {len(outros_pares) * 5:.2f}")
    for _, r in outros_pares.iterrows():
        print(f"   • {r['CLIENTE'][:35]:<35} | {r['PRODUTO'][:35]}")

    total = len(jamerson_pares) * 10 + len(outros_pares) * 5
    print(f"\n{'='*60}")
    print(f"  BONUS TOTAL: R$ {total:.2f}")
    print(f"{'='*60}\n")
