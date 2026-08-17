"""Home + roteador de navegação — Streamlit entrypoint.

Controla o menu lateral: "app" (esta home) e "Admin Objetivos" só aparecem
pra SUPER_ADMIN_EMAIL (utils.py) — todo mundo mais só vê "Credito e
Cadastro", que é a única página que vendedor/RCA comum usa (pedido em
2026-08-04, pra não confundir quem só precisa pedir limite/cadastro com
menus administrativos irrelevantes pra eles).
"""
import streamlit as st
from utils import inject_css, page_header, require_auth, SUPER_ADMIN_EMAIL

st.set_page_config(page_title="OfftradeHub — Dashboard Comercial", page_icon="📊", layout="wide", initial_sidebar_state="expanded")
inject_css()
rca_info = require_auth()

_is_super_admin = rca_info.get("email", "").lower() == SUPER_ADMIN_EMAIL


def _home():
    page_header("OfftradeHub — Dashboard Comercial", "Off Trade & On Trade · RJ")
    st.markdown(
        "Os dashboards agora são servidos em "
        "[offtrade.duckdns.org](https://offtrade.duckdns.org/). "
        "Esta tela só existe para as páginas de Crédito e Cadastro / Admin de Objetivos."
    )


_paginas = [st.Page("app_pages/Credito_e_Cadastro.py", title="Credito e Cadastro", icon="💳", url_path="Credito_e_Cadastro")]
if _is_super_admin:
    _paginas = (
        [st.Page(_home, title="app", icon="📊", url_path="app")]
        + _paginas
        + [st.Page("app_pages/Admin_Objetivos.py", title="Admin Objetivos", icon="⚙️", url_path="Admin_Objetivos")]
    )

st.navigation(_paginas).run()
