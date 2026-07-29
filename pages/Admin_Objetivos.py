"""Admin — Cadastro de Metas por Vendedor."""
import json
from pathlib import Path
from datetime import date

import streamlit as st

from utils import inject_css, page_header, require_auth, get_conn

st.set_page_config(page_title="Admin — Objetivos", page_icon="⚙️", layout="wide")
inject_css()
rca = require_auth()

if not rca.get("admin"):
    st.error("Acesso restrito a administradores.")
    st.stop()

page_header("⚙️ Admin — Objetivos Off Trade", "Cadastro de objetivos por vendedor e mês")

METAS_FILE = Path(__file__).parent.parent / "metas_config.json"

CAMPOS = [
    ("fat_tt",              "Faturamento TT",        True),
    ("pos_tt",              "Positivação TT",         False),
    ("fat_castas",          "Fat. Castas",            True),
    ("fat_hob_azeite",      "Fat. HOB + Azeite",      True),
    ("fat_domecq_passport", "Fat. Domeq + Passport",  True),
    ("pos_hob_azeite",      "Pos. HOB + Azeite",      False),
    ("pos_reckit",          "Pos. Reckit",            False),
    ("pos_crusoe",          "Pos. Crusoe",            False),
    ("pos_tatuzinho",       "Pos. Tatuzinho",         False),
    ("pos_redbull",         "Pos. Red Bull",          False),
    ("pos_pinatti",         "Pos. Pinatti",           False),
]


def _load() -> dict:
    if METAS_FILE.exists():
        return json.loads(METAS_FILE.read_text(encoding="utf-8"))
    return {"vendedores": []}


def _save(cfg: dict):
    METAS_FILE.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")


@st.cache_data(ttl=3600)
def _vendedores_oracle() -> list[str]:
    try:
        import pandas as pd
        with get_conn("crc") as conn:
            df = pd.read_sql(
                "SELECT DISTINCT NOME FROM CRC.PCUSUARI WHERE NOME LIKE '%OFF TRADE%' ORDER BY NOME",
                conn,
            )
            return df["NOME"].tolist()
    except Exception:
        return []


cfg = _load()
vendedores_existentes = {v["nome"]: v for v in cfg.get("vendedores", [])}
vendedores_oracle     = _vendedores_oracle()

st.markdown("### Selecione o vendedor e o mês")

col1, col2, col3 = st.columns([3, 2, 2])
with col1:
    nomes = vendedores_oracle or list(vendedores_existentes.keys())
    nome_sel = st.selectbox("Vendedor (nome Oracle)", nomes)
with col2:
    _meses = ["Jan", "Fev", "Mar", "Abr", "Mai", "Jun", "Jul", "Ago", "Set", "Out", "Nov", "Dez"]
    _hoje  = date.today()
    mes_idx = _hoje.month - 1
    mes_nome = st.selectbox("Mês", _meses, index=mes_idx)
with col3:
    ano = st.number_input("Ano", value=_hoje.year, min_value=2024, max_value=2030, step=1)

mes_key = f"{mes_nome}/{str(ano)[2:]}"

# Carrega valores existentes
dados_vendedor = vendedores_existentes.get(nome_sel, {})
metas_mes      = dados_vendedor.get("metas_por_mes", {}).get(mes_key, {})

st.markdown(f"### Objetivos de **{nome_sel}** — {mes_key}")

novos_valores = {}
cols = st.columns(3)
for i, (campo, label, is_brl) in enumerate(CAMPOS):
    valor_atual = metas_mes.get(campo, 0.0 if is_brl else 0)
    with cols[i % 3]:
        novos_valores[campo] = st.number_input(
            label,
            value=float(valor_atual),
            min_value=0.0,
            step=1000.0 if is_brl else 1.0,
            format="%.2f" if is_brl else "%.0f",
            key=f"{campo}_{nome_sel}_{mes_key}",
        )

if st.button("💾 Salvar objetivos", type="primary"):
    cfg = _load()
    vmap = {v["nome"]: v for v in cfg.get("vendedores", [])}

    if nome_sel not in vmap:
        vmap[nome_sel] = {"nome": nome_sel, "rca": "", "metas_por_mes": {}}

    vmap[nome_sel].setdefault("metas_por_mes", {})[mes_key] = novos_valores
    cfg["vendedores"] = list(vmap.values())
    _save(cfg)
    st.success(f"Objetivos de {nome_sel} para {mes_key} salvos com sucesso!")
    st.cache_data.clear()

st.markdown("---")
st.markdown("### Objetivos cadastrados")
if cfg.get("vendedores"):
    for v in cfg["vendedores"]:
        meses_str = ", ".join(sorted(v.get("metas_por_mes", {}).keys()))
        st.markdown(f"- **{v['nome']}** — meses: {meses_str or '(nenhum)'}")
else:
    st.info("Nenhum objetivo cadastrado ainda.")
