"""588 — em desenvolvimento."""
import streamlit as st
from utils import inject_css, page_header, require_auth

st.set_page_config(page_title="588", layout="wide")
inject_css()
require_auth()
page_header("588")
st.info("🚧 Página em desenvolvimento.")
