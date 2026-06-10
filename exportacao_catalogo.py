# exportacao_catalogo.py — Gera catalogo_data.js com estoque e preços
import json
import warnings
import pandas as pd
import oracledb
from sqlalchemy import create_engine
from pathlib import Path

warnings.filterwarnings("ignore", category=UserWarning, module="openpyxl")

oracledb.init_oracle_client(lib_dir=r"C:\instantclient")

engine = create_engine(
    'oracle+oracledb://vpn:vpn2320vpn@crc_oci',
    pool_pre_ping=True,
    pool_recycle=3600,
    connect_args={"expire_time": 2}
)

HERE = Path(__file__).parent

# ── 1. Produtos do catálogo (gerado pelo _extract_pdf3.py) ───────────────────
catalogo_json = HERE / '_catalogo_produtos.json'
with open(catalogo_json, encoding='utf-8') as f:
    _raw = json.load(f)
catalogo = {k: v.split(' Todas as imagens')[0].strip() for k, v in _raw.items()}

# ── 2. Estoque ────────────────────────────────────────────────────────────────
print("Carregando estoque...")
df_est = pd.read_sql(
    "SELECT codprod, qtestoque FROM crc.ROTINA_105 WHERE codfilial = '2'",
    engine
)
df_est['codprod'] = df_est['codprod'].astype(str).str.strip()
df_est['qtestoque'] = pd.to_numeric(df_est['qtestoque'], errors='coerce').fillna(0)
estoque = df_est.set_index('codprod')['qtestoque'].to_dict()

# ── 3. Tabela ON ──────────────────────────────────────────────────────────────
print("Carregando tabela ON...")
tabela_on = pd.read_excel(
    r"G:\Drives compartilhados\EQUIPE DE VENDAS RJ\TABELA DE PREÇO RJ.xlsx",
    sheet_name='TABELA', skiprows=5, dtype=str
)
tabela_on['COD CRC'] = tabela_on['COD CRC'].astype(str).str.strip()
tabela_on['PRECO_ON'] = pd.to_numeric(
    tabela_on['PREÇO'].str.replace(',', '.'), errors='coerce'
).round(2)
preco_on = tabela_on.set_index('COD CRC')['PRECO_ON'].to_dict()

# ── 4. Tabela PROMO ───────────────────────────────────────────────────────────
print("Carregando tabela PROMO...")
tabela_promo = pd.read_excel(
    r"G:\Drives compartilhados\Off Trade\Campanhas e Metas\PREÇO PROMO.xlsx",
    sheet_name='Plan1', dtype=str
)
tabela_promo['COD PROMO'] = tabela_promo['COD PROMO'].astype(str).str.strip()
tabela_promo['PRECO_PROMO'] = pd.to_numeric(
    tabela_promo['PREÇO PROMO'].str.replace(',', '.'), errors='coerce'
).round(2)
preco_promo = tabela_promo.set_index('COD PROMO')['PRECO_PROMO'].to_dict()

# ── 5. Monta lista de produtos ────────────────────────────────────────────────
print("Montando catálogo...")
produtos = []
for cod, desc in catalogo.items():
    on = preco_on.get(cod)
    promo = preco_promo.get(cod)
    qt = estoque.get(cod, 0)

    # Status do preço ON vs PROMO
    if on and promo:
        status = 'PROMO'
    elif on:
        status = 'ON'
    else:
        status = '-'

    produtos.append({
        'cod': cod,
        'desc': desc,
        'qt': float(qt),
        'on': on if pd.notna(on) and on else None,
        'promo': promo if pd.notna(promo) and promo else None,
        'status': status,
    })

# Ordena por descrição
produtos.sort(key=lambda x: x['desc'])

# ── 7. Escreve JS ─────────────────────────────────────────────────────────────
from datetime import datetime
ts = datetime.now().strftime('%d/%m/%Y %H:%M')
js = f"// Gerado automaticamente em {ts}\nconst CATALOGO_DATA = {json.dumps(produtos, ensure_ascii=False)};\n"

out = HERE / 'catalogo_data.js'
out.write_text(js, encoding='utf-8')

total_qt = sum(p['qt'] for p in produtos if p['qt'] > 0)
print(f"OK — {len(produtos)} produtos | Estoque total: {total_qt:.0f} un")
