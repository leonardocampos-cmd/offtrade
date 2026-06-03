import os
import streamlit as st
import oracledb
import pandas as pd
import requests
import re
import urllib3
from email.mime.text import MIMEText
from streamlit_oauth import OAuth2Component
from streamlit_cookies_controller import CookieController
import base64, json as _json

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

st.set_page_config(layout="wide")

CLIENT_ID     = os.environ.get("GOOGLE_CLIENT_ID", "")
CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET", "")
REDIRECT_URI  = "https://offtrade.duckdns.org/"
AUTHORIZE_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL     = "https://oauth2.googleapis.com/token"

oauth2  = OAuth2Component(CLIENT_ID, CLIENT_SECRET, AUTHORIZE_URL, TOKEN_URL, TOKEN_URL)
cookies = CookieController()

def _decode_email(token: dict) -> str:
    try:
        payload = token.get("id_token", "").split(".")[1]
        payload += "=" * (-len(payload) % 4)
        return _json.loads(base64.urlsafe_b64decode(payload)).get("email", "")
    except Exception:
        return ""

if "token" not in st.session_state:
    saved = cookies.get("offtrade_token")
    if saved:
        try:
            st.session_state["token"] = _json.loads(saved)
        except Exception:
            pass

if "token" not in st.session_state:
    try:
        result = oauth2.authorize_button(
            "Entrar com Google",
            redirect_uri=REDIRECT_URI,
            scope="openid email profile https://www.googleapis.com/auth/gmail.send",
            key="google_login",
            extras_params={"prompt": "select_account"},
            pkce="S256",
        )
    except Exception:
        st.query_params.clear()
        st.rerun()
    if result and "token" in result:
        st.session_state["token"] = result["token"]
        cookies.set("offtrade_token", _json.dumps(result["token"]))
        st.rerun()
    else:
        st.stop()

st.markdown("""
<style>
    .block-container { padding: 2rem 3rem; }
    .card { background: #1e1e2e; border-radius: 12px; padding: 1.2rem 1.8rem; margin-bottom: 1rem; }
    .nome { font-size: 1.4rem; font-weight: 700; color: #ffffff; }
    .sub  { font-size: 0.85rem; color: #aaaaaa; margin-top: 2px; }
    .badge-ok  { background: #1a4731; color: #4ade80; padding: 4px 14px; border-radius: 20px; font-size: 0.8rem; font-weight: 600; }
    .badge-blq { background: #4a1a1a; color: #f87171; padding: 4px 14px; border-radius: 20px; font-size: 0.8rem; font-weight: 600; }
    .metric-card { background: #2a2a3e; border-radius: 10px; padding: 0.9rem 1.2rem; text-align: center; }
    .metric-label { font-size: 0.72rem; color: #888; text-transform: uppercase; letter-spacing: 0.05em; }
    .metric-value { font-size: 1.15rem; font-weight: 700; color: #e2e2e2; margin-top: 4px; }
    .metric-value.green { color: #4ade80; }
    .metric-value.red   { color: #f87171; }
</style>
""", unsafe_allow_html=True)

oracledb.init_oracle_client()

DB_USER     = "vpn"
DB_PASSWORD = os.environ.get("DB_PASSWORD", "")
DB_DSN      = os.environ.get("DB_DSN", "")
SCHEMA      = "CRC"

EVOLUTION_API_URL   = os.environ.get("EVOLUTION_API_URL", "")
EVOLUTION_API_KEY   = os.environ.get("EVOLUTION_API_KEY", "")
EVOLUTION_INSTANCE  = "bees"
WHATSAPP_FINANCEIRO = "5521964384318"

EMAIL_FINANCEIRO = "cadastro@rigarr.com.br"
CHAVE_API_CNPJ   = os.environ.get("CHAVE_API_CNPJ", "")

def get_connection():
    return oracledb.connect(user=DB_USER, password=DB_PASSWORD, dsn=DB_DSN)

EMAILS_ADMIN = {
    "alexsandro.nunes@rigarr.com.br",
    "giovani.cabral@rigarr.com.br",
}

if "rca_info" not in st.session_state:
    email_login = _decode_email(st.session_state["token"])
    if email_login:
        if email_login.lower() in EMAILS_ADMIN:
            st.session_state["rca_info"] = {"email": email_login, "nome": email_login.split("@")[0].replace(".", " ").title()}
        else:
            try:
                with get_connection() as _conn:
                    _df = pd.read_sql(
                        f"SELECT CODUSUR, NOME FROM {SCHEMA}.PCUSUARI WHERE UPPER(TRIM(EMAIL)) = UPPER(:1) AND ROWNUM = 1",
                        _conn, params=[email_login.strip()]
                    )
                if not _df.empty:
                    st.session_state["rca_info"] = {
                        "codusur": int(_df.iloc[0]["CODUSUR"]),
                        "nome": str(_df.iloc[0]["NOME"]).strip(),
                        "email": email_login,
                    }
                else:
                    st.session_state["rca_info"] = {"email": email_login, "bloqueado": True}
            except Exception as _e:
                st.session_state["rca_info"] = {"email": email_login, "bloqueado": True, "erro": str(_e)}
    else:
        st.session_state["rca_info"] = {"bloqueado": True}

rca_info = st.session_state.get("rca_info", {})
if rca_info.get("bloqueado"):
    st.error("Acesso não autorizado. Entre em contato com a área de Análise Off Trade para liberar seu acesso.")
    st.caption(f"E-mail: `{rca_info.get('email', 'não identificado')}` | Erro: `{rca_info.get('erro', '—')}`")
    st.stop()

_cod = rca_info.get('codusur')
st.success(f"Bem-vindo, {rca_info['nome']}" + (f" (RCA {_cod})" if _cod else "") + "!")

def enviar_whatsapp(mensagem: str):
    if not EVOLUTION_API_URL:
        return
    try:
        requests.post(
            f"{EVOLUTION_API_URL}/message/sendText/{EVOLUTION_INSTANCE}",
            headers={"apikey": EVOLUTION_API_KEY, "Content-Type": "application/json"},
            json={"number": WHATSAPP_FINANCEIRO, "textMessage": {"text": mensagem}},
            timeout=5,
        )
    except Exception:
        pass

def enviar_email(assunto: str, corpo: str):
    try:
        token        = st.session_state.get("token", {})
        access_token = token.get("access_token", "")
        rca          = st.session_state.get("rca_info", {})
        remetente    = rca.get("email", "")

        msg = MIMEText(corpo)
        msg["Subject"] = assunto
        msg["From"]    = remetente
        msg["To"]      = EMAIL_FINANCEIRO

        raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
        r = requests.post(
            "https://gmail.googleapis.com/gmail/v1/users/me/messages/send",
            headers={"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"},
            json={"raw": raw},
            timeout=10,
        )
        if not r.ok:
            st.warning(f"Erro ao enviar e-mail: {r.status_code} — {r.text[:300]}")
    except Exception as _e:
        st.warning(f"Erro ao enviar e-mail: {_e}")

def notificar(assunto: str, corpo: str):
    enviar_whatsapp(f"{assunto}\n\n{corpo}")
    enviar_email(assunto, corpo)

def notificar_cadastro(assunto: str, corpo: str):
    enviar_email(assunto, corpo)

def _fetch_cnpj(cnpj_limpo: str) -> dict:
    url  = f"https://api.cnpja.com/office/{cnpj_limpo}?simples=true&registrations=BR"
    resp = requests.get(url, headers={"Authorization": CHAVE_API_CNPJ}, verify=False, timeout=15)
    resp.raise_for_status()
    return resp.json()

def consultar_cnpj_receita(cnpj: str) -> str:
    try:
        cnpj_limpo = re.sub(r"\D", "", cnpj)
        if len(cnpj_limpo) != 14:
            return ""
        d = _fetch_cnpj(cnpj_limpo)

        company = d.get("company", {})
        address = d.get("address", {})

        def nd(v, default="—"):
            return v if v else default

        razao    = nd(company.get("name"))
        alias    = nd(d.get("alias"), "")
        status   = nd(d.get("status", {}).get("text"))
        porte    = nd(company.get("size", {}).get("text"))
        equity   = company.get("equity")
        capital  = f"R$ {float(equity):,.2f}" if equity else "—"

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

        phones   = ", ".join(f'({p["area"]}) {p["number"]}' for p in d.get("phones", []))
        emails   = ", ".join(e["address"] for e in d.get("emails", []))
        cnae_p   = d.get("mainActivity", {})
        cnae_txt = f'{nd(cnae_p.get("id"))} — {nd(cnae_p.get("text"))}'
        simples  = "Sim" if company.get("simples", {}).get("optant") else "Não"
        mei      = "Sim" if company.get("simei", {}).get("optant") else "Não"

        ies     = d.get("registrations", [])
        ie_txt  = ""
        for ie in ies:
            ie_txt += (
                f'\n  Estado: {nd(ie.get("state"))}'
                f' | Nº: {nd(ie.get("number"))}'
                f' | Status: {nd(ie.get("status", {}).get("text"))}'
                f' | Tipo: {nd(ie.get("type", {}).get("text"))}'
            )

        socios     = company.get("members", [])
        socios_txt = ""
        for s in socios:
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
            (f"Insc. Estadual :{ie_txt}") if ie_txt else None,
        ]
        return "\n".join(l for l in linhas if l)
    except Exception as _e:
        return f"ERRO: {_e}"

@st.cache_data(ttl=600)
def buscar_rca(codusur: int):
    with get_connection() as conn:
        df = pd.read_sql(
            f"SELECT CODUSUR, NOME, TELEFONE1, EMAIL FROM {SCHEMA}.PCUSUARI WHERE CODUSUR = :1",
            conn, params=[codusur]
        )
    return df

@st.cache_data(ttl=300)
def buscar_cliente(busca: str):
    busca      = busca.strip()
    cnpj       = busca.replace(".", "").replace("/", "").replace("-", "")

    if cnpj.isdigit() and len(cnpj) == 14:
        filtro = f"REPLACE(REPLACE(REPLACE(c.cgcent,'.',''),'/',''),'-','') = '{cnpj}'"
    elif busca.isdigit():
        filtro = f"c.codcli = {int(busca)}"
    else:
        nome_upper = busca.upper().replace("'", "''")
        filtro = f"UPPER(c.cliente) LIKE '%{nome_upper}%'"

    query = f"""
        SELECT
            c.codcli,
            c.cliente AS nome,
            c.cgcent AS cnpj,
            c.bloqueio,
            c.codusur1,
            u.nome AS nome_rca1,
            TO_NUMBER(NVL(c.limcred, 0)) AS limcred,
            TO_NUMBER(NVL(prest.valor, 0)) AS valor_aberto,
            TO_NUMBER(NVL(ped.vlatend, 0)) AS valor_pedidos
        FROM {SCHEMA}.PCCLIENT c
        LEFT JOIN {SCHEMA}.PCUSUARI u ON u.codusur = c.codusur1
        LEFT JOIN (
            SELECT codcli, SUM(valor) AS valor
            FROM {SCHEMA}.PCPREST
            WHERE vpago IS NULL OR vpago = '0'
            GROUP BY codcli
        ) prest ON c.codcli = prest.codcli
        LEFT JOIN (
            SELECT codcli, SUM(vlatend) AS vlatend
            FROM {SCHEMA}.PCPEDC
            WHERE posicao NOT IN ('C', 'F')
            GROUP BY codcli
        ) ped ON c.codcli = ped.codcli
        WHERE {filtro}
    """
    with get_connection() as conn:
        df = pd.read_sql(query, conn)
    return df

# ── UI ────────────────────────────────────────────────────────────────────────

st.title("Crédito e Cadastro de Cliente")

busca = st.text_input(
    "Código, CNPJ ou nome do cliente",
    placeholder="Ex: 12345 ou 23.504.919/0001-18 ou Rigarr"
)

if busca:
    try:
        df = buscar_cliente(busca)

        if len(df) > 1:
            opcoes  = {f"{r['CODCLI']} — {r['NOME']}": i for i, r in df.iterrows()}
            escolha = st.selectbox("Vários clientes encontrados, selecione:", list(opcoes.keys()))
            df      = df.loc[[opcoes[escolha]]]

        if df.empty:
            st.warning("Cliente não cadastrado.")

            cnpj_limpo      = re.sub(r"\D", "", busca)
            bloqueio_receita = False

            if len(cnpj_limpo) == 14:
                try:
                    dados_rf     = _fetch_cnpj(cnpj_limpo)
                    company      = dados_rf.get("company", {})
                    address      = dados_rf.get("address", {})
                    status_cnpj  = dados_rf.get("status", {}).get("text", "—")
                    razao_social = company.get("name") or "—"
                    cidade       = address.get("city") or "—"
                    uf           = address.get("state") or "—"
                    ies          = dados_rf.get("registrations", [])

                    cnpj_ok           = status_cnpj.lower() == "ativa"
                    _STATUS_IE_OK     = {"habilitado", "ativo", "sem restrição", "não encontrada"}
                    ie_pendencias     = [
                        (ie.get("state", ""), ie.get("status", {}).get("text", ""))
                        for ie in ies
                        if ie.get("status", {}).get("text", "").lower() not in _STATUS_IE_OK
                        and ie.get("status", {}).get("text", "")
                    ]
                    ie_ok = len(ie_pendencias) == 0

                    if not ies or ie_ok:
                        ie_display = "Sem restrição"
                    else:
                        ie_display = " | ".join(
                            f'{ie.get("state", "")}: {ie.get("status", {}).get("text", "—")}'
                            for ie in ies
                            if ie.get("status", {}).get("text", "").lower() not in _STATUS_IE_OK
                            and ie.get("status", {}).get("text", "")
                        )

                    cor_cnpj = "#28a745" if cnpj_ok else "#dc3545"
                    cor_ie   = "#28a745" if ie_ok   else "#dc3545"

                    st.markdown(f"""
<div style="border:1px solid #444;border-radius:8px;padding:16px;margin:8px 0;background:#1a1a2e">
  <div style="font-size:1.05em;font-weight:bold;margin-bottom:10px">📋 Dados na Receita Federal</div>
  <table style="width:100%;border-collapse:collapse;font-size:0.95em">
    <tr><td style="padding:4px 8px;color:#999;width:150px">Razão Social</td><td style="padding:4px 8px">{razao_social}</td></tr>
    <tr><td style="padding:4px 8px;color:#999">Cidade / UF</td><td style="padding:4px 8px">{cidade} / {uf}</td></tr>
    <tr><td style="padding:4px 8px;color:#999">Situação CNPJ</td><td style="padding:4px 8px"><b style="color:{cor_cnpj}">{status_cnpj}</b></td></tr>
    <tr><td style="padding:4px 8px;color:#999">Insc. Estadual</td><td style="padding:4px 8px"><b style="color:{cor_ie}">{ie_display}</b></td></tr>
  </table>
</div>
""", unsafe_allow_html=True)

                    if not cnpj_ok:
                        st.error(f"Cadastro bloqueado: situação do CNPJ na Receita Federal é '{status_cnpj}'. Apenas empresas com situação 'Ativa' podem ser cadastradas.")
                        bloqueio_receita = True
                    if not ie_ok:
                        pendencias_str = ", ".join(f"{est}: {sts}" for est, sts in ie_pendencias)
                        st.error(f"Cadastro bloqueado: Inscrição Estadual com pendência — {pendencias_str}.")
                        bloqueio_receita = True

                except Exception:
                    st.info("Não foi possível consultar a Receita Federal para este CNPJ.")

            if not bloqueio_receita:
                rca_logado    = st.session_state.get("rca_info", {})
                if rca_logado.get("codusur"):
                    nome_rca      = rca_logado["nome"]
                    codusur_input = str(rca_logado["codusur"])
                    st.info(f"RCA: {nome_rca} (cód. {codusur_input})")
                else:
                    codusur_input = st.text_input("Código do RCA (CODUSUR)", placeholder="Ex: 12")
                    nome_rca      = ""
                    if codusur_input:
                        if codusur_input.isdigit():
                            df_rca = buscar_rca(int(codusur_input))
                            if df_rca.empty:
                                st.error("RCA não encontrado.")
                            else:
                                nome_rca = str(df_rca.iloc[0]["NOME"]).strip()
                                st.success(f"RCA: {nome_rca} (cód. {codusur_input})")
                        else:
                            st.error("Digite apenas números.")

                if st.button("Solicitar cadastro por e-mail", disabled=not nome_rca):
                    from datetime import datetime as _dt
                    import zoneinfo as _zi
                    _hora     = _dt.now(_zi.ZoneInfo("America/Sao_Paulo")).hour
                    _saudacao = "Bom dia" if _hora < 12 else ("Boa tarde" if _hora < 18 else "Boa noite")
                    dados_receita = consultar_cnpj_receita(busca)
                    rca_linha     = f"RCA Solicitante  : {nome_rca} (cód. {codusur_input})"
                    if dados_receita:
                        corpo = (
                            f"{_saudacao},\n\n"
                            f"Solicito por gentileza o cadastramento do cliente:\n\n"
                            f"{rca_linha}\n\n"
                            f"Dados obtidos na Receita Federal:\n\n{dados_receita}\n\n"
                            f"Podem, por favor, realizar o cadastro?\n\nObrigado!"
                        )
                    else:
                        corpo = (
                            f"{_saudacao},\n\n"
                            f"Solicito por gentileza o cadastramento do cliente:\n\n"
                            f"{rca_linha}\n\n"
                            f"Não foi possível obter dados da Receita Federal para este CNPJ.\n\n"
                            f"Podem, por favor, realizar o cadastro manualmente?\n\nObrigado!"
                        )
                    notificar_cadastro(assunto="Solicitação de Cadastro de Cliente", corpo=corpo)
                    st.success("Solicitação enviada ao time de cadastro.")

        else:
            row        = df.iloc[0]
            limcred    = float(row["LIMCRED"])
            aberto     = float(row["VALOR_ABERTO"])
            pedidos    = float(row["VALOR_PEDIDOS"])
            disponivel = limcred - aberto - pedidos
            bloqueado  = str(row["BLOQUEIO"]).strip().upper() == "S"
            cnpj_fmt   = str(row["CNPJ"]).strip().replace(".", "").replace("/", "").replace("-", "")
            badge      = '<span class="badge-blq">BLOQUEADO</span>' if bloqueado else '<span class="badge-ok">ATIVO</span>'
            cor_disp   = "green" if disponivel >= 0 else "red"

            st.markdown(f"""
            <div class="card">
                <div style="display:flex; justify-content:space-between; align-items:center;">
                    <div>
                        <div class="nome">{row["NOME"]}</div>
                        <div class="sub">Cód: {row["CODCLI"]} &nbsp;|&nbsp; CNPJ: {cnpj_fmt} &nbsp;|&nbsp; RCA: {int(row["CODUSUR1"]) if row["CODUSUR1"] else "—"} — {str(row["NOME_RCA1"]).strip() if row["NOME_RCA1"] else "—"}</div>
                    </div>
                    <div>{badge}</div>
                </div>
            </div>
            """, unsafe_allow_html=True)

            c1, c2, c3, c4 = st.columns(4)
            for col, label, valor, cor in [
                (c1, "Limite de Crédito", limcred,    ""),
                (c2, "Títulos em Aberto", aberto,     ""),
                (c3, "Pedidos Pendentes", pedidos,    ""),
                (c4, "Disponível",        disponivel, cor_disp),
            ]:
                col.markdown(f"""
                <div class="metric-card">
                    <div class="metric-label">{label}</div>
                    <div class="metric-value {cor}">R$ {valor:,.2f}</div>
                </div>
                """, unsafe_allow_html=True)

            if bloqueado:
                st.warning("Cliente BLOQUEADO.")
            elif disponivel <= 0:
                st.warning(f"Limite indisponível (R$ {disponivel:,.2f}).")

            rca_sol    = st.session_state.get("rca_info", {})
            rca_linha  = f"RCA Solicitante: {rca_sol.get('nome', '—')} (cód. {rca_sol.get('codusur', '—')})"
            info_cliente = (
                f"Cliente: {row['NOME']} ({cnpj_fmt}) — Cód. {row['CODCLI']}\n"
                f"{rca_linha}"
            )

            st.markdown("---")
            valor_pedido = st.number_input(
                "Valor do pedido (R$)", min_value=0.0, step=100.0, format="%.2f"
            )
            if bloqueado:
                assunto   = f"Solicitação de Desbloqueio — {row['NOME']}"
                corpo     = f"Solicitação de desbloqueio de cliente.\n\n{info_cliente}\n\nPodem, por favor, realizar o desbloqueio?\n\nObrigado!"
                label_btn = "Solicitar Desbloqueio pelo WhatsApp"
            else:
                assunto      = f"Solicitação de Limite — {row['NOME']}"
                valor_linha  = f"Valor do Pedido    : R$ {valor_pedido:,.2f}" if valor_pedido > 0 else ""
                corpo        = (
                    f"Solicitação de aumento de limite de crédito.\n\n"
                    f"{info_cliente}\n"
                    + (f"{valor_linha}\n" if valor_linha else "")
                    + f"\nPodem, por favor, realizar o ajuste?\n\nObrigado!"
                )
                label_btn = "Solicitar Aumento de Limite pelo WhatsApp"

            import urllib.parse
            msg_wa = urllib.parse.quote(f"{assunto}\n\n{corpo}")
            st.markdown(
                f'<a href="https://wa.me/{WHATSAPP_FINANCEIRO}?text={msg_wa}" target="_blank">'
                f'<button style="width:100%;padding:0.5rem;background:#25D366;color:white;border:none;border-radius:8px;font-size:1rem;cursor:pointer;">📲 {label_btn}</button></a>',
                unsafe_allow_html=True,
            )

    except Exception as e:
        st.error(f"Erro: {e}")
