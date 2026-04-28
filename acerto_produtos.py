import polars as pl
import oracledb
from sqlalchemy import create_engine
import os
from dotenv import load_dotenv

load_dotenv()

oracledb.init_oracle_client(lib_dir=r"C:\instantclient")
user = os.getenv('VPN_USER')
password = os.getenv('VPN_PASSWORD')
dsn_crc = os.getenv('DSN_CRC')
dsn_tk = os.getenv('DSN_TK')
dsn_sp = os.getenv('DSN_SPON')
dsn_mg = os.getenv('DSN_SPON')

engine_crc = create_engine(f'oracle+oracledb://{user}:{password}@{dsn_crc}')
engine_tk = create_engine(f'oracle+oracledb://{user}:{password}@{dsn_tk}')
engine_sp = create_engine(f'oracle+oracledb://{user}:{password}@{dsn_sp}')
engine_mg = create_engine(f'oracle+oracledb://{user}:{password}@{dsn_mg}')

