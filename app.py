"""Home + roteador de navegação — Streamlit entrypoint.

"Preço Promo" saiu do Streamlit em 2026-09-14 (pedido do usuário: "não quero
que seja no streamlit, direto num arquivo html") — virou HTML estático
(preco_promo.html) + backend Flask próprio (blueprint bp_precopromo em
pedidos_mercos_api.py), mesmo padrão que "Credito e Cadastro" já tinha
seguido em 2026-08-30. Era a ÚLTIMA página registrada aqui (Admin_Objetivos
já tinha saído do menu em 2026-08-24 e nunca foi religada à navegação) —
esse app.py não tem mais nenhuma página de verdade pra servir; mantido só
como placeholder pra não derrubar o serviço systemd "offtrade" (que outros
scripts/documentação ainda referenciam) até decidir desativá-lo de vez.
"""
import streamlit as st
from utils import inject_css

st.set_page_config(page_title="OfftradeHub — Dashboard Comercial", page_icon="📊", layout="wide")
inject_css()

st.info("Essa página saiu do Streamlit. Acesse **/preco_promo.html** no site.")
