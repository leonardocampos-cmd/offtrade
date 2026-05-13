#META
import pandas as pd
import oracledb
from sqlalchemy import create_engine
from datetime import datetime
import numpy as np
import time

oracledb.init_oracle_client(lib_dir=r"C:\instantclient")

user = "vpn"
password = "vpn2320vpn"
dsn = "crc_oci"
dsn_theking = "theking_oci"

engine = create_engine(
    f'oracle+oracledb://{user}:{password}@{dsn}',
    pool_pre_ping=True,
    pool_recycle=3600,
    connect_args={"expire_time": 2}
)
engine_theking = create_engine(
    f'oracle+oracledb://{user}:{password}@{dsn_theking}',
    pool_pre_ping=True,
    pool_recycle=3600,
    connect_args={"expire_time": 2}
)

def carregar_dados(query, engine, nome_tabela="tabela", max_tentativas=5):
    for tentativa in range(1, max_tentativas + 1):
        try:
            print(f"-> Lendo {nome_tabela} (Tentativa {tentativa}/{max_tentativas})...")
            with engine.connect() as conn:
                chunks = []
                for chunk in pd.read_sql(query, con=conn, chunksize=5000):
                    chunks.append(chunk)
                df = pd.concat(chunks, ignore_index=True)
                df.columns = df.columns.str.strip().str.upper()
                print(f"OK {nome_tabela} carregada!")
                return df
        except Exception as e:
            print(f"Erro na {nome_tabela}: {str(e)[:100]}")
            engine.dispose()
            if tentativa < max_tentativas:
                time.sleep(10)
            else:
                raise e

def _query_vendas(schema=None, mes_anterior=False):
    p = f"{schema}." if schema else ""
    filtro_mes = "ADD_MONTHS(TRUNC(SYSDATE, 'MM'), -1)" if mes_anterior else "TRUNC(SYSDATE, 'MM')"
    return f"""
    SELECT PCMOV.DTMOV      AS DTMOV,
           PCMOV.CODPROD    AS CODPROD,
           PCMOV.CODFORNEC    AS CODFORNEC,
           PCMOV.NUMNOTA    AS NUMNOTA,
           PCMOV.CODOPER    AS CODOPER,
           PCMOV.QT         AS QT,
           PCMOV.PUNIT      AS PUNIT,
           PCMOV.CODFILIAL  AS CODFILIAL,
           PCMOV.CODCLI     AS CODCLI,
           PCCLIENT.CLIENTE AS CLIENTE,
           PCMOV.CODUSUR    AS CODUSUR,
           PCMOV.NUMNOTADEV AS NUMNOTADEV,
           PCMOV.DTCANCEL   AS DTCANCEL,
           PCMOV.DESCRICAO  AS DESCRICAO,
           PCUSUARI.NOME    AS VENDEDOR,
           PCFORNEC.FORNECEDOR AS FORNECEDOR,
           PCFORNEC.FANTASIA AS FANTASIA,
           (PCMOV.PUNIT * PCMOV.QT) AS FATURAMENTO

    FROM {p}PCMOV
    JOIN {p}PCUSUARI ON PCMOV.CODUSUR = PCUSUARI.CODUSUR
    JOIN {p}PCPRODUT ON PCMOV.CODPROD = PCPRODUT.CODPROD
    JOIN {p}PCFORNEC ON PCMOV.CODFORNEC = PCFORNEC.CODFORNEC
    LEFT JOIN {p}PCCLIENT ON PCMOV.CODCLI = PCCLIENT.CODCLI
    WHERE TRUNC(PCMOV.DTMOV, 'MM') = {filtro_mes}
    AND PCMOV.CODOPER = 'S'
    AND PCMOV.CODFILIAL IN (1,2,4)
    AND PCMOV.NUMNOTADEV IS NULL
    AND PCMOV.DTCANCEL IS NULL
    AND PCUSUARI.NOME LIKE '%OFF TRADE%'
"""

tabela_vendas = pd.concat([
    carregar_dados(_query_vendas("CRC"),      engine,         "vendas_CRC"),
    carregar_dados(_query_vendas('thekings'), engine_theking, "vendas_thekings"),
], ignore_index=True)
arquivo = pd.concat([
    pd.read_excel(r"G:\Drives compartilhados\Off Trade\Campanhas e Metas\METAS_rj - MARÇO 2026.xlsx"),
    pd.read_excel(r"G:\Drives compartilhados\Off Trade\Campanhas e Metas\METAS_rj - Abril 2026.xlsx"),
    pd.read_excel(r"G:\Drives compartilhados\Off Trade\Campanhas e Metas\METAS_rj - MAIO 2026.xlsx"),
], ignore_index=True)
tabela_vendas['FATURAMENTO'] = pd.to_numeric(tabela_vendas['FATURAMENTO'], errors='coerce')
tabela_vendas.drop(columns=['CODPROD', 'CODFORNEC', 'NUMNOTA', 'CODOPER', 'PUNIT',
       'CODFILIAL', 'CODUSUR', 'NUMNOTADEV', 'DTCANCEL', 'FORNECEDOR'],inplace=True)
tabela_vendas['QT'] = pd.to_numeric(tabela_vendas['QT'], errors='coerce').fillna(0)
tabela_vendas['DTMOV'] = pd.to_datetime(tabela_vendas['DTMOV']).dt.strftime('%d/%m/%Y')
tabela_vendas['FATURAMENTO'] = tabela_vendas['FATURAMENTO'].round(2)

# Vendas do mês anterior (para exibição no detalhe do vendedor)
tabela_vendas_anterior = pd.concat([
    carregar_dados(_query_vendas("CRC",      mes_anterior=True), engine,         "vendas_anterior_CRC"),
    carregar_dados(_query_vendas('thekings', mes_anterior=True), engine_theking, "vendas_anterior_thekings"),
], ignore_index=True)
tabela_vendas_anterior['FATURAMENTO'] = pd.to_numeric(tabela_vendas_anterior['FATURAMENTO'], errors='coerce')
tabela_vendas_anterior.drop(columns=['CODPROD', 'CODFORNEC', 'NUMNOTA', 'CODOPER', 'PUNIT',
       'CODFILIAL', 'CODUSUR', 'NUMNOTADEV', 'DTCANCEL', 'FORNECEDOR'],inplace=True)
tabela_vendas_anterior['QT'] = pd.to_numeric(tabela_vendas_anterior['QT'], errors='coerce').fillna(0)
tabela_vendas_anterior['DTMOV'] = pd.to_datetime(tabela_vendas_anterior['DTMOV']).dt.strftime('%d/%m/%Y')
tabela_vendas_anterior['FATURAMENTO'] = tabela_vendas_anterior['FATURAMENTO'].round(2)

#FATURAMENTO TOTAL
faturamento_total = float(tabela_vendas['FATURAMENTO'].sum())

#FATURAMENTO CASTAS
faturamento_castas = float(tabela_vendas[tabela_vendas['FANTASIA'].str.contains('castas', case=False, na=False)]['FATURAMENTO'].sum().round(2))

#FATURAMENTO DOMECQ / PASSPORT
fat_domecq_passport = float(tabela_vendas[tabela_vendas['DESCRICAO'].str.contains('DOMECQ|PASSPORT', case=False, na=False)]['FATURAMENTO'].sum().round(2))

#FATURAMENTO AZEITE HBO
fat_azeite_hbo = float(tabela_vendas[
    tabela_vendas['DESCRICAO'].str.contains('AZEITE', case=False, na=False) |
    tabela_vendas['FANTASIA'].str.contains('HOB', case=False, na=False)
]['FATURAMENTO'].sum().round(2))
#FATURAMENTO AZEITE     
fat_azeite_zetona = float(tabela_vendas[
    tabela_vendas['DESCRICAO'].str.contains('AZEITE', case=False, na=False)]['FATURAMENTO'].sum().round(2))
positivacao_azeite_hob = int(tabela_vendas[
    tabela_vendas['DESCRICAO'].str.contains('AZEITE', case=False, na=False) |
    tabela_vendas['FANTASIA'].str.contains('HOB', case=False, na=False)
]['CODCLI'].nunique())

positivacao_tt = int(tabela_vendas['CODCLI'].nunique())

positivacao_reckit = int(tabela_vendas[
    tabela_vendas['FANTASIA'].str.contains('RECKIT', case=False, na= False)
]['CODCLI'].nunique())

positivacao_crusoe = int(tabela_vendas[tabela_vendas['FANTASIA'].str.contains('ROBINSON CRUSOE', case=False, na=False)]['CODCLI'].nunique())
positivacao_tatuzinho = int(tabela_vendas[tabela_vendas['FANTASIA'].str.contains('TATUZINHO', case=False, na=False)]['CODCLI'].nunique())
positivacao_redbull = int(tabela_vendas[tabela_vendas['FANTASIA'].str.contains('RED BULL', case=False, na=False)]['CODCLI'].nunique())
positivacao_pinatti = int(tabela_vendas[tabela_vendas['FANTASIA'].str.contains('PINATI', case=False, na=False)]['CODCLI'].nunique())
