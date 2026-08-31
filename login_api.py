"""
Serviço de autenticação de vendedor (Flask), separado do controle_vencimento.py
por pedido explícito — roda como processo/systemd/porta própria na VPS.

Valida a SENHA real do vendedor direto na PCUSUARI (Oracle) — o
vendedores_auth_data.js (arquivo público, baixado por qualquer um que abra
login.html) nunca chega a conter a senha de ninguém; só nome/e-mail/estado.

CODUSUR não é único entre os 6 schemas Winthor (RJ, MG etc. reaproveitam o
mesmo número pra pessoas diferentes — confirmado em 2026-08-03, caso Jeter x
Fabio, ambos CODUSUR 378) — por isso consulta todos e usa o e-mail
informado pra desempatar, igual exportacao_vendedores_auth.py já faz.

Uso local: python login_api.py  (abre em http://localhost:5051)
Na VPS roda atrás do nginx em /api/auth/ (ver deploy_login_api_vps.py).
"""
import base64
import json
import os
import time
from urllib.parse import quote_plus, urlencode

import oracledb
import pandas as pd
import requests
from sqlalchemy import create_engine, text
from dotenv import load_dotenv
from flask import Flask, Blueprint, request, jsonify, redirect

load_dotenv()

# ── OAuth Google (login unificado: identidade + permissão de Gmail) ────────
# Pedido do usuário em 2026-08-31: quem loga com Google (vendedor ou gestor)
# já concede de uma vez a permissão de mandar e-mail pelo próprio Gmail
# (usada por credito_cadastro_api.py), em vez de um consentimento separado
# só na hora de mandar a solicitação de cadastro. Mesmo Client ID/Secret já
# usados por credito_cadastro_api.py ("Cliente Web 2" no Google Cloud
# Console) — evita credencial nova; só precisou adicionar essa origem/redirect
# na lista de autorizados de lá.
GOOGLE_CLIENT_ID     = os.getenv("GOOGLE_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET", "")
# COOKIE_GMAIL fica em path="/" (não só /api/auth/) de propósito — precisa
# ser lido também por credito_cadastro_api.py (/api/credito/) e por qualquer
# outra rota futura que precise mandar e-mail como a pessoa logada.
COOKIE_GMAIL   = "offtrade_gmail_token"
COOKIE_MAX_AGE = 60 * 60 * 24 * 30  # 30 dias — refresh_token não expira sozinho


def _decode_email_id_token(id_token: str) -> str:
    """Mesmo decode 'cru' (sem verificar assinatura) que login.html já fazia
    do lado do cliente com o id_token do GSI — só pra exibição/matching de
    e-mail, não é usado como prova de identidade em nenhuma decisão de
    segurança adicional além do que já existia."""
    try:
        payload = id_token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        return json.loads(base64.urlsafe_b64decode(payload)).get("email", "")
    except Exception:
        return ""

oracledb.init_oracle_client(lib_dir=os.getenv("ORACLE_LIB", "/opt/oracle/instantclient_21_1"))

_user     = os.environ["VPN_USER"]
_password = os.environ["VPN_PASSWORD"]
_crc_user = os.getenv("CRC_USER", _user)
_crc_pass = os.getenv("CRC_PASSWORD", _password)

# tcp_connect_timeout curto é essencial aqui: CASTAS/GARRIDO usam IP interno
# (só alcançável da rede local) — da VPS, sem esse timeout a conexão trava
# no default de 60s por schema e a rota estoura o proxy_read_timeout do
# nginx antes de responder (504 confirmado em 2026-08-03).
_ENGINE_KW = dict(pool_pre_ping=True, pool_recycle=3600,
                   connect_args={"expire_time": 2, "tcp_connect_timeout": 5})

# CASTAS e GARRIDO (usadas em meta.py/exportacao_vendedores_auth.py) ficam de
# fora aqui: usam IP interno, só alcançável da rede local — da VPS a conexão
# trava por dezenas de segundos mesmo com tcp_connect_timeout curto (504
# confirmado em 2026-08-03) e nenhum dos vendedores com SENHA cadastrada hoje
# está nesses dois schemas. Se algum dia precisar, reavaliar como consultá-
# los sem travar a rota (ex: timeout mais agressivo via thread/future).
_SCHEMAS = [
    ("CRC", create_engine(f"oracle+oracledb://{_crc_user}:{quote_plus(_crc_pass)}@crc_oci", **_ENGINE_KW)),
    ("thekings", create_engine(f"oracle+oracledb://{_user}:{_password}@theking_oci", **_ENGINE_KW)),
    ("SPON", create_engine(f"oracle+oracledb://{_crc_user}:{quote_plus(_crc_pass)}@spon_oci", **_ENGINE_KW)),
    ("MGON", create_engine(f"oracle+oracledb://{_user}:{_password}@{os.getenv('DSN_MG', 'mgon_oci')}", **_ENGINE_KW)),
]

app = Flask(__name__)
bp = Blueprint("auth", __name__, url_prefix="/api/auth")


def _buscar_candidatos(rca: str) -> list[dict]:
    candidatos = []
    for nome_schema, engine in _SCHEMAS:
        # SPON tem vendedores nomeados "W.S" em vez de "...OFF TRADE" (mesmo
        # padrão usado em meta.py/pedidos.py/exportacao_vendedores_auth.py) —
        # sem esse OR, RCA como o 588 (W.S) nunca aparecia na busca e o login
        # falhava com "RCA não encontrado" mesmo com senha certa (2026-08-06).
        nome_filtro = (
            "(NOME LIKE '%OFF TRADE%' OR NOME LIKE '%W.S%')"
            if nome_schema == "SPON" else "NOME LIKE '%OFF TRADE%'"
        )
        try:
            with engine.connect() as conn:
                df = pd.read_sql(
                    text(
                        f"SELECT CODUSUR, NOME, EMAIL, EMAIL2, ESTADO, SENHA "
                        f"FROM {nome_schema}.PCUSUARI "
                        f"WHERE CODUSUR = :rca AND {nome_filtro}"
                    ),
                    conn, params={"rca": rca},
                )
        except Exception:
            continue
        df.columns = df.columns.str.strip().str.upper()
        for _, row in df.iterrows():
            senha_raw = row.get("SENHA")
            candidatos.append({
                "nome":   (row.get("NOME") or "").strip(),
                "email":  str(row.get("EMAIL") or "").strip().lower(),
                "email2": str(row.get("EMAIL2") or "").strip().lower(),
                "estado": str(row.get("ESTADO") or "").strip().upper(),
                "senha":  "" if pd.isna(senha_raw) else str(senha_raw).strip(),
            })
    return candidatos


@bp.route("/login-vendedor", methods=["POST"])
def login_vendedor():
    dados = request.get_json(silent=True) or {}
    rca   = str(dados.get("rca", "")).strip()[:20]
    email = str(dados.get("email", "")).strip().lower()[:100]
    senha = str(dados.get("senha", "")).strip()[:50]

    if not rca or not email or not senha:
        return {"ok": False, "motivo": "Dados incompletos."}, 400

    try:
        candidatos = _buscar_candidatos(rca)
    except Exception:
        return {"ok": False, "motivo": "Não foi possível verificar agora. Tente novamente."}, 200

    if not candidatos:
        return {"ok": False, "motivo": "RCA não encontrado."}, 200

    vendedor = next((c for c in candidatos if email in (c["email"], c["email2"]) and email), None)
    if not vendedor:
        return {"ok": False, "motivo": "E-mail não corresponde ao RCA informado."}, 200

    if not vendedor["senha"] or senha != vendedor["senha"]:
        return {"ok": False, "motivo": "Senha incorreta."}, 200

    return {"ok": True, "nome": vendedor["nome"], "estado": vendedor["estado"], "email": vendedor["email"]}, 200


OAUTH_REDIRECT_URI = "https://offtrade.duckdns.org/api/auth/oauth/callback"
OAUTH_SCOPE = "openid email profile https://www.googleapis.com/auth/gmail.send"


@bp.route("/oauth/login")
def oauth_login():
    """Redirect completo (não popup — testado e descartado: bloqueio de
    popup é comum demais pra depender disso) pro consentimento do Google.
    'rca' e 'voltar' viajam no 'state' (não são segredo, só preservam o que
    a pessoa tinha digitado no formulário antes de sair da página) e voltam
    intactos em /oauth/callback."""
    rca    = request.args.get("rca", "")
    voltar = request.args.get("voltar", "/login.html")
    if not voltar.startswith("/"):
        voltar = "/login.html"
    state = base64.urlsafe_b64encode(json.dumps({"rca": rca, "voltar": voltar}).encode()).decode()
    params = {
        "client_id": GOOGLE_CLIENT_ID,
        "redirect_uri": OAUTH_REDIRECT_URI,
        "response_type": "code",
        "scope": OAUTH_SCOPE,
        "access_type": "offline",
        # "consent" força a tela de consentimento mesmo pra quem já autorizou
        # antes — sem isso o Google só devolve refresh_token na 1ª vez, e sem
        # refresh_token a permissão de Gmail expira em ~1h sem como renovar.
        "prompt": "select_account consent",
        "state": state,
    }
    return redirect("https://accounts.google.com/o/oauth2/v2/auth?" + urlencode(params))


@bp.route("/oauth/callback")
def oauth_callback():
    code  = request.args.get("code")
    state = request.args.get("state", "")
    rca, voltar = "", "/login.html"
    try:
        decoded = json.loads(base64.urlsafe_b64decode(state.encode()).decode())
        rca    = decoded.get("rca", "")
        voltar = decoded.get("voltar", "/login.html")
        if not voltar.startswith("/"):
            voltar = "/login.html"
    except Exception:
        pass

    def _erro():
        sep = "&" if "?" in voltar else "?"
        return redirect(f"{voltar}{sep}g=erro&rca={rca}")

    if not code:
        return _erro()

    try:
        resp = requests.post("https://oauth2.googleapis.com/token", data={
            "code": code,
            "client_id": GOOGLE_CLIENT_ID,
            "client_secret": GOOGLE_CLIENT_SECRET,
            "redirect_uri": OAUTH_REDIRECT_URI,
            "grant_type": "authorization_code",
        }, timeout=10)
        resp.raise_for_status()
        token = resp.json()
    except Exception as e:
        print(f"[OAUTH] troca de code falhou: {str(e)[:300]}")
        return _erro()

    token["expires_at"] = time.time() + token.get("expires_in", 3600)
    email = _decode_email_id_token(token.get("id_token", ""))
    if not email:
        return _erro()
    token["email"] = email

    sep = "&" if "?" in voltar else "?"
    destino = redirect(f"{voltar}{sep}g=ok&rca={rca}")
    destino.set_cookie(COOKIE_GMAIL, json.dumps(token), httponly=True, secure=True,
                        samesite="Lax", max_age=COOKIE_MAX_AGE, path="/")
    return destino


app.register_blueprint(bp)

if __name__ == "__main__":
    # threaded=True é essencial: sem isso o dev server do Flask atende UM
    # request por vez — se um deles travar numa conexão Oracle morta (visto
    # em 2026-08-04, série de ORA-03113 seguida de silêncio total nos logs
    # por quase 20h), todo mundo mais tentando logar fica na fila atrás dele
    # pra sempre, e nenhum crash acontece pro systemd Restart=always agir.
    app.run(host="0.0.0.0", port=5051, threaded=True)
