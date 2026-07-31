"""Home — Streamlit entrypoint.

Não é mais servida publicamente (nginx roteia "/" para o index.html estático).
Único propósito: ponto de entrada do `streamlit run` para as páginas que
continuam vivas em pages/ (Credito_e_Cadastro, Admin_Objetivos).
"""
import streamlit as st
from utils import inject_css, page_header, require_auth

st.set_page_config(page_title="OfftradeHub — Dashboard Comercial", page_icon="📊", layout="wide")

inject_css()
require_auth()

page_header("OfftradeHub — Dashboard Comercial", "Off Trade & On Trade · RJ")

st.markdown(
    "Os dashboards agora são servidos em "
    "[offtrade.duckdns.org](https://offtrade.duckdns.org/). "
    "Esta tela só existe para as páginas de Crédito e Cadastro / Admin de Objetivos."
)
