"""
Crédito e Cadastro de Cliente — backend (Flask), substitui a antiga página
Streamlit (app_pages/Credito_e_Cadastro.py, removida). Serve só a API que
credito_cadastro.html consome — a página em si é HTML estático, publicada
pelo site (deploy_static_vps.py), igual qualquer outra *.html da raiz.

O e-mail sai pelo Gmail do próprio RCA solicitante — mesmo comportamento da
versão Streamlit original (recuperada do git em 2026-08-31 pra portar essa
lógica pra cá), reimplementado sem o streamlit_oauth: fluxo OAuth clássico do
Google (authorization code, access_type=offline + prompt=consent pra ganhar
refresh_token, escopo gmail.send) com token guardado num cookie httpOnly
(COOKIE_GMAIL) — só o backend lê/renova, nunca passa pelo JS da página.
Pedido explícito do usuário em 2026-08-31: SEM fallback pra conta fixa —
quem não logou com Google (ex: vendedor RCA+senha, sem Google Workspace) não
consegue enviar, mesma limitação que a versão Streamlit original já tinha na
prática (ensure_valid_token() também exigia sessão Google de verdade).

Uso local: python credito_cadastro_api.py  (abre em http://localhost:5055)
Na VPS roda atrás do nginx em /api/credito/ (ver deploy_credito_cadastro_vps.py).

Porta 5055: 5050-5054 já estavam todas ocupadas por outros serviços da VPS
(vencimento, login-api, pedido_reply_bot, whatsapp-resumo, kanban-api) —
colisão descoberta ao implantar pela primeira vez (o serviço entrava em
crash-loop, "Address already in use", primeiro em 5052 e depois em 5053).
"""
import base64
import json
import os
import re
import time
from datetime import datetime
from email.mime.text import MIMEText
from pathlib import Path
from urllib.parse import quote_plus

import oracledb
import pandas as pd
import requests
import urllib3
from urllib.parse import urlencode
from dotenv import load_dotenv
from flask import Flask, Blueprint, request, jsonify, redirect
from sqlalchemy import bindparam, create_engine, text

load_dotenv()
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

RUNTIME = os.getenv("OFFTRADE_RUNTIME", "local")
oracledb.init_oracle_client(lib_dir=os.getenv("ORACLE_LIB", "/opt/oracle/instantclient_21_1"))

_user     = os.environ["VPN_USER"]
_password = os.environ["VPN_PASSWORD"]
_crc_user = os.getenv("CRC_USER", _user)
_crc_pass = os.getenv("CRC_PASSWORD", _password)

_ENGINE_KW = dict(pool_pre_ping=True, pool_recycle=3600, connect_args={"expire_time": 2})

# GARRIDO não tem alias TNS — mesma string de conexão direta usada em
# meta.py::engine_garrido/utils.py::DSN["garrido"] (IP interno, mas
# alcançável a partir da VPS, diferente de CASTAS — ver memória do projeto
# "banco CASTAS: limitação de rede").
_DSN_GARRIDO = os.getenv(
    "DSN_GARRIDO",
    "10.107.213.84:1521/orcl_pdb1.subnetwintcompa.vcnrootautoskyo.oraclevcn.com",
)

_FONTES = [
    ("CRC",     create_engine(f"oracle+oracledb://{_crc_user}:{quote_plus(_crc_pass)}@crc_oci", **_ENGINE_KW)),
    # GARRIDO também tem vendas do RJ — sem isso, cliente cadastrado só na
    # base GARRIDO não aparece na busca (mesmo motivo documentado na versão
    # Streamlit original, pedido do usuário em 2026-08-14).
    ("GARRIDO", create_engine(f"oracle+oracledb://{_user}:{_password}@{_DSN_GARRIDO}", **_ENGINE_KW)),
]

EMAIL_FINANCEIRO  = "cadastro@rigarr.com.br"
EMAIL_CADASTRO_CC = "danielle.soares@rigarr.com.br,leonardo.campos@rigarr.com.br"
CHAVE_API_CNPJ    = os.getenv("CHAVE_API_CNPJ", "")

BASE = Path(__file__).parent
SOLICITACOES_PATH = BASE / "solicitacoes_cadastro.json"

# ── OAuth Google (Gmail do RCA solicitante) ─────────────────────────────────
# Mesmo Client ID/Secret já usados pro login do app Streamlit (utils.py) —
# precisa ter essa URL de callback cadastrada em "URIs de redirecionamento
# autorizados" no Google Cloud Console (feito manualmente pelo usuário,
# 2026-08-31 — esse backend não tem como alterar isso sozinho).
#
# COOKIE_GMAIL é o MESMO nome/path que login_api.py usa (offtrade_gmail_token,
# path="/") — desde 2026-08-31 o login com Google em login.html (vendedor OU
# gestor) já concede esse escopo de cara, então pra quem entrou por ali essa
# rota /oauth/login abaixo nunca chega a ser usada; ela continua existindo só
# como fallback pra quem logou por RCA+senha (sem sessão Google) e quer dar
# essa permissão avulsa só pra mandar a solicitação de cadastro.
GOOGLE_CLIENT_ID     = os.getenv("GOOGLE_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET", "")
OAUTH_REDIRECT_URI   = "https://offtrade.duckdns.org/api/credito/oauth/callback"
OAUTH_SCOPE          = "openid email https://www.googleapis.com/auth/gmail.send"
COOKIE_GMAIL         = "offtrade_gmail_token"
COOKIE_MAX_AGE        = 60 * 60 * 24 * 30  # 30 dias — refresh_token não expira sozinho

app = Flask(__name__)
bp = Blueprint("credito", __name__, url_prefix="/api/credito")


# ── Oracle ────────────────────────────────────────────────────────────────────

def _buscar_rca(codusur: int):
    with _FONTES[0][1].connect() as conn:
        df = pd.read_sql(
            text("SELECT CODUSUR, NOME FROM CRC.PCUSUARI WHERE CODUSUR = :cod"),
            conn, params={"cod": codusur},
        )
    df.columns = df.columns.str.upper()
    return df


_QUERY_CLIENTE = """
    SELECT
        c.codcli,
        c.cliente AS nome,
        c.cgcent AS cnpj,
        c.bloqueio,
        c.codusur1,
        u.nome AS nome_rca1,
        TO_CHAR(c.dtultcomp, 'DD/MM/YYYY') AS dtultcomp,
        TRUNC(SYSDATE) - TRUNC(c.dtultcomp) AS dias_sem_compra,
        TO_NUMBER(NVL(c.limcred, 0)) AS limcred,
        TO_NUMBER(NVL(prest.valor, 0)) AS valor_aberto,
        TO_NUMBER(NVL(ped.vlatend, 0)) AS valor_pedidos
    FROM {schema}.PCCLIENT c
    LEFT JOIN {schema}.PCUSUARI u ON u.codusur = c.codusur1
    LEFT JOIN (
        SELECT codcli, SUM(valor) AS valor
        FROM {schema}.PCPREST
        WHERE vpago IS NULL OR vpago = '0'
        GROUP BY codcli
    ) prest ON c.codcli = prest.codcli
    LEFT JOIN (
        SELECT codcli, SUM(vlatend) AS vlatend
        FROM {schema}.PCPEDC
        WHERE posicao NOT IN ('C', 'F')
        GROUP BY codcli
    ) ped ON c.codcli = ped.codcli
    WHERE {filtro}
"""


def _buscar_cliente(busca: str) -> pd.DataFrame:
    busca = busca.strip()
    cnpj  = re.sub(r"\D", "", busca)

    if cnpj.isdigit() and len(cnpj) == 14:
        filtro = "REPLACE(REPLACE(REPLACE(c.cgcent,'.',''),'/',''),'-','') = :termo"
        params = {"termo": cnpj}
    elif busca.isdigit():
        filtro = "c.codcli = :termo"
        params = {"termo": int(busca)}
    else:
        filtro = "UPPER(c.cliente) LIKE :termo"
        params = {"termo": f"%{busca.upper()}%"}

    frames = []
    for schema, engine in _FONTES:
        try:
            with engine.connect() as conn:
                df = pd.read_sql(
                    text(_QUERY_CLIENTE.format(schema=schema, filtro=filtro)),
                    conn, params=params,
                )
            df.columns = df.columns.str.upper()
            if not df.empty:
                df["FONTE"] = schema
                frames.append(df)
        except Exception:
            # Uma fonte fora do ar não pode derrubar a busca nas outras.
            continue
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


# ── Receita Federal (CNPJ) ──────────────────────────────────────────────────

def _fetch_cnpj(cnpj_limpo: str, incluir_registros: bool = True) -> dict:
    """registrations=BR (Inscrição Estadual) usa um serviço à parte do
    provedor que já ficou fora do ar sozinho mesmo com os dados básicos de
    CNPJ disponíveis — por isso incluir_registros=False existe, pra poder
    cair pro básico em vez de perder a consulta inteira."""
    url = f"https://api.cnpja.com/office/{cnpj_limpo}?simples=true"
    if incluir_registros:
        url += "&registrations=BR"
    resp = requests.get(url, headers={"Authorization": CHAVE_API_CNPJ}, verify=False, timeout=15)
    resp.raise_for_status()
    return resp.json()


_STATUS_IE_OK = {"habilitado", "ativo", "sem restrição", "não encontrada"}


def _consultar_cnpj(cnpj_limpo: str) -> dict:
    """Busca CNPJ + IE, com fallback pro básico se o serviço de IE falhar
    (ver _fetch_cnpj). Devolve os campos já prontos pra exibição/decisão de
    bloqueio, iguais em espírito à versão Streamlit original."""
    ie_indisponivel = False
    try:
        d = _fetch_cnpj(cnpj_limpo, incluir_registros=True)
    except Exception as e1:
        try:
            d = _fetch_cnpj(cnpj_limpo, incluir_registros=False)
            ie_indisponivel = True
            print(f"[CNPJ] {cnpj_limpo}: consulta completa falhou ({str(e1)[:200]}), caiu pro básico com sucesso.")
        except Exception as e2:
            print(f"[CNPJ] {cnpj_limpo}: falha total. Completa: {str(e1)[:200]} | Básica: {str(e2)[:200]}")
            return {"ok": True, "disponivel": False}

    company     = d.get("company", {})
    address     = d.get("address", {})
    status_cnpj = d.get("status", {}).get("text", "—")
    razao       = company.get("name") or "—"
    cidade      = address.get("city") or "—"
    uf          = address.get("state") or "—"
    ies         = d.get("registrations", [])

    cnpj_ok       = status_cnpj.lower() == "ativa"
    ie_pendencias = [
        {"estado": ie.get("state", ""), "status": ie.get("status", {}).get("text", "")}
        for ie in ies
        if ie.get("status", {}).get("text", "").lower() not in _STATUS_IE_OK
        and ie.get("status", {}).get("text", "")
    ]
    ie_ok = len(ie_pendencias) == 0 and not ie_indisponivel

    if ie_indisponivel:
        ie_display = "Não verificada — serviço de IE fora do ar"
        cor_ie = "#f5c518"
    else:
        ie_display = "Sem restrição" if (not ies or ie_ok) else " | ".join(
            f'{p["estado"]}: {p["status"]}' for p in ie_pendencias
        )
        cor_ie = "#28a745" if ie_ok else "#dc3545"

    bloqueio = (not cnpj_ok) or (not ie_indisponivel and not ie_ok)

    return {
        "ok": True,
        "disponivel": True,
        "razao_social": razao,
        "cidade": cidade,
        "uf": uf,
        "status_cnpj": status_cnpj,
        "cnpj_ok": cnpj_ok,
        "ie_display": ie_display,
        "ie_ok": ie_ok,
        "ie_indisponivel": ie_indisponivel,
        "cor_cnpj": "#28a745" if cnpj_ok else "#dc3545",
        "cor_ie": cor_ie,
        "bloqueio": bloqueio,
    }


def _texto_cnpj_email(cnpj_limpo: str) -> str | None:
    """Monta o bloco de texto da Receita Federal pro corpo do e-mail — uma
    consulta NOVA e independente da exibida na tela (pode falhar mesmo com a
    de exibição tendo funcionado); None se não conseguiu confirmar de jeito
    nenhum, quem chama decide não mandar o e-mail nesse caso."""
    try:
        try:
            d = _fetch_cnpj(cnpj_limpo, incluir_registros=True)
            ie_indisponivel = False
        except Exception:
            d = _fetch_cnpj(cnpj_limpo, incluir_registros=False)
            ie_indisponivel = True

        company = d.get("company", {})
        address = d.get("address", {})

        def nd(v, default="—"):
            return v if v else default

        razao   = nd(company.get("name"))
        alias   = nd(d.get("alias"), "")
        status  = nd(d.get("status", {}).get("text"))
        porte   = nd(company.get("size", {}).get("text"))
        equity  = company.get("equity")
        capital = f"R$ {float(equity):,.2f}" if equity else "—"

        logr   = nd(address.get("street"), "")
        num    = nd(address.get("number"), "")
        comp   = nd(address.get("details"), "")
        bairro = nd(address.get("district"), "")
        cidade = nd(address.get("city"), "")
        uf     = nd(address.get("state"), "")
        cep_raw = re.sub(r"\D", "", str(nd(address.get("zip"), "")))
        cep    = f"{cep_raw[:5]}-{cep_raw[5:]}" if len(cep_raw) == 8 else cep_raw
        ende   = f"{logr}, {num}"
        if comp and comp != "—":
            ende += f" — {comp}"
        ende += f", {bairro} — {cidade}/{uf} — CEP {cep}"

        phones  = ", ".join(f'({p["area"]}) {p["number"]}' for p in d.get("phones", []))
        emails  = ", ".join(e["address"] for e in d.get("emails", []))
        cnae_p  = d.get("mainActivity", {})
        cnae_txt = f'{nd(cnae_p.get("id"))} — {nd(cnae_p.get("text"))}'
        simples = "Sim" if company.get("simples", {}).get("optant") else "Não"
        mei     = "Sim" if company.get("simei", {}).get("optant") else "Não"

        ie_txt = ""
        for ie in d.get("registrations", []):
            ie_txt += (
                f'\n  Estado: {nd(ie.get("state"))}'
                f' | Nº: {nd(ie.get("number"))}'
                f' | Status: {nd(ie.get("status", {}).get("text"))}'
                f' | Tipo: {nd(ie.get("type", {}).get("text"))}'
            )

        socios_txt = ""
        for s in company.get("members", []):
            p = s.get("person", {})
            socios_txt += f'\n  {nd(p.get("name"))} — {nd(s.get("role", {}).get("text"))}'

        linhas = [
            f"Razão Social   : {razao}",
            f"Nome Fantasia  : {alias}" if alias else None,
            f"CNPJ           : {cnpj_limpo[:2]}.{cnpj_limpo[2:5]}.{cnpj_limpo[5:8]}/{cnpj_limpo[8:12]}-{cnpj_limpo[12:]}",
            f"Porte          : {porte}",
            f"Simples / MEI  : {simples} / {mei}",
            f"CNAE Principal : {cnae_txt}",
            f"Endereço       : {ende}",
            f"Telefone(s)    : {phones}" if phones else None,
            f"E-mail(s)      : {emails}" if emails else None,
            f"Insc. Estadual :{ie_txt}" if ie_txt else None,
        ]
        texto = "\n".join(l for l in linhas if l)
        if ie_indisponivel:
            texto += (
                "\n\n⚠️ Observação: a Inscrição Estadual não pôde ser verificada "
                "automaticamente no momento da solicitação (serviço do provedor fora "
                "do ar) — favor confirmar manualmente antes de aprovar o cadastro."
            )
        return texto
    except Exception as e:
        print(f"[CNPJ] {cnpj_limpo}: falha ao montar texto do e-mail: {str(e)[:200]}")
        return None


# ── Gmail do RCA solicitante (OAuth por usuário) ────────────────────────────

def _decode_email_id_token(id_token: str) -> str:
    """Mesmo decode 'cru' (sem verificar assinatura) que utils.py::_decode_email
    já usa pro login do app Streamlit — só pra exibição/From, não é usado como
    prova de identidade em nenhuma decisão de autorização."""
    try:
        payload = id_token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        return json.loads(base64.urlsafe_b64decode(payload)).get("email", "")
    except Exception:
        return ""


def _token_valido_ou_renovado(token: dict):
    """Retorna (token_atualizado_ou_None, erro_ou_None). Renova via
    refresh_token quando falta menos de 60s pra expirar — mesma margem e
    mesmo motivo de utils.py::ensure_valid_token (evita cair no 401 reativo
    da chamada de envio logo em seguida)."""
    if not token or not token.get("access_token"):
        return None, "login_google_necessario"
    if token.get("expires_at", 0) < time.time() + 60:
        refresh_token = token.get("refresh_token")
        if not refresh_token:
            return None, "sessao_expirada"
        try:
            resp = requests.post("https://oauth2.googleapis.com/token", data={
                "refresh_token": refresh_token,
                "client_id": GOOGLE_CLIENT_ID,
                "client_secret": GOOGLE_CLIENT_SECRET,
                "grant_type": "refresh_token",
            }, timeout=10)
            resp.raise_for_status()
            novo = resp.json()
        except Exception as e:
            print(f"[OAUTH] refresh falhou pra {token.get('email', '?')}: {str(e)[:300]}")
            return None, "sessao_expirada"
        # Google nunca devolve refresh_token de volta numa renovação — perder
        # isso aqui faria a PRÓXIMA expiração (~1h depois) já cair em "sem
        # permissão de renovação automática" mesmo o original ainda sendo
        # válido (mesmo bug raiz já documentado/corrigido em utils.py).
        novo["refresh_token"] = refresh_token
        novo["expires_at"] = time.time() + novo.get("expires_in", 3600)
        novo["email"] = token.get("email", "")
        return novo, None
    return token, None


def _set_cookie_gmail(resp, token: dict):
    resp.set_cookie(COOKIE_GMAIL, json.dumps(token), httponly=True, secure=True,
                     samesite="Lax", max_age=COOKIE_MAX_AGE, path="/")


def _limpar_cookie_gmail(resp):
    resp.delete_cookie(COOKIE_GMAIL, path="/")


def _enviar_email_como_rca(assunto: str, corpo: str, cc: str = None):
    """Manda pelo Gmail do RCA logado (cookie COOKIE_GMAIL) — sem fallback pra
    conta fixa (pedido do usuário em 2026-08-31: quem não logou com Google
    não consegue enviar). Retorna (ok, erro_ou_None, token_pra_regravar_ou_None)
    — quem chama decide o que fazer com o cookie (regravar se renovou,
    limpar se a sessão morreu de vez)."""
    raw_cookie = request.cookies.get(COOKIE_GMAIL)
    if not raw_cookie:
        return False, "login_google_necessario", None
    try:
        token = json.loads(raw_cookie)
    except Exception:
        return False, "login_google_necessario", None

    token, erro = _token_valido_ou_renovado(token)
    if erro:
        return False, erro, None

    remetente = token.get("email", "")
    msg = MIMEText(corpo)
    msg["Subject"] = assunto
    msg["From"] = remetente
    msg["To"] = EMAIL_FINANCEIRO
    if cc:
        msg["Cc"] = cc
    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
    try:
        resp = requests.post(
            "https://gmail.googleapis.com/gmail/v1/users/me/messages/send",
            headers={"Authorization": f"Bearer {token['access_token']}", "Content-Type": "application/json"},
            json={"raw": raw}, timeout=10,
        )
    except Exception as e:
        print(f"[EMAIL] Falha ao enviar (RCA {remetente}) '{assunto}': {str(e)[:300]}")
        return False, "falha_envio", token
    if resp.status_code == 401:
        # Token passou pela checagem de expiração mas o Gmail recusou mesmo
        # assim (revogado manualmente, relógio dessincronizado etc.) — mesmo
        # tratamento de utils.py::_enviar_email na versão Streamlit original.
        return False, "sessao_expirada", None
    if not resp.ok:
        print(f"[EMAIL] Gmail recusou (RCA {remetente}): {resp.status_code} — {resp.text[:300]}")
        return False, "falha_envio", token
    return True, None, token


_MENSAGENS_ERRO_ENVIO = {
    "login_google_necessario": "Entre com sua conta Google pra poder enviar solicitações.",
    "sessao_expirada": "Sua sessão do Google expirou — entre com o Google de novo pra enviar.",
    "falha_envio": "Falha ao enviar o e-mail. Tente novamente em instantes.",
}


def _resposta_erro_envio(erro: str, token: dict | None):
    """Monta a resposta de erro de envio e decide o que fazer com o cookie:
    limpa quando a sessão morreu de vez, regrava quando só teve um erro de
    envio pontual mas o token (possivelmente renovado) ainda é válido."""
    precisa_login = erro in ("login_google_necessario", "sessao_expirada")
    resp = jsonify({
        "ok": False,
        "motivo": _MENSAGENS_ERRO_ENVIO.get(erro, "Falha ao enviar o e-mail."),
        "precisa_login_google": precisa_login,
    })
    if precisa_login:
        _limpar_cookie_gmail(resp)
    elif token:
        _set_cookie_gmail(resp, token)
    return resp


# ── Histórico de solicitações (dedupe) ──────────────────────────────────────

def _carregar_solicitacoes() -> dict:
    if not SOLICITACOES_PATH.exists():
        return {"novo_cadastro": [], "alteracao": []}
    try:
        return json.loads(SOLICITACOES_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {"novo_cadastro": [], "alteracao": []}


def _registrar_solicitacao(tipo: str, registro: dict):
    dados = _carregar_solicitacoes()
    dados.setdefault(tipo, []).append(registro)
    tmp = f"{SOLICITACOES_PATH}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(dados, f, ensure_ascii=False, indent=2)
    os.replace(tmp, SOLICITACOES_PATH)


def _cadastro_ja_solicitado(cnpj_limpo: str):
    achados = [s for s in _carregar_solicitacoes().get("novo_cadastro", []) if s.get("cnpj") == cnpj_limpo]
    return achados[-1] if achados else None


def _alteracoes_duplicadas(codcli: int, fonte: str, preenchidos: dict) -> dict:
    historico = _carregar_solicitacoes().get("alteracao", [])
    duplicadas = {}
    for campo, valor in preenchidos.items():
        anteriores = [
            s for s in historico
            if s.get("codcli") == codcli and s.get("fonte") == fonte
            and s.get("campo") == campo
            and str(s.get("novo_valor", "")).strip().lower() == valor.strip().lower()
        ]
        if anteriores:
            duplicadas[campo] = anteriores[-1]
    return duplicadas


def _agora() -> str:
    import zoneinfo
    return datetime.now(zoneinfo.ZoneInfo("America/Sao_Paulo")).strftime("%d/%m/%Y %H:%M")


def _saudacao() -> str:
    import zoneinfo
    hora = datetime.now(zoneinfo.ZoneInfo("America/Sao_Paulo")).hour
    return "Bom dia" if hora < 12 else ("Boa tarde" if hora < 18 else "Boa noite")


# ── Rotas — OAuth Google (Gmail do RCA) ─────────────────────────────────────

@bp.route("/oauth/login")
def oauth_login():
    voltar = request.args.get("voltar", "/credito_cadastro.html")
    state = base64.urlsafe_b64encode(voltar.encode("utf-8")).decode()
    params = {
        "client_id": GOOGLE_CLIENT_ID,
        "redirect_uri": OAUTH_REDIRECT_URI,
        "response_type": "code",
        "scope": OAUTH_SCOPE,
        "access_type": "offline",
        # "consent" força a tela de consentimento mesmo pra quem já autorizou
        # antes — sem isso o Google só devolve refresh_token na PRIMEIRA
        # autorização, e sem refresh_token a sessão morre em ~1h sem como
        # renovar (mesmo motivo documentado em utils.py::_show_login).
        "prompt": "select_account consent",
        "state": state,
    }
    return redirect("https://accounts.google.com/o/oauth2/v2/auth?" + urlencode(params))


@bp.route("/oauth/callback")
def oauth_callback():
    code  = request.args.get("code")
    state = request.args.get("state", "")
    try:
        voltar = base64.urlsafe_b64decode(state.encode("utf-8")).decode("utf-8")
    except Exception:
        voltar = "/credito_cadastro.html"
    if not voltar.startswith("/"):
        voltar = "/credito_cadastro.html"

    if not code:
        return redirect(voltar + ("&" if "?" in voltar else "?") + "google=erro")

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
        return redirect(voltar + ("&" if "?" in voltar else "?") + "google=erro")

    token["expires_at"] = time.time() + token.get("expires_in", 3600)
    token["email"] = _decode_email_id_token(token.get("id_token", ""))

    destino = redirect(voltar + ("&" if "?" in voltar else "?") + "google=ok")
    _set_cookie_gmail(destino, token)
    return destino


@bp.route("/oauth/status")
def oauth_status():
    raw_cookie = request.cookies.get(COOKIE_GMAIL)
    if not raw_cookie:
        return {"logado": False}
    try:
        token = json.loads(raw_cookie)
    except Exception:
        return {"logado": False}
    # Só informativo (pra exibir "enviando como fulano@..." na tela) — não
    # renova nem valida contra o Google aqui; a renovação de verdade só
    # acontece na hora de enviar (_enviar_email_como_rca).
    return {"logado": bool(token.get("access_token")), "email": token.get("email", "")}


@bp.route("/oauth/logout", methods=["POST"])
def oauth_logout():
    resp = jsonify({"ok": True})
    _limpar_cookie_gmail(resp)
    return resp


# ── Rotas ─────────────────────────────────────────────────────────────────────

@bp.route("/rca")
def rca():
    codusur = request.args.get("codusur", "")
    if not codusur.isdigit():
        return {"ok": False, "motivo": "Código inválido."}, 400
    try:
        df = _buscar_rca(int(codusur))
    except Exception as e:
        return {"ok": False, "motivo": f"Não foi possível consultar agora ({str(e)[:150]})."}, 200
    if df.empty:
        return {"ok": False, "motivo": "RCA não encontrado."}, 200
    return {"ok": True, "nome": str(df.iloc[0]["NOME"]).strip()}


@bp.route("/cliente")
def cliente():
    busca = request.args.get("q", "")
    if not busca.strip():
        return {"clientes": []}
    df = _buscar_cliente(busca)
    if df.empty:
        return {"clientes": []}
    clientes = []
    for _, r in df.iterrows():
        clientes.append({
            "codcli": int(r["CODCLI"]),
            "nome": str(r["NOME"] or "").strip(),
            "cnpj": re.sub(r"\D", "", str(r["CNPJ"] or "")),
            "bloqueio": str(r["BLOQUEIO"] or "").strip().upper() == "S",
            "codusur1": int(r["CODUSUR1"]) if pd.notna(r["CODUSUR1"]) else None,
            "nome_rca1": str(r["NOME_RCA1"] or "").strip() if pd.notna(r["NOME_RCA1"]) else "—",
            "dtultcomp": r["DTULTCOMP"] if pd.notna(r["DTULTCOMP"]) else None,
            "dias_sem_compra": int(r["DIAS_SEM_COMPRA"]) if pd.notna(r["DIAS_SEM_COMPRA"]) else None,
            "limcred": float(r["LIMCRED"]),
            "valor_aberto": float(r["VALOR_ABERTO"]),
            "valor_pedidos": float(r["VALOR_PEDIDOS"]),
            "fonte": r["FONTE"],
        })
    return {"clientes": clientes}


@bp.route("/cnpj")
def cnpj():
    cnpj_limpo = re.sub(r"\D", "", request.args.get("cnpj", ""))
    if len(cnpj_limpo) != 14:
        return {"ok": False, "motivo": "CNPJ precisa ter 14 dígitos."}, 400
    if not CHAVE_API_CNPJ:
        return {"ok": False, "motivo": "CHAVE_API_CNPJ não configurada no servidor."}, 200
    return _consultar_cnpj(cnpj_limpo)


@bp.route("/cadastro", methods=["POST"])
def cadastro():
    dados = request.get_json(silent=True) or {}
    cnpj_limpo   = re.sub(r"\D", "", str(dados.get("cnpj", "")))
    rca_nome     = str(dados.get("rca_nome", "")).strip()[:100]
    rca_codusur  = str(dados.get("rca_codusur", "")).strip()[:20]
    forcar       = bool(dados.get("forcar"))

    if len(cnpj_limpo) != 14 or not rca_nome or not rca_codusur:
        return {"ok": False, "motivo": "Dados incompletos."}, 400

    duplicata = _cadastro_ja_solicitado(cnpj_limpo)
    if duplicata and not forcar:
        return {"ok": False, "duplicata": duplicata}

    dados_receita = _texto_cnpj_email(cnpj_limpo)
    if not dados_receita:
        return {"ok": False, "motivo": "Não foi possível confirmar o CNPJ na Receita Federal agora — e-mail NÃO enviado por segurança. Tente novamente."}

    corpo = (
        f"{_saudacao()},\n\nSolicito o cadastramento do cliente:\n\n"
        f"RCA Solicitante  : {rca_nome} (cód. {rca_codusur})\n\n"
        f"Dados da Receita Federal:\n\n{dados_receita}\n\n"
        f"Podem realizar o cadastro?\n\nObrigado!"
    )
    ok, erro, token = _enviar_email_como_rca("Solicitação de Cadastro de Cliente", corpo, cc=EMAIL_CADASTRO_CC)
    if not ok:
        return _resposta_erro_envio(erro, token)
    if token:
        resp = jsonify({"ok": True})
        _set_cookie_gmail(resp, token)
    else:
        resp = jsonify({"ok": True})

    _registrar_solicitacao("novo_cadastro", {
        "cnpj": cnpj_limpo,
        "data": _agora(),
        "rca": f"{rca_nome} (cód. {rca_codusur})",
    })
    return resp


@bp.route("/alteracao", methods=["POST"])
def alteracao():
    dados = request.get_json(silent=True) or {}
    try:
        codcli = int(dados.get("codcli"))
    except (TypeError, ValueError):
        return {"ok": False, "motivo": "Cliente inválido."}, 400
    fonte        = str(dados.get("fonte", "")).strip()
    nome_cliente = str(dados.get("nome_cliente", "")).strip()
    cnpj_fmt     = str(dados.get("cnpj", "")).strip()
    rca_nome     = str(dados.get("rca_nome", "")).strip()[:100]
    rca_codusur  = str(dados.get("rca_codusur", "")).strip()[:20]
    forcar       = bool(dados.get("forcar"))
    campos       = dados.get("campos") or {}
    preenchidos  = {c: str(v).strip() for c, v in campos.items() if str(v).strip()}

    if not fonte or not preenchidos or not rca_nome or not rca_codusur:
        return {"ok": False, "motivo": "Dados incompletos."}, 400

    duplicadas = _alteracoes_duplicadas(codcli, fonte, preenchidos)
    if duplicadas and not forcar:
        return {"ok": False, "duplicadas": duplicadas}

    linhas_campos = "\n".join(f"{c}: {v}" for c, v in preenchidos.items())
    corpo = (
        f"Solicito a atualização de cadastro do cliente:\n\n"
        f"Cliente: {nome_cliente} ({cnpj_fmt}) — Cód. {codcli}\n"
        f"RCA Solicitante: {rca_nome} (cód. {rca_codusur})\n\n"
        f"Campos a alterar:\n{linhas_campos}\n\n"
        f"Podem realizar a atualização?\n\nObrigado!"
    )
    ok, erro, token = _enviar_email_como_rca(f"Solicitação de Alteração de Cadastro — {nome_cliente}", corpo, cc=EMAIL_CADASTRO_CC)
    if not ok:
        return _resposta_erro_envio(erro, token)
    if token:
        resp = jsonify({"ok": True})
        _set_cookie_gmail(resp, token)
    else:
        resp = jsonify({"ok": True})

    agora = _agora()
    for campo, valor in preenchidos.items():
        _registrar_solicitacao("alteracao", {
            "codcli": codcli,
            "fonte": fonte,
            "campo": campo,
            "novo_valor": valor,
            "data": agora,
            "rca": f"{rca_nome} (cód. {rca_codusur})",
        })
    return resp


app.register_blueprint(bp)

if __name__ == "__main__":
    debug = RUNTIME != "vps"
    host = "127.0.0.1" if RUNTIME == "vps" else "0.0.0.0"
    app.run(host=host, port=5055, debug=debug, threaded=True)
