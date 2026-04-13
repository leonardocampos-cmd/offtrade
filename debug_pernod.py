import pandas as pd
import oracledb
from sqlalchemy import create_engine
from datetime import date

oracledb.init_oracle_client(lib_dir=r"C:\instantclient")
engine        = create_engine('oracle+oracledb://vpn:vpn2320vpn@crc_oci')
engine_theking = create_engine('oracle+oracledb://vpn:vpn2320vpn@theking_oci')

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
          AND (UPPER(M.DESCRICAO) LIKE 'WHISKY JAMESON%'
               OR UPPER(F.FANTASIA) LIKE '%PERNOD%')
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
    mask_jameson      = leandro['PRODUTO'].str.upper().str.startswith('WHISKY JAMESON')
    mask_pernod_other = leandro['FANTASIA'].str.contains('PERNOD', case=False, na=False) & ~mask_jameson

    jameson_pares      = leandro[mask_jameson].drop_duplicates(subset=['CODCLI', 'PRODUTO'])
    pernod_other_pares = leandro[mask_pernod_other].drop_duplicates(subset=['CODCLI', 'PRODUTO'])

    print(f"\n{'='*60}")
    print(f"  CAMPANHA PERNOD — LEANDRO")
    print(f"{'='*60}")

    print(f"\n🥃 WHISKY JAMESON (R$10 cada) — {len(jameson_pares)} pares únicos")
    print(f"   Subtotal: R$ {len(jameson_pares) * 10:.2f}")
    if not jameson_pares.empty:
        for _, r in jameson_pares.iterrows():
            print(f"   • {r['CLIENTE'][:40]:<40} | {r['PRODUTO'][:40]}")

    print(f"\n🍷 PERNOD outros (R$5 cada) — {len(pernod_other_pares)} pares únicos")
    print(f"   Subtotal: R$ {len(pernod_other_pares) * 5:.2f}")
    if not pernod_other_pares.empty:
        for _, r in pernod_other_pares.iterrows():
            print(f"   • {r['CLIENTE'][:40]:<40} | {r['PRODUTO'][:40]}")

    total = len(jameson_pares) * 10 + len(pernod_other_pares) * 5
    print(f"\n{'='*60}")
    print(f"  BÔNUS TOTAL: R$ {total:.2f}")
    print(f"{'='*60}\n")
