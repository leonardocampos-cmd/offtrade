# #logica

# peso_atig = 0.5
# atingimento = 10
# total_atig = peso_atig * atingimento if peso_atig * atingimento > peso_atig else peso_atig * atingimento
# total_liquidados = 40000
# comissao = (total_atig * total_liquidados)/100
# comissao = round(comissao, 2)
# print(f"Total atingimento: {total_atig}%")
# print(f"Total comissão: R$ {comissao}")

import pandas as pd
import oracledb
from sqlalchemy import create_engine
import os
import numpy as np
import time
from dotenv import load_dotenv

load_dotenv()
oracledb.init_oracle_client(lib_dir=os.getenv("ORACLE_LIB", r"C:\instantclient"))

user = os.environ["VPN_USER"]
password = os.environ["VPN_PASSWORD"]
dsn = "crc_oci"

engine = create_engine(
    f'oracle+oracledb://{user}:{password}@{dsn}',
    pool_pre_ping=True,
    pool_recycle=3600,
    connect_args={"expire_time": 2}
)

# --- FUNÇÃO DE CARGA COM RETRY ---
def carregar_dados(query, engine, nome_tabela="tabela", max_tentativas=3):
    for tentativa in range(1, max_tentativas + 1):
        try:
            print(f"-> Lendo {nome_tabela} (Tentativa {tentativa}/{max_tentativas})...")
            
            # Use o engine diretamente ou uma conexão
            # Mudança crucial: Chamar carregar_dados, não carregar_dados
            chunks = []
            for chunk in pd.read_sql(query, engine, chunksize=5000):
                chunks.append(chunk)
            
            if not chunks:
                return pd.DataFrame() # Retorna vazio se não houver dados
                
            df = pd.concat(chunks, ignore_index=True)
            df.columns = df.columns.str.strip().str.lower() # Padroniza colunas
            print(f"✅ {nome_tabela} carregada!")
            return df

        except Exception as e:
            print(f"⚠️ Erro na {nome_tabela}: {str(e)[:100]}")
            # Se der erro de conexão, resetamos o pool
            engine.dispose()
            if tentativa < max_tentativas:
                time.sleep(10)
            else:
                raise e

tabela_liquidados = carregar_dados("""
    SELECT *
    FROM crc.ROTINA_1048
""", engine,"Liquidados")
tabela_liquidados.to_csv("liquidados.csv", index=False)