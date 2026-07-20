#CONFERENCIA PREÇO
import os
import pandas as pd
import oracledb
from sqlalchemy import create_engine
from datetime import datetime, date, timedelta
import numpy as np
import warnings

# Filtra avisos específicos do openpyxl
warnings.filterwarnings("ignore", category=UserWarning, module="openpyxl")

# 1. Configuração de Conexão
from utils import ORACLE_LIB
oracledb.init_oracle_client(lib_dir=ORACLE_LIB)
user = os.environ["VPN_USER"]
password = os.environ["VPN_PASSWORD"]
dsn = "crc_oci"
engine = create_engine(
    f'oracle+oracledb://{user}:{password}@{dsn}',
    pool_pre_ping=True,
    pool_recycle=3600,
    connect_args={"expire_time": 2}
)
from meta import carregar_dados
import baixar_planilhas_drive as _bpd

# 2. Carga das Tabelas de Referência
# --- TABELA ON ---
tabela_preco_on = pd.read_excel(
    _bpd.com_fallback(
        _bpd.caminho_tabela_preco_rj,
        r"G:\Drives compartilhados\EQUIPE DE VENDAS RJ\TABELA DE PREÇO RJ.xlsx"
    ),
    sheet_name='TABELA', skiprows=5, dtype=str
)
col_on_excluir = ['Unnamed: 0', 'COD BRASIL', 'PRODUTOS', 'CATEGORIA', 'FORNECEDOR', 'CX C/', 'PLT C/', 
                  'CONDIÇÃO PROMO', 'BASE RECOMP.', 'EM FALTA?']
tabela_preco_on.drop(columns=[c for c in col_on_excluir if c in tabela_preco_on.columns], inplace=True)
tabela_preco_on['PREÇO'] = pd.to_numeric(tabela_preco_on['PREÇO'].str.replace(',', '.'), errors='coerce').round(2)

# --- TABELA PROMO ---
tabela_promo = pd.read_excel(
    _bpd.com_fallback(
        _bpd.caminho_preco_promo,
        r"G:\Drives compartilhados\Off Trade\Campanhas e Metas\PREÇO PROMO.xlsx"
    ),
    sheet_name='Plan1', dtype=str
)
tabela_promo['PREÇO PROMO'] = pd.to_numeric(tabela_promo['PREÇO PROMO'].str.replace(',', '.'), errors='coerce').round(2)

# 3. Extração de Dados do Banco (Query Corrigida para evitar duplicidade de PVENDA)
tabela_mov = """
    SELECT 
        NUMPED, NUMNOTA, DATA, CLIENTE, CODPROD, 
        ROUND(TOTAL / NULLIF(QT, 0), 2) AS PVENDA, 
        DESCRICAO, NOME, QT, TOTAL, CODCOB
    FROM CRC.PBI_PCPEDI
    WHERE 
        TRUNC(DATA, 'MM') = TRUNC(SYSDATE, 'MM')
        AND NOME LIKE '%OFF TRADE%'   
"""
df = carregar_dados(tabela_mov, engine, "conferencia_preco")
profit = pd.read_excel(_bpd.com_fallback(
    _bpd.caminho_profit_rj,
    r"G:\Drives compartilhados\Profit RJ\Controle de ultima entrada, descontos-acréscimos e precificação RJ - versão 1.xlsb"
), sheet_name='Precificação', skiprows=8)
profit.drop(columns=['Unnamed: 0', 'PRODUTO', 'FORNECEDORA', 'TIPO',
       'NACIONALIDADE', 'PAUTA', 'MVA%', 'BASE DO ST', 'CUSTO SEM ST/IPI',
       'CUSTO COM ST/IPI', 'PREÇO DE VENDA', 'PREÇO SEM ST',
       'PREÇO PROMO (S.A)', 'MARGEM', 'MARKUP', 'VL.CONTA',
       '90% ACIMA DA PAUTA?', 'DESCONTO/ACRESCIMO', 'VALOR', 'BASE DE RETENÇÃO'], inplace=True)
df.columns = df.columns.str.upper()
df['CODPROD'] = df['CODPROD'].astype(str).str.strip()

df = df.merge(tabela_preco_on[['COD CRC', 'PREÇO']], left_on='CODPROD', right_on='COD CRC', how='left')
df = df.merge(tabela_promo[['COD PROMO', 'PREÇO PROMO']], left_on='CODPROD', right_on='COD PROMO', how='left')
profit['CODIGO'] = profit['CODIGO'].astype(str).str.strip()
df = df.merge(profit[['CODIGO', 'CUSTO COM DESCONTO']], left_on='CODPROD', right_on='CODIGO', how='left')

df = df.rename(columns={'PREÇO': 'PREÇO ON'})

def aplicar_conferencia(df_local):

    df_local['MENOR_VALOR'] = df_local[['PREÇO ON', 'PREÇO PROMO']].min(axis=1).round(2)
    df_local['MAIOR_VALOR'] = df_local[['PREÇO ON', 'PREÇO PROMO']].max(axis=1).round(2)

    def classificar(row):
        pv = row['PVENDA']
        menor = row['MENOR_VALOR']
        maior = row['MAIOR_VALOR']

        if pv < menor:
            return 'ABAIXO DA TABELA'

        if pd.notna(maior) and pv > maior:
            return 'ACIMA DA TABELA'

        if pd.notna(row['PREÇO PROMO']) and pv <= row['PREÇO PROMO']:
            return 'PREÇO PROMO'

        elif pd.notna(row['PREÇO ON']) and pv <= row['PREÇO ON']:
            return 'PREÇO ON'

        else:
            return 'ACIMA DA TABELA'

    df_local['STATUS_CONFERENCIA'] = df_local.apply(classificar, axis=1)
    return df_local

df = aplicar_conferencia(df)

# 6. Limpeza e Cálculos Finais
# Remove colunas auxiliares de join
df.drop(columns=['COD CRC', 'COD PROMO'], inplace=True, errors='ignore')

# Filtra vendedores indesejados
vendedores_remover = [
    'JEAN MENEZES - OFF TRADE', 'DOUGLAS SCHADE - OFF TRADE', 
    'ENEIVA RODRIGUES - OFF TRADE', 'ZEINALDO DE OLIVEIRA - OFF TRADE', 
    'EUDES MORGAN - OFF TRADE', 'RAQUEL ARAUJO - OFF TRADE','JOAO VICTOR DA ROCHA - OFF TRADE',
    'MARA DEPOLLI - OFF TRADE','GILDO ADRIANO - OFF TRADE','CARLOS TERRA - OFF TRADE','JOSIETH LIMA - OFF TRADE','FRANZ BENEVIDES - OFF TRADE','WANDERSON FERREIRA - OFF TRADE',
    'TIAGO SILVA - OFF TRADE','ROSENIR RIBEIRO - OFF TRADE','ALDICEIA PEIXOTO - OFF TRADE','RICARDO CLAUDIO - OFF TRADE',
]
df = df[~df['NOME'].isin(vendedores_remover)]

# Cálculo da Porcentagem de Diferença em relação ao Menor Valor
df["PORCENTAGEM DIFERENÇA"] = np.where(
    df['MENOR_VALOR'] > 0,
    (abs(df['PVENDA'] - df['MENOR_VALOR']) / df['MENOR_VALOR']) * 100,
    0
)
df['PORCENTAGEM DIFERENÇA'] = df['PORCENTAGEM DIFERENÇA'].round(2).apply(lambda x: f"{x:.2f}%")
# Converter as colunas para numérico (forçando erro a virar NaN)
df['PVENDA'] = pd.to_numeric(df['PVENDA'], errors='coerce')
df['CUSTO COM DESCONTO'] = pd.to_numeric(df['CUSTO COM DESCONTO'], errors='coerce')

# Agora o cálculo deve funcionar
df['MARGEM'] = (df['PVENDA'] - df['CUSTO COM DESCONTO']) / df['CUSTO COM DESCONTO']
    
# Opcional: Tratar possíveis NaNs ou divisões por zero geradas
df['MARGEM'] = df['MARGEM'].fillna(0)
df['DATA'] = pd.to_datetime(df['DATA']).dt.date
hoje = date.today()
# ontem = date.today() - timedelta(days=1)
df = df[df['DATA'] == hoje]
df = df[(df['MARGEM'] <= 0) | (df['MARGEM'] > 1)]

# 4. Ordenar os dados
df = df.sort_values(by='DATA', ascending=False)

# 5. SÓ AGORA transformar em texto para exibição/relatório
df['MARGEM'] = df['MARGEM'].round(2).apply(lambda x: f"{x:.2f}%")