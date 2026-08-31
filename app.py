"""Home + roteador de navegação — Streamlit entrypoint.

Nenhum menu visível pra ninguém (pedido em 2026-08-04 pra não confundir quem
só precisa pedir limite/cadastro, "app" e "Admin Objetivos" removidas do
menu em 2026-08-24 — position="hidden" abaixo esconde a barra de navegação
inteira, então isso vale mesmo com 1 única página registrada).

"Credito e Cadastro" saiu do Streamlit em 2026-08-30 — virou HTML estático
(credito_cadastro.html) + backend Flask próprio (credito_cadastro_api.py),
fora deste app. Preco Promo continua aqui por enquanto.

A página "app" aqui embaixo é só uma casca invisível, não uma home de
verdade: com uma ÚNICA página registrada, o Streamlit trata ela como
"página padrão" e o cliente reescreve a URL do navegador de volta pra raiz
do domínio (offtrade.duckdns.org/) depois de carregar — só que a raiz é
servida pelo nginx como o site estático (dashboard hub), não chega no
Streamlit, então isso quebraria o link de "Preco_Promo" (voltava pra home
estática ao dar F5/recarregar). Mantendo essa página muda como
primeira/padrão, "Preco_Promo" nunca é a página padrão e sua URL nunca é
reescrita — mesmo bug documentado em 2026-08-24 pra "Credito_e_Cadastro".

NÃO chamar require_auth() aqui (cada app_pages/*.py já chama a própria, ver
Preco_Promo.py) — chamar antes de st.navigation() rodava o loop de retry de
cookie (st.rerun() várias vezes, ver require_auth() em utils.py) ANTES do
Streamlit saber que a URL pedida era uma página válida, e isso resetava o
roteamento pro fallback (_home()) mesmo com a URL certa na barra de
endereço — bug real reportado pelo usuário em 2026-08-26."""
import streamlit as st
from utils import inject_css

st.set_page_config(page_title="OfftradeHub — Dashboard Comercial", page_icon="📊", layout="wide", initial_sidebar_state="expanded")
inject_css()

_pagina_preco_promo = st.Page("app_pages/Preco_Promo.py", title="Preco Promo", icon="🏷️", url_path="Preco_Promo")


def _home():
    st.switch_page(_pagina_preco_promo)


_pagina_home = st.Page(_home, title="app", icon="📊", url_path="app")

st.navigation([_pagina_home, _pagina_preco_promo], position="hidden").run()
