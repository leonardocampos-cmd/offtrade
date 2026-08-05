"""
Gera acessos_data.js — auditoria de acessos ao OfftradeHub (quem acessou e
o que acessou), pra alimentar acessos.html (página restrita a
leonardo.campos@rigarr.com.br).

Roda SÓ na VPS (via cron próprio, independente do main.py/pipeline), lendo
o log dedicado do nginx (/var/log/nginx/offtrade_access.log, formato
"offtrade_track" definido em nginx.conf) e correlacionando cada request com
o cookie offtrade_token (o mesmo JWT-like usado pelo SSO do Streamlit —
decodifica só o e-mail do payload, sem checar assinatura, mesmo esquema de
utils.py:_decode_email). Requests sem esse cookie (não autenticados, bots,
scanners) são contados no total mas não aparecem no detalhamento por pessoa.

Mantém estado acumulado em acessos_state.json (watermark = timestamp da
última linha já processada + totais por usuário). Sem isso, o histórico
sumiria toda vez que o logrotate do nginx girasse o arquivo (diário, config
padrão do Debian) — o script só olha pra frente do watermark, nunca reseta,
então "retroativo" aqui significa "desde que essa auditoria foi ligada",
sem perder nada dali em diante mesmo com o log girando.

Localmente (fora da VPS) o arquivo de log não existe — o script roda sem
erro e gera um payload vazio, só pra não quebrar testes/dev.
"""
import base64
import json
import os
import re
import shutil
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from urllib.parse import unquote

from dotenv import load_dotenv

BASE = Path(__file__).parent
load_dotenv(BASE / ".env")
LOG_PATH = Path("/var/log/nginx/offtrade_access.log")
LOG_PATH_ROTACIONADO = Path("/var/log/nginx/offtrade_access.log.1")
STATE_PATH = BASE / "acessos_state.json"
OUT_PATH = BASE / "acessos_data.js"

# Requests que não são "página visitada" de verdade — assets e rotas internas.
_SKIP_EXT = re.compile(r"\.(js|css|png|jpe?g|gif|svg|ico|woff2?|map|xml|txt|json)(\?|$)", re.IGNORECASE)
_SKIP_PATH = re.compile(r"^/(_stcore|static|_oauth_callback|health|vencimento)(/|$)")

_NGINX_ESCAPE_RE = re.compile(r"\\x([0-9A-Fa-f]{2})")


def _cookie_dict(cookie_raw: str):
    """Decodifica o valor bruto do cookie offtrade_token vindo do log do nginx.

    Em tráfego real (login.html/metas.html sempre fazem encodeURIComponent
    antes de setar o cookie) o valor chega percent-encoded, sem problema.
    Mas se o valor tiver aspas literais não escapadas (ex.: cookie setado
    "na mão" fora do fluxo normal), o nginx as escapa como \\xHH no próprio
    arquivo de log (modo de escape padrão dele) — sem reverter isso antes do
    json.loads, a linha seria descartada como se fosse tráfego anônimo.
    """
    if not cookie_raw or cookie_raw == "-":
        return None
    unescaped = _NGINX_ESCAPE_RE.sub(lambda m: chr(int(m.group(1), 16)), cookie_raw)
    for candidato in (unescaped, unquote(unescaped), unquote(cookie_raw), cookie_raw):
        try:
            obj = json.loads(candidato)
            if isinstance(obj, dict):
                return obj
        except Exception:
            continue
    return None


def _decode_email(id_token: str) -> str:
    try:
        payload = id_token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        return json.loads(base64.urlsafe_b64decode(payload)).get("email", "").strip().lower()
    except Exception:
        return ""


def _nome_por_email(email: str, vendedores_auth: dict) -> str:
    for info in vendedores_auth.values():
        # RCA pode mapear pra um vendedor só (dict, formato antigo) ou pra
        # vários (list, formato novo — mesmo RCA em estados/schemas
        # diferentes é gente diferente).
        candidatos = info if isinstance(info, list) else [info]
        for c in candidatos:
            if c.get("email", "").lower() == email or c.get("email2", "").lower() == email:
                return c.get("nome", "").replace("- OFF TRADE", "").replace("-OFF TRADE", "").strip()
    return email.split("@")[0].replace(".", " ").title()


def _carregar_vendedores_auth() -> dict:
    path = BASE / "vendedores_auth_data.js"
    if not path.exists():
        return {}
    raw = path.read_text(encoding="utf-8")
    try:
        return json.loads(raw.split("=", 1)[1].rstrip("; \n"))
    except Exception:
        return {}


def _parse_linha(linha: str):
    partes = linha.rstrip("\n").split(" ||| ")
    if len(partes) != 6:
        return None
    time_iso, ip, uri, status, cookie_raw, user_agent = partes
    return {
        "time_iso": time_iso, "ip": ip, "uri": uri, "status": status,
        "cookie_raw": cookie_raw, "user_agent": user_agent,
    }


def _carregar_state() -> dict:
    if STATE_PATH.exists():
        try:
            return json.loads(STATE_PATH.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"watermark": "", "total_requests": 0, "total_anonimos": 0, "usuarios": {}}


def _salvar_state(state: dict):
    STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


vendedores_auth = _carregar_vendedores_auth()
state = _carregar_state()
watermark = state["watermark"]
max_visto = watermark

# Linhas do arquivo rotacionado (.1) só importam se ainda forem mais novas
# que o watermark — cobre o caso raro de o cron não ter rodado bem na hora
# exata da rotação diária do nginx (logrotate), sem reprocessar o que já
# tinha sido contado antes de girar.
arquivos = [p for p in (LOG_PATH_ROTACIONADO, LOG_PATH) if p.exists()]

for path in arquivos:
    with open(path, encoding="utf-8", errors="replace") as f:
        for linha in f:
            row = _parse_linha(linha)
            if not row:
                continue
            if row["time_iso"] <= watermark:
                continue

            state["total_requests"] += 1
            if row["time_iso"] > max_visto:
                max_visto = row["time_iso"]

            uri_sem_query = row["uri"].split("?", 1)[0]
            if _SKIP_EXT.search(uri_sem_query) or _SKIP_PATH.match(uri_sem_query):
                continue

            cookie_raw = row["cookie_raw"]
            token = _cookie_dict(cookie_raw) if cookie_raw not in ("-", "") else None
            email = _decode_email(token.get("id_token", "")) if token else ""

            if not email:
                state["total_anonimos"] += 1
                continue

            u = state["usuarios"].setdefault(email, {
                "email": email,
                "nome": _nome_por_email(email, vendedores_auth),
                "primeira_visita": row["time_iso"],
                "ultima_visita": row["time_iso"],
                "total_acessos": 0,
                "paginas": {},
            })
            u["total_acessos"] += 1
            u["ultima_visita"] = row["time_iso"]
            if row["time_iso"] < u["primeira_visita"]:
                u["primeira_visita"] = row["time_iso"]
            pag = u["paginas"].setdefault(uri_sem_query, {"contagem": 0, "ultima_visita": ""})
            pag["contagem"] += 1
            pag["ultima_visita"] = row["time_iso"]
            # nome pode ter sido "RCA X"/prefixo de e-mail numa primeira
            # passada (antes de vendedores_auth_data.js existir/atualizar) —
            # reavalia a cada rodada pra corrigir sozinho quando o cadastro aparecer.
            u["nome"] = _nome_por_email(email, vendedores_auth)

state["watermark"] = max_visto
_salvar_state(state)

usuarios_out = []
for u in sorted(state["usuarios"].values(), key=lambda x: x["ultima_visita"], reverse=True):
    paginas_out = sorted(
        ({"pagina": p, **info} for p, info in u["paginas"].items()),
        key=lambda x: x["contagem"], reverse=True,
    )
    usuarios_out.append({
        "email": u["email"],
        "nome": u["nome"],
        "primeira_visita": u["primeira_visita"],
        "ultima_visita": u["ultima_visita"],
        "total_acessos": u["total_acessos"],
        "paginas": paginas_out,
    })

rastreando_desde = min((u["primeira_visita"] for u in usuarios_out), default="")

payload = {
    "atualizado_em": datetime.now().strftime("%d/%m/%Y %H:%M"),
    "rastreando_desde": rastreando_desde,
    "total_requests": state["total_requests"],
    "total_anonimos": state["total_anonimos"],
    "usuarios": usuarios_out,
}

with open(OUT_PATH, "w", encoding="utf-8") as f:
    f.write(f"// Gerado automaticamente\nconst ACESSOS_DATA = {json.dumps(payload, ensure_ascii=False, indent=2)};\n")

print(f"OK acessos_data.js — {len(usuarios_out)} usuários identificados (acumulado), "
      f"{state['total_requests']} requests, {state['total_anonimos']} anônimos")

# Copia direto pro site estático, igual ao passo final de main.py quando
# OFFTRADE_RUNTIME=vps — este script roda no seu próprio cron, mais frequente
# que o pipeline principal, pra manter a auditoria quase em tempo real.
if os.getenv("OFFTRADE_RUNTIME") == "vps":
    try:
        shutil.copy(OUT_PATH, "/opt/offtrade-static/acessos_data.js")
        print("OK acessos_data.js copiado para /opt/offtrade-static")
    except Exception as e:
        print(f"[AVISO] cópia para /opt/offtrade-static falhou: {e}")
