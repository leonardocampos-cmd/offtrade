"""Home — Dashboard Comercial Rigarr."""
import streamlit as st
from utils import inject_css, page_header, section_title, require_auth

st.set_page_config(
    page_title="Rigarr — Dashboard Comercial",
    page_icon="📊",
    layout="wide",
)

inject_css()
require_auth()

page_header("Rigarr — Dashboard Comercial", "Off Trade &amp; On Trade · RJ")

# ── Campanhas Ativas ──────────────────────────────────────────────────────────

section_title('Campanhas <span style="font-size:.6rem;font-weight:800;padding:1px 8px;border-radius:20px;background:rgba(34,197,94,.15);color:#22c55e;margin-left:8px">Ativas</span>')

col1, col2, col3 = st.columns(3, gap="small")

with col1:
    st.markdown("""
<a class="camp-card moving" href="/Moving" target="_self">
    <div class="camp-card-top">
        <span class="camp-name">Campanha MOVING</span>
        <span class="camp-status status-ativa">Ativa</span>
    </div>
    <div class="camp-periodo">Junho 2026</div>
    <div class="camp-scope">Off Trade RJ</div>
    <div class="camp-premios">
        Prêmio: 5% do faturamento Moving individual<br>
        Gatilho de equipe: R$ 120.000
    </div>
    <span class="camp-link">Ver Apuração →</span>
</a>
""".strip(), unsafe_allow_html=True)

with col2:
    st.markdown("""
<a class="camp-card amarula" href="/Amarula" target="_self">
    <div class="camp-card-top">
        <span class="camp-name">Campanha Amarula</span>
        <span class="camp-status status-ativa">Ativa</span>
    </div>
    <div class="camp-periodo">25/05/2026 — 25/06/2026</div>
    <div class="camp-scope">Empresa toda · On Trade + Off Trade</div>
    <div class="camp-premios">
        Melhor Positivação → R$ 3.000<br>
        Melhor Faturamento → R$ 3.000
    </div>
    <span class="camp-link">Ver Ranking →</span>
</a>
""".strip(), unsafe_allow_html=True)

with col3:
    st.markdown("""
<a class="camp-card pernod" href="/Pernod" target="_self">
    <div class="camp-card-top">
        <span class="camp-name">Campanha PERNOD</span>
        <span class="camp-status status-ativa">Mai/26 Ativa</span>
    </div>
    <div class="camp-periodo">Maio 2026</div>
    <div class="camp-scope">Off Trade RJ</div>
    <div class="camp-premios">
        Jameson: R$ 10 por SKU/PDV<br>
        Demais Pernod: R$ 5 por SKU/PDV
    </div>
    <span class="camp-link">Ver Ranking →</span>
</a>
""".strip(), unsafe_allow_html=True)

# ── Campanhas Encerradas ──────────────────────────────────────────────────────

section_title('Campanhas <span style="font-size:.6rem;font-weight:800;padding:1px 8px;border-radius:20px;background:rgba(148,163,184,.1);color:#94a3b8;margin-left:8px">Encerradas</span>')

col1, *_ = st.columns(3, gap="small")
with col1:
    st.markdown("""
<a class="camp-card pernod" href="/Pernod" target="_self">
    <div class="camp-card-top">
        <span class="camp-name">Campanha PERNOD</span>
        <span class="camp-status status-enc">Abr/26 Enc.</span>
    </div>
    <div class="camp-periodo">Abril 2026</div>
    <div class="camp-scope">Off Trade RJ</div>
    <div class="camp-premios">
        Jameson: R$ 10 por SKU/PDV<br>
        Demais Pernod: R$ 5 por SKU/PDV
    </div>
    <span class="camp-link">Ver Resultado →</span>
</a>
""".strip(), unsafe_allow_html=True)

# ── Dashboards ────────────────────────────────────────────────────────────────

section_title("Dashboards")

DASHBOARDS = [
    ("📊", "Metas Off Trade",    "Faturamento, positivação e metas mensais por vendedor RJ",   "/Metas"),
    ("🚚", "Entregas",           "Acompanhamento de pedidos e status de entrega",               "/Entregas"),
    ("🗺️", "Dashboard SP",      "Vendas e indicadores da equipe de São Paulo",                 "/SP"),
    ("🍺", "Amarula",            "Ranking da campanha Amarula por positivação e faturamento",   "/Amarula"),
    ("🥃", "Pernod",             "Apuração da campanha Pernod por SKU e PDV",                   "/Pernod"),
    ("🏃", "Moving",             "Apuração da campanha Moving por vendedor",                    "/Moving"),
    ("🔄", "Migração RCA 588",   "Clientes do RCA 588 que compraram de outro vendedor",         "/588"),
    ("🏢", "Base de Clientes",   "Clientes por RCA com Razão Social, CNPJ e exportação CSV",   "/Clientes_RCA"),
    ("📋", "Catálogo",           "Saldo e valor em estoque por produto com preços",             "/Catalogo"),
    ("💳", "Crédito e Cadastro", "Consulta de crédito, desbloqueio e solicitação de cadastro", "/Credito_e_Cadastro"),
]

cards_html = ""
for i, (icon, name, desc, href) in enumerate(DASHBOARDS):
    cards_html += (
        f'<a class="dash-card" href="{href}" target="_self">'
        f'<span class="dash-icon">{icon}</span>'
        f'<div><div class="dash-name">{name}</div><div class="dash-desc">{desc}</div></div>'
        f'</a>'
    )

st.markdown(
    f'<div style="display:grid;grid-template-columns:repeat(3,1fr);gap:10px">{cards_html}</div>',
    unsafe_allow_html=True,
)

st.markdown("""
<div style="text-align:center;margin-top:32px;padding-top:16px;border-top:1px solid #2d3144;
     color:#94a3b8;font-size:.72rem">
    Dashboard Comercial · Rigarr · Off Trade + On Trade
</div>
""".strip(), unsafe_allow_html=True)
