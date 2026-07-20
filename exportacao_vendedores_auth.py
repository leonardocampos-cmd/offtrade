"""
Gera vendedores_auth_data.js com o mapeamento CODUSUR → e-mail
para autenticação da Área do Vendedor.

Consulta PCUSUARI WHERE NOME LIKE '%OFF TRADE%' em todos os schemas
(CRC, thekings, CASTAS, GARRIDO, SPON, MGON) para cobrir vendedores
de todos os estados, não só RJ.
"""
import json
import pandas as pd
from datetime import datetime
from pathlib import Path
from meta import (
    engine, engine_theking, engine_castas, engine_garrido,
    engine_spon, engine_mgon, carregar_dados,
)

_SCHEMAS = [
    ("CRC",      engine),
    ("thekings", engine_theking),
    ("CASTAS",   engine_castas),
    ("GARRIDO",  engine_garrido),
    ("SPON",     engine_spon),
    ("MGON",     engine_mgon),
]

_partes = []
for _s, _e in _SCHEMAS:
    try:
        _partes.append(carregar_dados(
            f"SELECT CODUSUR, NOME, EMAIL, EMAIL2, ESTADO FROM {_s}.PCUSUARI WHERE NOME LIKE '%OFF TRADE%'",
            _e,
            f"vendedores_auth_{_s}",
        ))
    except Exception as _ex:
        print(f"[AVISO] vendedores_auth_{_s} falhou — ignorado ({_ex})")

df = pd.concat(_partes, ignore_index=True) if _partes else pd.DataFrame(
    columns=["CODUSUR", "NOME", "EMAIL", "EMAIL2", "ESTADO"]
)

auth = {}
for _, row in df.iterrows():
    try:
        codusur = str(int(row["CODUSUR"]))
    except (ValueError, TypeError):
        continue
    email_raw  = row.get("EMAIL")
    email2_raw = row.get("EMAIL2")
    estado_raw = row.get("ESTADO")
    email  = "" if pd.isna(email_raw)  else str(email_raw).strip().lower()
    email2 = "" if pd.isna(email2_raw) else str(email2_raw).strip().lower()
    estado = "" if pd.isna(estado_raw) else str(estado_raw).strip().upper()
    nome   = (row.get("NOME") or "").strip()
    if codusur and email:
        auth[codusur] = {"nome": nome, "email": email, "email2": email2, "estado": estado}

now = datetime.now().strftime("%d/%m/%Y %H:%M")
js  = f"// Gerado em {now}\nconst VENDEDORES_AUTH = {json.dumps(auth, ensure_ascii=False, indent=2)};\n"
Path("vendedores_auth_data.js").write_text(js, encoding="utf-8")
print(f"[OK] vendedores_auth_data.js — {len(auth)} vendedores exportados")
