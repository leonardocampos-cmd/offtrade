"""
Gera vendedores_auth_data.js com o mapeamento CODUSUR → e-mail
para autenticação da Área do Vendedor.

Consulta: SPON.PCUSUARI WHERE NOME LIKE '%OFF TRADE%'
"""
import json
import pandas as pd
from datetime import datetime
from pathlib import Path
from meta import engine_spon, carregar_dados

df = carregar_dados(
    "SELECT CODUSUR, NOME, EMAIL FROM SPON.PCUSUARI WHERE NOME LIKE '%OFF TRADE%'",
    engine_spon,
    "vendedores_auth",
)

auth = {}
for _, row in df.iterrows():
    try:
        codusur = str(int(row["CODUSUR"]))
    except (ValueError, TypeError):
        continue
    email_raw = row.get("EMAIL")
    email = "" if pd.isna(email_raw) else str(email_raw).strip().lower()
    nome  = (row.get("NOME")  or "").strip()
    if codusur and email:
        auth[codusur] = {"nome": nome, "email": email}

now = datetime.now().strftime("%d/%m/%Y %H:%M")
js  = f"// Gerado em {now}\nconst VENDEDORES_AUTH = {json.dumps(auth, ensure_ascii=False, indent=2)};\n"
Path("vendedores_auth_data.js").write_text(js, encoding="utf-8")
print(f"[OK] vendedores_auth_data.js — {len(auth)} vendedores exportados")
