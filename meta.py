#META
import os
import pandas as pd
import oracledb
from sqlalchemy import create_engine
from datetime import datetime
import numpy as np
import time
from dotenv import load_dotenv
from urllib.parse import quote_plus

load_dotenv()

from utils import ORACLE_LIB
oracledb.init_oracle_client(lib_dir=ORACLE_LIB)

user     = os.getenv("VPN_USER", "vpn")
password = os.getenv("VPN_PASSWORD", "vpn2320vpn")
crc_user = os.getenv("CRC_USER", user)
crc_pass = os.getenv("CRC_PASSWORD", password)
dsn = "crc_oci"
dsn_theking = "theking_oci"

engine = create_engine(
    f'oracle+oracledb://{crc_user}:{quote_plus(crc_pass)}@{dsn}',
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
engine_castas = create_engine(
    f'oracle+oracledb://{user}:{password}@10.131.62.40:1576/?service_name=CASTASPRD',
    pool_pre_ping=True,
    pool_recycle=3600,
    connect_args={"expire_time": 2}
)
engine_garrido = create_engine(
    f'oracle+oracledb://{user}:{password}@10.107.213.84:1521/?service_name=orcl_pdb1.subnetwintcompa.vcnrootautoskyo.oraclevcn.com',
    pool_pre_ping=True,
    pool_recycle=3600,
    connect_args={"expire_time": 2}
)
engine_spon = create_engine(
    f'oracle+oracledb://{user}:{password}@spon_oci',
    pool_pre_ping=True,
    pool_recycle=3600,
    connect_args={"expire_time": 2}
)
engine_mgon = create_engine(
    f'oracle+oracledb://{user}:{password}@{os.getenv("DSN_MG", "mgon_oci")}',
    pool_pre_ping=True,
    pool_recycle=3600,
    connect_args={"expire_time": 2}
)

# Nomes das fontes (bancos Oracle) que falharam nesta execução — usado para
# avisar o usuário em metas.html que os resultados podem estar incompletos.
FONTES_INDISPONIVEIS: list[str] = []

def carregar_dados(query, engine, nome_tabela="tabela", max_tentativas=3):
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

def _query_vendas(schema=None, mes_anterior=False, filtro_filial="(1,2,4)", filtro_estent=None):
    p = f"{schema}." if schema else ""
    filtro_mes = "ADD_MONTHS(TRUNC(SYSDATE, 'MM'), -1)" if mes_anterior else "TRUNC(SYSDATE, 'MM')"
    extra_filial = f"\n    AND PCMOV.CODFILIAL IN {filtro_filial}" if filtro_filial else ""
    extra_estent = f"\n    AND PCUSUARI.ESTADO = '{filtro_estent}'" if filtro_estent else ""
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
    AND PCMOV.CODOPER IN ('S','SB')
    AND PCMOV.NUMNOTADEV IS NULL
    AND PCMOV.DTCANCEL IS NULL
    AND PCUSUARI.ESTADO = 'RJ'
    AND PCUSUARI.NOME LIKE '%OFF TRADE%'{extra_filial}{extra_estent}
"""

_parts_vendas = []
for _s, _e, _n, _ff, _fe in [
    ("CRC",      engine,         "vendas_CRC",      "(1,2,4)", "RJ"),
    ("thekings", engine_theking, "vendas_thekings",  "(1,2,4)", "RJ"),
    ("CASTAS",   engine_castas,  "vendas_CASTAS",    None,      "RJ"),
    ("GARRIDO",  engine_garrido, "vendas_GARRIDO",   None,      "RJ"),
    ("SPON",     engine_spon,    "vendas_SPON",      None,      "RJ"),
    ("MGON",     engine_mgon,    "vendas_MGON",      None,      "RJ"),
]:
    try:
        _parts_vendas.append(carregar_dados(_query_vendas(_s, filtro_filial=_ff, filtro_estent=_fe), _e, _n))
    except Exception as _ex:
        print(f"[AVISO] {_n} falhou ({str(_ex)[:80]}) — ignorado")
        FONTES_INDISPONIVEIS.append(_n)
if not _parts_vendas:
    raise RuntimeError("Nenhuma fonte de vendas disponível — todas as bases Oracle estão fora do ar.")
tabela_vendas = pd.concat(_parts_vendas, ignore_index=True)
import baixar_planilhas_drive as _bpd
try:
    arquivo = pd.read_excel(_bpd.com_fallback(
        _bpd.caminho_metas_rj,
        r"G:\Drives compartilhados\Off Trade\Campanhas e Metas\METAS\METAS RJ.xlsx"
    ))
    arquivo.columns = arquivo.columns.str.strip()
    arquivo = arquivo.rename(columns={
        'META FATURAMENTO':              'FATURAMENTO TT',
        'META FATURAMENTO CASTAS':       'FAT CASTAS',
        'META FATURAMENTO AZEITE':       'FATURAMENTO AZEITE (legado)',
        'META POSITIVAÇÃO':              'POSITIVAÇÃO TT',
        'META POSITIVAÇÃO HOB + AZEITE': 'POSITIVAÇÃO HOB + AZEITE',
        'META POSITIVAÇÃO RECKIT':       'POSITIVAÇÃO RECKIT',
        'META POSITIVAÇÃO TIAL':         'POSITIVAÇÃO TIAL',
        'META POSITIVAÇÃO TATUZINHO':    'POSITIVAÇÃO TATUZINHO',
        'META POSITIVAÇÃO RED BULL':     'POSITIVAÇÃO RED BULL',
        'META POSITIVAÇÃO PINATTI':      'POSITIVAÇÃO PINATTI',
        'META POSITIVAÇÃO ESSENZA+HOB': 'POSITIVAÇÃO ESSENZA+HOB',
        'META FATURAMENTO HOB + AZEITE': 'FATURAMENTO HOB + AZEITE',
        'META FATURAMENTO PERNOD': 'FATURAMENTO PERNOD',
    })
except Exception as _ex:
    print(f"[AVISO] METAS RJ.xlsx (Drive) falhou ({str(_ex)[:80]}) — ignorado (arquivo não utilizado no pipeline)")

tabela_vendas['FATURAMENTO'] = pd.to_numeric(tabela_vendas['FATURAMENTO'], errors='coerce')
tabela_vendas.drop(columns=['CODPROD', 'CODFORNEC', 'NUMNOTA', 'CODOPER', 'PUNIT',
       'CODFILIAL', 'CODUSUR', 'NUMNOTADEV', 'DTCANCEL', 'FORNECEDOR'],inplace=True)
tabela_vendas['QT'] = pd.to_numeric(tabela_vendas['QT'], errors='coerce').fillna(0)
tabela_vendas['DTMOV'] = pd.to_datetime(tabela_vendas['DTMOV']).dt.strftime('%d/%m/%Y')
tabela_vendas['FATURAMENTO'] = tabela_vendas['FATURAMENTO'].round(2)

# Vendas do mês anterior (para exibição no detalhe do vendedor)
_parts_vendas_ant = []
for _s, _e, _n, _ff, _fe in [
    ("CRC",      engine,         "vendas_anterior_CRC",      "(1,2,4)", "RJ"),
    ("thekings", engine_theking, "vendas_anterior_thekings", "(1,2,4)", "RJ"),
    ("CASTAS",   engine_castas,  "vendas_anterior_CASTAS",   None,      "RJ"),
    ("GARRIDO",  engine_garrido, "vendas_anterior_GARRIDO",  None,      "RJ"),
    ("SPON",     engine_spon,    "vendas_anterior_SPON",     None,      "RJ"),
]:
    try:
        _parts_vendas_ant.append(carregar_dados(_query_vendas(_s, mes_anterior=True, filtro_filial=_ff, filtro_estent=_fe), _e, _n))
    except Exception as _ex:
        print(f"[AVISO] {_n} falhou ({str(_ex)[:80]}) — ignorado")
        FONTES_INDISPONIVEIS.append(_n)
if not _parts_vendas_ant:
    raise RuntimeError("Nenhuma fonte de vendas do mês anterior disponível — todas as bases Oracle estão fora do ar.")
tabela_vendas_anterior = pd.concat(_parts_vendas_ant, ignore_index=True)
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
