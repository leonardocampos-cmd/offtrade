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
    # SPON/MGON têm vendedores nomeados "W.S" em vez de "...OFF TRADE" (mesmo
    # padrão de meta.py/pedidos.py) — sem esse OR, RCA como o 588 (W.S) nunca
    # entrava aqui e o login sempre falhava em "RCA não encontrado" já no
    # front, antes até de chamar login_api.py (confirmado em 2026-08-06).
    _nome_f = "(NOME LIKE '%OFF TRADE%' OR NOME LIKE '%W.S%')" if _s in ("SPON", "MGON") else "NOME LIKE '%OFF TRADE%'"
    try:
        _partes.append(carregar_dados(
            f"SELECT CODUSUR, NOME, EMAIL, EMAIL2, ESTADO FROM {_s}.PCUSUARI WHERE {_nome_f} AND BLOQUEIO = 'N'",
            _e,
            f"vendedores_auth_{_s}",
        ))
    except Exception as _ex:
        print(f"[AVISO] vendedores_auth_{_s} falhou — ignorado ({_ex})")

df = pd.concat(_partes, ignore_index=True) if _partes else pd.DataFrame(
    columns=["CODUSUR", "NOME", "EMAIL", "EMAIL2", "ESTADO"]
)

### CODUSUR não é único entre os schemas — RJ e MG (por exemplo) já reutilizaram
### o mesmo número para pessoas diferentes (confirmado em 2026-08-03: CODUSUR 378
### é FABIO VALOTTI no CRC/RJ e JETER LUCIO SOARES no MGON/MG). Guardar um único
### objeto por chave faz um sobrescrever o outro silenciosamente, e quem "ganha"
### depende só da ordem/sucesso das consultas naquela rodada — foi o que deixou
### o Jeter sem acesso mesmo estando cadastrado na fonte. Por isso cada CODUSUR
### guarda uma LISTA de candidatos; login.html desempata pelo e-mail digitado.
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
        candidatos = auth.setdefault(codusur, [])
        if not any(c["email"] == email and c["email2"] == email2 for c in candidatos):
            candidatos.append({"nome": nome, "email": email, "email2": email2, "estado": estado})

_out = Path("vendedores_auth_data.js")

# Trava: se TODAS as 6 bases falharam (ex: VPN fora do ar), auth fica vazio —
# sobrescrever o arquivo bom com {} derrubaria o login de vendedor pra todo
# mundo (aconteceu em 2026-07-31). Sem fonte nenhuma carregada, não escreve
# por cima do que já existe.
if not auth and not _partes and _out.exists():
    print("[AVISO] Todas as bases falharam e nenhum vendedor foi carregado — "
          "mantendo vendedores_auth_data.js existente (não sobrescrevendo com vazio).")
else:
    now = datetime.now().strftime("%d/%m/%Y %H:%M")
    js  = f"// Gerado em {now}\nconst VENDEDORES_AUTH = {json.dumps(auth, ensure_ascii=False, indent=2)};\n"
    _out.write_text(js, encoding="utf-8")
    print(f"[OK] vendedores_auth_data.js — {len(auth)} vendedores exportados")
