"""Utilitários compartilhados: CSS, auth Google, conexão Oracle."""
import os, base64, json, time
from datetime import datetime

import streamlit as st
import oracledb
import pandas as pd
from dotenv import load_dotenv
from streamlit_oauth import OAuth2Component
from streamlit_cookies_controller import CookieController

load_dotenv()

# ── Configuração ──────────────────────────────────────────────────────────────

CLIENT_ID     = os.getenv("GOOGLE_CLIENT_ID", "")
CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET", "")
REDIRECT_URI  = "https://offtrade.duckdns.org/"
AUTHORIZE_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL     = "https://oauth2.googleapis.com/token"

# Mantida em sincronia manual com EMAILS_OK em login.html — as duas listas
# ficaram divergentes por um tempo e bloqueavam gestores autorizados no site
# estático quando entravam no Streamlit (confirmado em 2026-07-21).
EMAILS_ADMIN = {
    "danielle.soares@rigarr.com.br",
    "allan.correa@rigarr.com.br",
    "leonardo.campos@rigarr.com.br",
    "alexsandro.nunes@rigarr.com.br",
    "giovani.cabral@rigarr.com.br",
    "kaliel.caro@rigarr.com.br",
    "artur.furlan@rigarr.com.br",
    "daniel.diniz@rigarr.com.br",
    "marcus.tanamachi@rigarr.com.br",
    "geovanna.lescano@rigarr.com.br",
    "fernando.risson@rigarr.com.br",
    "erocles.oliveira@rigarr.com.br",
    "andre.massensini@rigarr.com.br",
    "priscilla.zambrano@rigarr.com.br",
    "anderson.canaveis@rigarr.com.br",
}

ORACLE_LIB = os.getenv("ORACLE_LIB", "/opt/oracle/instantclient_21_1")

DSN = {
    "crc":     os.getenv("DSN_CRC", "crc_oci"),
    "theking": os.getenv("DSN_TK",  "theking_oci"),
    "sp":      os.getenv("DNS_SP",  "spon_oci"),
    "mg":      os.getenv("DNS_MG",  "mgon_oci"),
}

_oracle_ready = False


# ── Git (deploy dos exportadores) ───────────────────────────────────────────────

def git_commit_push(files: list[str], mensagem: str) -> None:
    """Commita e envia os arquivos ao GitHub Pages. Não faz nada quando o
    diretório não é um repositório git — é o caso normal na VPS, que roda a
    partir de /opt/offtrade-pipeline, um diretório sincronizado sob demanda
    por deploy_pipeline_vps.py, não um clone git (ver GMAIL_AUTOMACAO.md /
    memória "vps-pipeline-independente" para o porquê). Sem essa checagem,
    todo exportador que roda na VPS falhava com "git add" (exit 128),
    marcando o passo inteiro como falho no log mesmo quando o dado foi
    gerado corretamente."""
    import subprocess
    from pathlib import Path

    repo_dir = Path(__file__).parent
    if not (repo_dir / ".git").exists():
        return
    try:
        subprocess.run(["git", "-C", str(repo_dir), "add", *files], check=True)
        subprocess.run(["git", "-C", str(repo_dir), "commit", "-m", mensagem])
        subprocess.run(["git", "-C", str(repo_dir), "push", "origin", "master"], check=True)
        print("OK GitHub Pages atualizado.")
    except subprocess.CalledProcessError as e:
        print(f"[AVISO] git commit/push falhou — ignorado: {e}")


# ── Oracle ────────────────────────────────────────────────────────────────────

def _init_oracle():
    global _oracle_ready
    if not _oracle_ready:
        oracledb.init_oracle_client(lib_dir=ORACLE_LIB)
        _oracle_ready = True


def get_conn(db: str = "crc"):
    _init_oracle()
    if db == "crc":
        user = os.getenv("CRC_USER", os.getenv("VPN_USER", "vpn"))
        password = os.getenv("CRC_PASSWORD", os.getenv("VPN_PASSWORD", ""))
    else:
        user = os.getenv("VPN_USER", "vpn")
        password = os.getenv("VPN_PASSWORD", "")
    return oracledb.connect(user=user, password=password, dsn=DSN[db])


@st.cache_data(ttl=300)
def sql(query: str, db: str = "crc", params=None) -> pd.DataFrame:
    with get_conn(db) as conn:
        df = pd.read_sql(query, conn, params=params)
        df.columns = df.columns.str.upper()
        return df


# ── Auth ──────────────────────────────────────────────────────────────────────

def _decode_email(token: dict) -> str:
    try:
        payload = token.get("id_token", "").split(".")[1]
        payload += "=" * (-len(payload) % 4)
        return json.loads(base64.urlsafe_b64decode(payload)).get("email", "")
    except Exception:
        return ""


def require_auth() -> dict:
    """Garante autenticação. Retorna rca_info. Para execução se não autenticado."""
    cookies = CookieController()

    if "token" not in st.session_state:
        # O CookieController devolve `default={}` (não None) enquanto o
        # round-trip assíncrono JS->Python não completou — "offtrade_token"
        # ausente de um dict vazio é indistinguível de "usuário sem cookie"
        # na primeira renderização. Sem retry, a página de SSO (cookie setado
        # por login.html) nunca via o cookie e sempre caía no login.
        all_cookies = cookies.getAll() or {}
        if "offtrade_token" not in all_cookies:
            tentativas = st.session_state.get("_cookie_sync_tentativas", 0)
            if tentativas < 6:
                st.session_state["_cookie_sync_tentativas"] = tentativas + 1
                time.sleep(0.3)
                st.rerun()
        saved = all_cookies.get("offtrade_token")
        if saved:
            # O componente já desserializa cookies com conteúdo JSON de volta
            # para dict/list automaticamente — "saved" vem como dict, não
            # string. Um json.loads(dict) levanta TypeError, que o antigo
            # "except Exception: pass" engolia silenciosamente e fazia o SSO
            # nunca completar (sempre caía na tela de login mesmo com o
            # cookie presente e correto no navegador).
            try:
                st.session_state["token"] = saved if isinstance(saved, dict) else json.loads(saved)
            except Exception:
                pass

    if "token" not in st.session_state:
        _show_login(cookies)

    if "rca_info" not in st.session_state:
        _resolve_rca()

    rca = st.session_state.get("rca_info", {})
    if rca.get("bloqueado"):
        st.error(f"Acesso não autorizado. E-mail: `{rca.get('email', '?')}` | Erro: `{rca.get('erro', '—')}`")
        st.stop()

    return rca


def _show_login(cookies):
    oauth2 = OAuth2Component(CLIENT_ID, CLIENT_SECRET, AUTHORIZE_URL, TOKEN_URL, TOKEN_URL)
    st.markdown("""
<div style="max-width:360px;margin:80px auto;text-align:center">
    <div style="font-size:1.6rem;font-weight:800;color:#f5c518;margin-bottom:6px">OfftradeHub</div>
    <div style="font-size:.85rem;color:#94a3b8;margin-bottom:32px">Dashboard Comercial · Off Trade</div>
</div>
""".strip(), unsafe_allow_html=True)
    try:
        result = oauth2.authorize_button(
            "Entrar com Google",
            redirect_uri=REDIRECT_URI,
            scope="openid email profile https://www.googleapis.com/auth/gmail.send",
            key="google_login",
            # access_type=offline + prompt=consent: sem isso o Google não
            # devolve refresh_token, o access_token expira em ~1h e some sem
            # como renovar — Credito_e_Cadastro.py (envio de e-mail com o
            # token da própria pessoa) caía em 401 depois de uma sessão aberta
            # por mais de 1h (confirmado em 2026-08-04). "consent" força a
            # tela de consentimento de novo mesmo pra quem já autorizou antes,
            # senão só entra em vigor pra login novo.
            extras_params={"prompt": "select_account consent", "access_type": "offline"},
        )
    except Exception:
        st.query_params.clear()
        st.rerun()
    if result and "token" in result:
        st.session_state["token"] = result["token"]
        cookies.set("offtrade_token", json.dumps(result["token"]))
        st.rerun()
    st.stop()


def ensure_valid_token() -> dict:
    """Garante um access_token válido em st.session_state["token"], renovando
    via refresh_token se tiver expirado. Levanta RuntimeError (sem
    refresh_token disponível — login de antes do access_type=offline entrar
    em vigor) quando só reautenticação manual resolve; quem chama decide como
    avisar o usuário."""
    token = st.session_state.get("token") or {}
    if not token:
        raise RuntimeError("Sessão não autenticada.")
    if token.get("expires_at") and token["expires_at"] < time.time():
        if not token.get("refresh_token"):
            raise RuntimeError("Sessão expirada — atualize a página e faça login de novo.")
        oauth2 = OAuth2Component(CLIENT_ID, CLIENT_SECRET, AUTHORIZE_URL, TOKEN_URL, TOKEN_URL)
        token = oauth2.refresh_token(token)
        st.session_state["token"] = token
        try:
            CookieController().set("offtrade_token", json.dumps(token))
        except Exception:
            pass
    return token


def _resolve_rca():
    email = _decode_email(st.session_state["token"])
    if email.lower() in EMAILS_ADMIN:
        st.session_state["rca_info"] = {
            "email": email,
            "nome": email.split("@")[0].replace(".", " ").title(),
            "admin": True,
        }
        return
    try:
        df = sql(
            "SELECT CODUSUR, NOME FROM CRC.PCUSUARI "
            "WHERE UPPER(TRIM(EMAIL)) = UPPER(:1) AND ROWNUM = 1",
            params=[email.strip()],
        )
        if not df.empty:
            st.session_state["rca_info"] = {
                "codusur": int(df.iloc[0]["CODUSUR"]),
                "nome": str(df.iloc[0]["NOME"]).strip(),
                "email": email,
                "admin": False,
            }
        else:
            st.session_state["rca_info"] = {"email": email, "bloqueado": True}
    except Exception as e:
        st.session_state["rca_info"] = {"email": email, "bloqueado": True, "erro": str(e)}


# ── CSS ───────────────────────────────────────────────────────────────────────

_CSS = """
<style>
/* Layout */
.block-container { padding: 1.5rem 2rem !important; max-width: 1020px !important; }

/* Oculta chrome do Streamlit */
#MainMenu, footer { visibility: hidden; }
[data-testid="stDecoration"] { display: none; }
[data-testid="stMainMenu"] { visibility: hidden; }
header[data-testid="stHeader"] { visibility: hidden; height: 0; min-height: 0; }

/* Design tokens — espelham os HTML existentes */
:root {
    --bg:     #0f1117;
    --card:   #1a1d27;
    --card2:  #22263a;
    --accent: #f5c518;
    --green:  #22c55e;
    --yellow: #eab308;
    --red:    #ef4444;
    --text:   #e2e8f0;
    --muted:  #94a3b8;
    --border: #2d3144;
}

/* Header de página */
.page-header {
    display: flex; align-items: center; justify-content: space-between;
    margin-bottom: 24px; padding-bottom: 16px; border-bottom: 1px solid var(--border);
    flex-wrap: wrap; gap: 12px;
}
.header-brand { font-size: 1.25rem; font-weight: 800; color: var(--accent); letter-spacing: -.02em; }
.header-sub   { font-size: .75rem; color: var(--muted); margin-top: 2px; }
.header-date  { font-size: .78rem; color: var(--muted); }

/* Títulos de seção */
.section-title {
    font-size: .65rem; text-transform: uppercase; letter-spacing: .1em;
    color: var(--muted); margin: 24px 0 14px;
    padding-bottom: 8px; border-bottom: 1px solid var(--border);
}

/* Card genérico */
.card {
    background: var(--card); border: 1px solid var(--border); border-radius: 12px;
    padding: 18px; transition: border-color .2s, transform .15s;
}
.card:hover { transform: translateY(-2px); border-color: var(--accent); }

/* Cards de campanha */
.camp-card {
    background: var(--card); border: 1px solid var(--border);
    border-left: 4px solid var(--border); border-radius: 12px;
    padding: 16px 18px; display: block; color: var(--text);
    text-decoration: none; transition: .2s; margin-bottom: 0;
}
.camp-card:hover { transform: translateY(-2px); }
.camp-card.amarula { border-left-color: #d4962a; }
.camp-card.amarula:hover { border-color: #d4962a; }
.camp-card.pernod  { border-left-color: #3b82f6; }
.camp-card.pernod:hover  { border-color: #3b82f6; }
.camp-card.moving  { border-left-color: #10b981; }
.camp-card.moving:hover  { border-color: #10b981; }
.camp-card-top { display: flex; justify-content: space-between; align-items: flex-start; gap: 8px; }
.camp-name     { font-size: .95rem; font-weight: 700; }
.camp-status   { font-size: .6rem; font-weight: 800; text-transform: uppercase;
                 letter-spacing: .08em; padding: 2px 8px; border-radius: 20px; white-space: nowrap; }
.status-ativa  { background: rgba(34,197,94,.15); color: var(--green); }
.status-enc    { background: rgba(148,163,184,.1); color: var(--muted); }
.camp-periodo  { font-size: .75rem; color: var(--muted); margin-top: 4px; }
.camp-scope    { font-size: .72rem; color: var(--muted); }
.camp-premios  { font-size: .75rem; color: var(--text); line-height: 1.7; margin: 8px 0 4px; }
.camp-link     { font-size: .78rem; font-weight: 700; color: var(--accent); margin-top: 6px; display: block; }

/* Cards de dashboard (links) */
.dash-card {
    background: var(--card); border: 1px solid var(--border); border-radius: 12px;
    padding: 14px 16px; display: flex; align-items: center; gap: 14px;
    color: var(--text); text-decoration: none; transition: .2s; height: 100%;
}
.dash-card:hover { border-color: var(--accent); transform: translateY(-2px); }
.dash-icon { font-size: 1.4rem; flex-shrink: 0; }
.dash-name { font-size: .9rem; font-weight: 700; }
.dash-desc { font-size: .72rem; color: var(--muted); margin-top: 2px; }

/* Métricas */
.metric-card {
    background: var(--card); border: 1px solid var(--border);
    border-radius: 10px; padding: 14px 16px; text-align: center;
}
.metric-label { font-size: .7rem; color: var(--muted); text-transform: uppercase; letter-spacing: .05em; }
.metric-value { font-size: 1.15rem; font-weight: 700; color: var(--text); margin-top: 4px; }
.metric-value.green  { color: var(--green); }
.metric-value.red    { color: var(--red); }
.metric-value.yellow { color: var(--yellow); }

/* Barra de progresso */
.bar-bg   { background: var(--card2); border-radius: 99px; height: 5px; overflow: hidden; margin: 4px 0 8px; }
.bar-fill { height: 100%; border-radius: 99px; }
.bar-green  { background: var(--green); }
.bar-yellow { background: var(--yellow); }
.bar-red    { background: var(--red); }

/* Badges */
.badge { display: inline-block; padding: 2px 10px; border-radius: 20px; font-size: .7rem; font-weight: 700; }
.badge-ok   { background: rgba(34,197,94,.15); color: var(--green); }
.badge-blq  { background: rgba(239,68,68,.15); color: var(--red); }
.badge-warn { background: rgba(234,179,8,.15); color: var(--yellow); }

/* Cards de vendedor */
.vendor-card {
    background: var(--card); border: 1px solid var(--border); border-radius: 12px;
    padding: 18px; margin-bottom: 12px;
}
.vendor-name { font-size: .95rem; font-weight: 600; }
.vendor-rca  { font-size: .72rem; color: var(--muted); margin-bottom: 12px; }

.stat-row {
    display: flex; justify-content: space-between; align-items: center;
    padding: 5px 0; border-bottom: 1px solid var(--border); font-size: .8rem;
}
.stat-row:last-child { border-bottom: none; }
.stat-label { color: var(--muted); }
.stat-val   { font-weight: 600; }

/* Tabela customizada */
.custom-table { width: 100%; border-collapse: collapse; font-size: .82rem; }
.custom-table th {
    color: var(--muted); font-weight: 600; text-align: left;
    padding: 6px 8px; border-bottom: 1px solid var(--border);
}
.custom-table td { padding: 6px 8px; border-bottom: 1px solid var(--border); }
.custom-table tr:last-child td { border-bottom: none; }
.custom-table .td-right { text-align: right; white-space: nowrap; }
</style>
"""


def inject_css():
    st.markdown(_CSS, unsafe_allow_html=True)


# ── Helpers de UI ─────────────────────────────────────────────────────────────

def page_header(title: str, subtitle: str = ""):
    hoje = datetime.now().strftime("%d/%m/%Y")
    sub_html = f'<div class="header-sub">{subtitle}</div>' if subtitle else ""
    st.markdown(f"""
<div class="page-header">
    <div>
        <div class="header-brand">{title}</div>
        {sub_html}
    </div>
    <div class="header-date">{hoje}</div>
</div>
""".strip(), unsafe_allow_html=True)


def section_title(label: str):
    st.markdown(f'<div class="section-title">{label}</div>', unsafe_allow_html=True)


def metric_card(label: str, value: str, color: str = ""):
    st.markdown(f"""
<div class="metric-card">
    <div class="metric-label">{label}</div>
    <div class="metric-value {color}">{value}</div>
</div>
""".strip(), unsafe_allow_html=True)


def progress_bar(pct: float) -> str:
    pct = min(max(pct, 0), 150)
    color = "green" if pct >= 80 else ("yellow" if pct >= 50 else "red")
    bar_w = min(pct, 100)
    return (
        f'<div class="bar-bg">'
        f'<div class="bar-fill bar-{color}" style="width:{bar_w:.1f}%"></div>'
        f'</div>'
    )


def pct_badge(pct: float) -> str:
    color = "green" if pct >= 80 else ("yellow" if pct >= 50 else "red")
    cls   = "badge-ok" if pct >= 80 else ("badge-warn" if pct >= 50 else "badge-blq")
    return f'<span class="badge {cls}">{pct:.0f}%</span>'


def fmt_brl(v: float) -> str:
    return f"R$ {v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
