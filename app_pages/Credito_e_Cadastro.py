"""Crédito e Cadastro de Cliente."""
import os, re, base64, json, urllib.parse
from email.mime.text import MIMEText

import streamlit as st
import pandas as pd
import requests
import urllib3

from utils import inject_css, page_header, require_auth, get_conn, sql, fmt_brl, ensure_valid_token, back_button

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

inject_css()
rca_info = require_auth()

back_button()
page_header("Crédito e Cadastro de Cliente", "OfftradeHub · Off Trade")

SCHEMA             = "CRC"
# GARRIDO também tem vendas do RJ (pedido do usuário em 2026-08-14) — sem
# isso, cliente cadastrado só na base GARRIDO não aparecia na busca (nem
# última venda, nem elegibilidade de troca de RCA).
FONTES_CLIENTE     = [("crc", "CRC"), ("garrido", "GARRIDO")]
EMAIL_FINANCEIRO   = "cadastro@rigarr.com.br"
EMAIL_CADASTRO_CC  = "offtrade@rigarr.com.br"
WHATSAPP_FINANCEIRO = "5521964384318"
CHAVE_API_CNPJ     = os.getenv("CHAVE_API_CNPJ", "")
EVOLUTION_API_URL  = os.getenv("EVOLUTION_API_URL", os.getenv("EVOLUTION_BASE_URL", ""))
EVOLUTION_API_KEY  = os.getenv("EVOLUTION_API_KEY", os.getenv("EVOLUTION_KEY", ""))
EVOLUTION_INSTANCE = "bees"


def _enviar_whatsapp(mensagem: str):
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


def _enviar_email(assunto: str, corpo: str, cc: str = None) -> bool:
    """Retorna True só quando o e-mail foi de fato aceito pela Gmail API —
    quem chama não deve mostrar 'enviado com sucesso' sem checar o retorno."""
    try:
        try:
            token = ensure_valid_token()
        except RuntimeError:
            # ensure_valid_token() já limpou sessão/cookie antes de levantar
            # (sem refresh_token ou Google recusou renovar) — mesmo motivo do
            # 401 abaixo: rerun direto pra tela de login em vez de aviso +
            # pedir F5 manual.
            st.rerun()
        access_token = token.get("access_token", "")
        remetente    = rca_info.get("email", "")
        msg          = MIMEText(corpo)
        msg["Subject"] = assunto
        msg["From"]    = remetente
        msg["To"]      = EMAIL_FINANCEIRO
        if cc:
            msg["Cc"] = cc
        raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
        r = requests.post(
            "https://gmail.googleapis.com/gmail/v1/users/me/messages/send",
            headers={"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"},
            json={"raw": raw},
            timeout=10,
        )
        if r.status_code == 401:
            # Token passou pela checagem de validade em ensure_valid_token()
            # mas o Gmail recusou mesmo assim (ex: revogado manualmente,
            # relógio do servidor dessincronizado) — limpa a sessão igual a
            # ensure_valid_token() faz no caso "sem refresh_token", senão
            # "atualizar a página" não resolve (mesmo motivo do outro caso).
            st.session_state.pop("token", None)
            try:
                from streamlit_cookies_controller import CookieController
                CookieController().remove("offtrade_token")
            except Exception:
                pass
            # st.rerun() em vez de st.warning() + esperar o usuário atualizar
            # manualmente: com a sessão já limpa acima, o rerun cai direto na
            # tela "Entrar com Google" (require_auth() roda de novo do topo),
            # sem mostrar o aviso confuso de "Gmail recusou o token" — pedido
            # do usuário em 2026-08-14 pra não precisar dar F5 na mão.
            st.rerun()
        elif not r.ok:
            st.warning(f"Erro ao enviar e-mail: {r.status_code} — {r.text[:300]}")
            return False
        return True
    except Exception as e:
        st.warning(f"Erro ao enviar e-mail: {e}")
        return False


def _fetch_cnpj(cnpj_limpo: str, incluir_registros: bool = True) -> dict:
    """registrations=BR (Inscrição Estadual) usa um serviço à parte do
    provedor (cnpja.com) que já ficou fora do ar sozinho (503 'ccc service
    is offline', confirmado em 2026-08-17) mesmo com os dados básicos de
    CNPJ disponíveis — por isso incluir_registros=False existe, pra quem
    chama poder cair pro básico em vez de perder a consulta inteira."""
    url = f"https://api.cnpja.com/office/{cnpj_limpo}?simples=true"
    if incluir_registros:
        url += "&registrations=BR"
    resp = requests.get(
        url,
        headers={"Authorization": CHAVE_API_CNPJ},
        verify=False,
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json()


def _consultar_cnpj_receita(cnpj: str) -> str:
    try:
        cnpj_limpo = re.sub(r"\D", "", cnpj)
        if len(cnpj_limpo) != 14:
            return ""
        try:
            d = _fetch_cnpj(cnpj_limpo, incluir_registros=True)
        except Exception:
            # Serviço de IE fora do ar não pode derrubar o resto dos dados
            # (razão social, endereço etc.) — cai pro básico, só perde a
            # linha de Insc. Estadual do texto (ver docstring de _fetch_cnpj).
            d = _fetch_cnpj(cnpj_limpo, incluir_registros=False)
        company  = d.get("company", {})
        address  = d.get("address", {})

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

        ies    = d.get("registrations", [])
        ie_txt = ""
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
            f"Insc. Estadual :{ie_txt}" if ie_txt else None,
        ]
        return "\n".join(l for l in linhas if l)
    except Exception as e:
        return f"ERRO: {e}"


@st.cache_data(ttl=600)
def _buscar_rca(codusur: int):
    return sql(
        f"SELECT CODUSUR, NOME, TELEFONE1, EMAIL FROM {SCHEMA}.PCUSUARI WHERE CODUSUR = :1",
        params=[codusur],
    )


@st.cache_data(ttl=300)
def _buscar_cliente(busca: str):
    busca  = busca.strip()
    cnpj   = re.sub(r"\D", "", busca)
    if cnpj.isdigit() and len(cnpj) == 14:
        filtro = f"REPLACE(REPLACE(REPLACE(c.cgcent,'.',''),'/',''),'-','') = '{cnpj}'"
    elif busca.isdigit():
        filtro = f"c.codcli = {int(busca)}"
    else:
        nome_upper = busca.upper().replace("'", "''")
        filtro = f"UPPER(c.cliente) LIKE '%{nome_upper}%'"

    frames = []
    for db, schema in FONTES_CLIENTE:
        try:
            df = sql(f"""
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
            """, db=db)
            if not df.empty:
                df["FONTE"] = schema
                frames.append(df)
        except Exception:
            # Uma fonte fora do ar não pode derrubar a busca nas outras —
            # mesmo padrão de _resolve_rca() em utils.py.
            continue
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


# ── UI ────────────────────────────────────────────────────────────────────────

busca = st.text_input(
    "Código, CNPJ ou nome do cliente",
    placeholder="Ex: 12345 ou 23.504.919/0001-18 ou Rigarr",
)

if busca:
    try:
        df = _buscar_cliente(busca)

        if len(df) > 1:
            # Inclui a fonte no label — CRC e GARRIDO são bases Winthor
            # separadas, cada uma com sua própria numeração de CODCLI, então
            # o mesmo código pode identificar clientes diferentes nas duas.
            opcoes  = {f"{r['CODCLI']} — {r['NOME']} ({r['FONTE']})": i for i, r in df.iterrows()}
            escolha = st.selectbox("Vários clientes encontrados, selecione:", list(opcoes.keys()))
            df      = df.loc[[opcoes[escolha]]]

        if df.empty:
            st.warning("Cliente não cadastrado.")
            cnpj_limpo       = re.sub(r"\D", "", busca)
            bloqueio_receita = False
            ie_indisponivel  = False

            if len(cnpj_limpo) in (12, 13):
                # Perto do tamanho de CNPJ (14) mas não exatamente — sem isso
                # caía direto no ramo "senão é código de cliente" (linha
                # abaixo em _buscar_cliente), nunca batia com nada e o
                # usuário só via "Cliente não cadastrado" sem saber que o
                # número em si tava incompleto (confirmado em 2026-08-04,
                # caso 3417024800012 — 13 dígitos, sem bater nem como CNPJ
                # com zero à esquerda nem como código de cliente).
                st.warning(
                    f'⚠️ "{busca}" tem {len(cnpj_limpo)} dígitos — CNPJ tem 14. '
                    "Confira se não falta algum dígito (ex: zero à esquerda) antes de prosseguir."
                )
            elif len(cnpj_limpo) == 14 and not CHAVE_API_CNPJ:
                st.warning("⚠️ CHAVE_API_CNPJ não configurada — consulta à Receita Federal desativada. Configure a variável no .env.")
            elif len(cnpj_limpo) == 14:
                dados_rf        = None
                ie_indisponivel = False
                try:
                    dados_rf = _fetch_cnpj(cnpj_limpo, incluir_registros=True)
                except Exception:
                    # Consulta completa (CNPJ + Insc. Estadual) falhou — o
                    # serviço de IE do provedor (cnpja.com) já teve
                    # instabilidade própria (503 'ccc service is offline',
                    # confirmado em 2026-08-17) mesmo com o CNPJ básico
                    # disponível, então tenta de novo só com o básico em vez
                    # de perder a consulta inteira (ver docstring de
                    # _fetch_cnpj).
                    try:
                        dados_rf        = _fetch_cnpj(cnpj_limpo, incluir_registros=False)
                        ie_indisponivel = True
                    except Exception:
                        dados_rf = None

                if dados_rf is None:
                    st.info("Não foi possível consultar a Receita Federal para este CNPJ.")
                    bloqueio_receita = True
                else:
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
                    ie_ok = len(ie_pendencias) == 0 and not ie_indisponivel

                    if ie_indisponivel:
                        ie_display = "Não verificada — serviço de IE fora do ar"
                        cor_ie     = "#f5c518"
                    else:
                        ie_display = "Sem restrição" if (not ies or ie_ok) else " | ".join(
                            f'{est}: {sts}' for est, sts in ie_pendencias
                        )
                        cor_ie = "#28a745" if ie_ok else "#dc3545"
                    cor_cnpj = "#28a745" if cnpj_ok else "#dc3545"

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
                        st.error(f"Cadastro bloqueado: situação '{status_cnpj}'. Apenas empresas 'Ativa' podem ser cadastradas.")
                        bloqueio_receita = True
                    if ie_indisponivel:
                        st.warning("Inscrição Estadual não pôde ser verificada agora (serviço do provedor fora do ar) — solicitação segue, mas o time de cadastro vai receber um aviso pra conferir manualmente.")
                    elif not ie_ok:
                        st.error(f"Cadastro bloqueado: Inscrição Estadual com pendência — {', '.join(f'{e}: {s}' for e, s in ie_pendencias)}.")
                        bloqueio_receita = True

            if not bloqueio_receita:
                if rca_info.get("codusur"):
                    nome_rca      = rca_info["nome"]
                    codusur_input = str(rca_info["codusur"])
                    st.info(f"RCA: {nome_rca} (cód. {codusur_input})")
                else:
                    codusur_input = st.text_input("Código do RCA (CODUSUR)", placeholder="Ex: 12")
                    nome_rca      = ""
                    if codusur_input:
                        if codusur_input.isdigit():
                            df_rca = _buscar_rca(int(codusur_input))
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
                    dados_receita = _consultar_cnpj_receita(busca)
                    rca_linha     = f"RCA Solicitante  : {nome_rca} (cód. {codusur_input})"
                    obs_ie = (
                        "\n\n⚠️ Observação: a Inscrição Estadual não pôde ser verificada "
                        "automaticamente no momento da solicitação (serviço do provedor fora "
                        "do ar) — favor confirmar manualmente antes de aprovar o cadastro."
                        if ie_indisponivel else ""
                    )
                    if dados_receita:
                        corpo = (
                            f"{_saudacao},\n\nSolicito o cadastramento do cliente:\n\n"
                            f"{rca_linha}\n\nDados da Receita Federal:\n\n{dados_receita}{obs_ie}\n\n"
                            f"Podem realizar o cadastro?\n\nObrigado!"
                        )
                    else:
                        corpo = (
                            f"{_saudacao},\n\nSolicito o cadastramento do cliente:\n\n"
                            f"{rca_linha}\n\nNão foi possível obter dados da Receita Federal.{obs_ie}\n\n"
                            f"Podem realizar o cadastro manualmente?\n\nObrigado!"
                        )
                    if _enviar_email("Solicitação de Cadastro de Cliente", corpo, cc=EMAIL_CADASTRO_CC):
                        st.success("Solicitação enviada ao time de cadastro.")

        else:
            row        = df.iloc[0]
            limcred    = float(row["LIMCRED"])
            aberto     = float(row["VALOR_ABERTO"])
            pedidos    = float(row["VALOR_PEDIDOS"])
            disponivel = limcred - aberto - pedidos
            bloqueado  = str(row["BLOQUEIO"]).strip().upper() == "S"
            cnpj_fmt   = re.sub(r"\D", "", str(row["CNPJ"]).strip())
            badge      = '<span class="badge badge-blq">BLOQUEADO</span>' if bloqueado else '<span class="badge badge-ok">ATIVO</span>'
            cor_disp   = "green" if disponivel >= 0 else "red"

            codusur_atual   = int(row["CODUSUR1"]) if row["CODUSUR1"] else None
            nome_rca_atual  = str(row["NOME_RCA1"]).strip() if row["NOME_RCA1"] else "—"
            dtultcomp       = row["DTULTCOMP"] if pd.notna(row["DTULTCOMP"]) else None
            dias_sem_compra = int(row["DIAS_SEM_COMPRA"]) if pd.notna(row["DIAS_SEM_COMPRA"]) else None
            ultima_venda_txt = f"{dtultcomp} ({dias_sem_compra} dias atrás)" if dtultcomp else "Nunca comprou"

            st.markdown(f"""
<div class="card" style="margin-bottom:16px">
<div style="display:flex;justify-content:space-between;align-items:center">
<div>
<div style="font-size:1.1rem;font-weight:700">{row["NOME"]}</div>
<div style="font-size:.8rem;color:#94a3b8;margin-top:2px">
Cód: {row["CODCLI"]} &nbsp;|&nbsp; CNPJ: {cnpj_fmt}
&nbsp;|&nbsp; RCA: {codusur_atual if codusur_atual else "—"} — {nome_rca_atual}
&nbsp;|&nbsp; Última venda: {ultima_venda_txt}
</div>
</div>
<div>{badge}</div>
</div>
</div>
""".strip(), unsafe_allow_html=True)

            c1, c2, c3, c4 = st.columns(4)
            for col, label, valor, cor in [
                (c1, "Limite de Crédito", limcred,    ""),
                (c2, "Títulos em Aberto", aberto,     ""),
                (c3, "Pedidos Pendentes", pedidos,    ""),
                (c4, "Disponível",        disponivel, cor_disp),
            ]:
                col.markdown(
                    f'<div class="metric-card">'
                    f'<div class="metric-label">{label}</div>'
                    f'<div class="metric-value {cor}">{fmt_brl(valor)}</div>'
                    f'</div>',
                    unsafe_allow_html=True,
                )

            if bloqueado:
                st.warning("Cliente BLOQUEADO.")
            elif disponivel <= 0:
                st.warning(f"Limite indisponível ({fmt_brl(disponivel)}).")

            rca_linha    = f"RCA Solicitante: {rca_info.get('nome', '—')} (cód. {rca_info.get('codusur', '—')})"
            info_cliente = (
                f"Cliente: {row['NOME']} ({cnpj_fmt}) — Cód. {row['CODCLI']}\n{rca_linha}"
            )

            # ── Troca de RCA ─────────────────────────────────────────────────
            # codusur1 = 10 é o RCA "estacionamento" usado pro cadastro pra
            # marcar cliente sem vendedor ativo (ver clientes_inativos.html /
            # exportacao_clientes_inativos.py — mesma convenção usada lá).
            # 200 entrou na mesma lista a pedido do usuário em 2026-08-14.
            RCAS_SEM_VENDEDOR   = {10, 200}
            DIAS_LIMITE_TROCA   = 60
            codusur_solicitante = rca_info.get("codusur")
            rca_inativo         = codusur_atual in RCAS_SEM_VENDEDOR
            sem_compra_limite   = dias_sem_compra is None or dias_sem_compra > DIAS_LIMITE_TROCA
            elegivel_troca      = rca_inativo or sem_compra_limite
            confirmar_key       = f"confirmar_troca_{row['CODCLI']}"

            if elegivel_troca:
                motivo = f"RCA inativo (cód. {codusur_atual})" if rca_inativo else (
                    "nunca comprou" if dias_sem_compra is None else f"{dias_sem_compra} dias sem compra"
                )
                st.warning(f"⚠️ Cliente sem vendedor ativo — {motivo}.")

                if st.button("🔄 Pedir Troca de RCA", key=f"btn_{confirmar_key}"):
                    st.session_state[confirmar_key] = True

                if st.session_state.get(confirmar_key):
                    st.info(
                        f"Confirma a solicitação de troca de RCA de **{row['NOME']}** "
                        f"para **{rca_info.get('nome', '—')}** (cód. {codusur_solicitante if codusur_solicitante else '—'})?"
                    )
                    col_ok, col_cancel = st.columns(2)
                    if col_ok.button("✅ Confirmar", key=f"ok_{confirmar_key}"):
                        corpo_troca = (
                            f"Solicito a troca de RCA do cliente:\n\n"
                            f"Cliente: {row['NOME']} ({cnpj_fmt}) — Cód. {row['CODCLI']}\n"
                            f"RCA Atual  : {nome_rca_atual} (cód. {codusur_atual if codusur_atual else '—'})\n"
                            f"Motivo     : {motivo}\n"
                            f"Última venda: {ultima_venda_txt}\n\n"
                            f"{rca_linha}\n\n"
                            f"Podem realizar a troca de RCA para o solicitante?\n\nObrigado!"
                        )
                        if _enviar_email(f"Solicitação de Troca de RCA — {row['NOME']}", corpo_troca, cc=EMAIL_CADASTRO_CC):
                            st.success("Solicitação de troca de RCA enviada ao cadastro.")
                            st.session_state[confirmar_key] = False
                    if col_cancel.button("Cancelar", key=f"cancel_{confirmar_key}"):
                        st.session_state[confirmar_key] = False
                        st.rerun()
            elif codusur_solicitante and codusur_atual and codusur_atual != codusur_solicitante:
                st.info(f"ℹ️ Este cliente já possui vendedor dedicado: **{nome_rca_atual}** (cód. {codusur_atual}).")

            st.markdown("---")
            # st.form + st.form_submit_button em vez de widgets soltos + st.button:
            # a tentativa anterior (comentário removido) achava que st.button já
            # bastava, mas number_input/selectbox só sincronizam o valor digitado
            # pro backend em change/blur — clicar direto no botão sem tirar o foco
            # do campo ainda podia disparar o rerun com o valor antigo (0,00/index
            # padrão). Dentro de um form, os widgets só aplicam o valor quando o
            # form_submit_button é clicado, então o valor atual sempre é capturado
            # (confirmado bug reportado pelo usuário em 2026-08-13: valor e prazo
            # não apareciam na mensagem).
            with st.form("form_limite_pedido"):
                col_valor, col_prazo = st.columns(2)
                valor_pedido = col_valor.number_input("Valor do pedido (R$)", min_value=0.0, step=100.0, format="%.2f")
                prazo_pagto  = col_prazo.selectbox("Prazo de pagamento (dias)", [7, 14, 21, 28, 30, 35, 42, 45, 60, 90], index=4)

                if bloqueado:
                    assunto     = f"Solicitação de Desbloqueio — {row['NOME']}"
                    valor_linha = f"Valor do Pedido    : {fmt_brl(valor_pedido)}" if valor_pedido > 0 else ""
                    prazo_linha = f"Prazo de Pagamento : {prazo_pagto} dias"
                    corpo       = (
                        f"Solicitação de desbloqueio de cliente.\n\n{info_cliente}\n"
                        + (f"{valor_linha}\n" if valor_linha else "")
                        + f"{prazo_linha}\n"
                        + f"\nPodem realizar o desbloqueio?\n\nObrigado!"
                    )
                    label_btn = "Solicitar Desbloqueio pelo WhatsApp"
                else:
                    assunto     = f"Solicitação de Limite — {row['NOME']}"
                    valor_linha = f"Valor do Pedido    : {fmt_brl(valor_pedido)}" if valor_pedido > 0 else ""
                    prazo_linha = f"Prazo de Pagamento : {prazo_pagto} dias"
                    corpo       = (
                        f"Solicitação de aumento de limite de crédito.\n\n{info_cliente}\n"
                        + (f"{valor_linha}\n" if valor_linha else "")
                        + f"{prazo_linha}\n"
                        + f"\nPodem realizar o ajuste?\n\nObrigado!"
                    )
                    label_btn = "Solicitar Aumento de Limite pelo WhatsApp"

                enviado = st.form_submit_button(f"📲 {label_btn}", use_container_width=True)

            if enviado:
                msg_wa = urllib.parse.quote(f"{assunto}\n\n{corpo}")
                wa_url = f"https://wa.me/{WHATSAPP_FINANCEIRO}?text={msg_wa}"
                st.markdown(
                    f'<a href="{wa_url}" target="_blank">'
                    f'<button style="width:100%;padding:.5rem;background:#25D366;color:white;border:none;'
                    f'border-radius:8px;font-size:1rem;cursor:pointer">✅ Clique aqui para abrir o WhatsApp</button></a>',
                    unsafe_allow_html=True,
                )

    except Exception as e:
        st.error(f"Erro: {e}")
