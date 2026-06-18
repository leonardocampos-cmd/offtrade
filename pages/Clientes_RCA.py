"""Clientes_RCA — em desenvolvimento."""
import streamlit as st
from utils import inject_css, page_header, require_auth

st.set_page_config(page_title="Clientes_RCA", layout="wide")
inject_css()
require_auth()
page_header("Clientes_RCA")
st.info("🚧 Página em desenvolvimento.")
