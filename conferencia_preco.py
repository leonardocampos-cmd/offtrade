#CONFERENCIA PREÇO
import os
import pandas as pd
import oracledb
from sqlalchemy import create_engine
from datetime import datetime, date, timedelta
import numpy as np
import warnings
from urllib.parse import quote_plus

# Filtra avisos específicos do openpyxl
warnings.filterwarnings("ignore", category=UserWarning, module="openpyxl")

# 1. Configuração de Conexão
from utils import ORACLE_LIB
oracledb.init_oracle_client(lib_dir=ORACLE_LIB)
user = os.environ["VPN_USER"]
password = os.environ["VPN_PASSWORD"]
# CRC_USER/CRC_PASSWORD é uma conta dedicada com grant em CRC.PCUSUARI (usada
# em meta.py/login_api.py) — VPN_USER sozinho não enxerga essa tabela num JOIN
# (ORA-00942 confirmado em 2026-08-04 ao juntar PBI_PCPEDI com PCUSUARI pra
# filtrar por ESTADO do vendedor).
crc_user = os.getenv("CRC_USER", user)
crc_pass = os.getenv("CRC_PASSWORD", password)
dsn = "crc_oci"
engine = create_engine(
    f'oracle+oracledb://{crc_user}:{quote_plus(crc_pass)}@{dsn}',
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
tabela_preco_on.columns = tabela_preco_on.columns.str.strip()
col_on_excluir = ['Unnamed: 0', 'COD BRASIL', 'PRODUTOS', 'CATEGORIA', 'FORNECEDOR', 'PLT C/',
                  'CONDIÇÃO PROMO', 'BASE RECOMP.', 'EM FALTA?']
tabela_preco_on.drop(columns=[c for c in col_on_excluir if c in tabela_preco_on.columns], inplace=True)
tabela_preco_on['PREÇO'] = pd.to_numeric(tabela_preco_on['PREÇO'].str.replace(',', '.'), errors='coerce').round(2)
tabela_preco_on['PREÇO PROMOCIONAL'] = pd.to_numeric(tabela_preco_on['PREÇO PROMOCIONAL'].str.replace(',', '.'), errors='coerce').round(2)
# CX C/ só é numérico quando a embalagem é "caixa fechada" (ex.: "12"); formatos
# como "2 unid"/"4 PACKS"/"-" ficam NaN e o LIMITADOR (que só existe pros SKUs
# com CX C/ numérico) simplesmente não é aplicável nesses casos.
tabela_preco_on['CX C/'] = pd.to_numeric(tabela_preco_on['CX C/'], errors='coerce')

# --- TABELA PROMO ---
tabela_promo = pd.read_excel(
    _bpd.com_fallback(
        _bpd.caminho_preco_promo,
        r"G:\Drives compartilhados\Off Trade\Campanhas e Metas\PREÇO PROMO_RJ.xlsx"
    ),
    sheet_name='Plan1', dtype=str
)
tabela_promo['PREÇO PROMO'] = pd.to_numeric(tabela_promo['PREÇO PROMO'].str.replace(',', '.'), errors='coerce').round(2)
tabela_promo['LIMITADOR'] = tabela_promo['LIMITADOR'].str.strip().str.upper()
# LIMITADOR pode ser "N CX POR SKU" (limite de quantidade, extrai o N),
# "OTD" ou "OTI" (a promo só vale pro cliente cadastrado na base
# correspondente — ver bloco BASES OTD/OTI abaixo). Fica NaN pros dois
# últimos casos, tratados à parte por não serem numéricos.
tabela_promo['LIMITADOR_CX'] = pd.to_numeric(
    tabela_promo['LIMITADOR'].str.extract(r'(\d+)', expand=False), errors='coerce'
)
# Alguns COD PROMO aparecem em várias linhas — preços antigos esquecidos na
# planilha (fica com o menor, mais conservador) e/ou o MESMO produto com
# LIMITADOR "OTD" numa linha e "OTI" noutra (regras por canal, não uma tag
# única) — por isso TEM_OTD/TEM_OTI marcam presença em vez de usar 'first',
# que descartava uma das duas tags quando as duas apareciam (bug encontrado
# em 2026-08-06: produtos com linha OTD E linha OTI, ex. COD PROMO 1757).
tabela_promo['TEM_OTD'] = tabela_promo['LIMITADOR'] == 'OTD'
tabela_promo['TEM_OTI'] = tabela_promo['LIMITADOR'] == 'OTI'
tabela_promo = tabela_promo.groupby('COD PROMO', as_index=False).agg(
    {'PREÇO PROMO': 'min', 'LIMITADOR_CX': 'min', 'TEM_OTD': 'any', 'TEM_OTI': 'any'}
)

# --- BASES DE CLIENTES OTD/OTI ---
# Produtos com LIMITADOR "OTD"/"OTI" só valem o preço promo pro CODCLI
# cadastrado na base correspondente — pedido do usuário em 2026-08-06.
def _carregar_codclis_base(caminho_fn, caminho_fallback, sheet_name, nome_base):
    try:
        df_base = pd.read_excel(
            _bpd.com_fallback(caminho_fn, caminho_fallback),
            sheet_name=sheet_name, dtype=str,
        )
        df_base.columns = df_base.columns.str.strip()
        codclis = set(df_base['CÓDIGO'].dropna().str.strip())
        print(f"Base {nome_base}: {len(codclis)} cliente(s)")
        return codclis
    except Exception as e:
        print(f"[AVISO] Base {nome_base} indisponível ({str(e)[:100]}) — LIMITADOR {nome_base} tratado como sem cliente elegível (promo não aplica)")
        return set()

_codclis_otd = _carregar_codclis_base(
    _bpd.caminho_base_otd,
    r"G:\Drives compartilhados\Off Trade\Campanhas e Metas\BASE OTD.xlsx",
    "BASE CENSUS OTD Q1_FY27", "OTD",
)
_codclis_oti = _carregar_codclis_base(
    _bpd.caminho_base_oti,
    r"G:\Drives compartilhados\Off Trade\Campanhas e Metas\BASE OTI JUNHO.xlsx",
    "Planilha1", "OTI",
)

# 3. Extração de Dados do Banco (Query Corrigida para evitar duplicidade de PVENDA)
# Join com PCUSUARI só pra filtrar por ESTADO do vendedor (PBI_PCPEDI não tem
# essa coluna) — a TABELA DE PREÇO RJ.xlsx só vale pra vendedor RJ; sem esse
# filtro, vendedor OFF TRADE de outro estado (ex.: ES) também entrava e era
# comparado contra a tabela errada.
tabela_mov = """
    SELECT
        P.NUMPED, P.NUMNOTA, P.DATA, P.CLIENTE, P.CODCLI, P.CODPROD,
        ROUND(P.TOTAL / NULLIF(P.QT, 0), 2) AS PVENDA,
        P.DESCRICAO, P.NOME, P.QT, P.TOTAL, P.CODCOB
    FROM CRC.PBI_PCPEDI P
    JOIN CRC.PCUSUARI U ON P.CODUSUR = U.CODUSUR
    WHERE
        TRUNC(P.DATA, 'MM') = TRUNC(SYSDATE, 'MM')
        AND P.NOME LIKE '%OFF TRADE%'
        AND U.ESTADO = 'RJ'
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

df = df.merge(tabela_preco_on[['COD CRC', 'PREÇO', 'PREÇO PROMOCIONAL', 'CX C/']], left_on='CODPROD', right_on='COD CRC', how='left')
df = df.merge(tabela_promo[['COD PROMO', 'PREÇO PROMO', 'LIMITADOR_CX', 'TEM_OTD', 'TEM_OTI']], left_on='CODPROD', right_on='COD PROMO', how='left')
df['CODCLI'] = df['CODCLI'].astype(str).str.strip()
df['TEM_OTD'] = df['TEM_OTD'].fillna(False)
df['TEM_OTI'] = df['TEM_OTI'].fillna(False)
profit['CODIGO'] = profit['CODIGO'].astype(str).str.strip()
df = df.merge(profit[['CODIGO', 'CUSTO COM DESCONTO']], left_on='CODPROD', right_on='CODIGO', how='left')

df = df.rename(columns={'PREÇO': 'PREÇO ON'})

# Acima do LIMITADOR (ex.: "2 CX POR SKU"), o PREÇO PROMO da PREÇO PROMO_RJ.xlsx
# deixa de valer pra linha — zera pra NaN antes de entrar na conferência.
df['QT'] = pd.to_numeric(df['QT'], errors='coerce')
_qt_em_caixas = df['QT'] / df['CX C/']
_excede_limitador_qtd = df['LIMITADOR_CX'].notna() & _qt_em_caixas.notna() & (_qt_em_caixas > df['LIMITADOR_CX'])

# LIMITADOR "OTD"/"OTI": promo só vale se o CODCLI estiver numa das bases
# correspondentes às regras do produto — se o produto tiver as duas linhas
# (OTD e OTI), basta estar em uma delas (ver BASES DE CLIENTES OTD/OTI acima).
_precisa_otd_ou_oti = df['TEM_OTD'] | df['TEM_OTI']
_cliente_elegivel_otd_oti = (
    (df['TEM_OTD'] & df['CODCLI'].isin(_codclis_otd))
    | (df['TEM_OTI'] & df['CODCLI'].isin(_codclis_oti))
)
_bloqueado_otd_oti = _precisa_otd_ou_oti & ~_cliente_elegivel_otd_oti

df.loc[_excede_limitador_qtd | _bloqueado_otd_oti, 'PREÇO PROMO'] = np.nan

def aplicar_conferencia(df_local):

    _precos_promo = ['PREÇO PROMO', 'PREÇO PROMOCIONAL']
    df_local['MENOR_VALOR'] = df_local[['PREÇO ON'] + _precos_promo].min(axis=1).round(2)
    df_local['MAIOR_VALOR'] = df_local[['PREÇO ON'] + _precos_promo].max(axis=1).round(2)
    df_local['MELHOR_PROMO'] = df_local[_precos_promo].min(axis=1)

    def classificar(row):
        pv = row['PVENDA']
        menor = row['MENOR_VALOR']
        maior = row['MAIOR_VALOR']

        if pv < menor:
            return 'ABAIXO DA TABELA'

        if pd.notna(maior) and pv > maior:
            return 'ACIMA DA TABELA'

        if pd.notna(row['MELHOR_PROMO']) and pv <= row['MELHOR_PROMO']:
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