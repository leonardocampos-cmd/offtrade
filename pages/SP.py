"""SP — Faturamento e Positivação · Mês Atual."""
import json
from datetime import date
from pathlib import Path

import pandas as pd
import streamlit as st

from utils import (
    inject_css, page_header, section_title, require_auth,
    get_conn, fmt_brl, progress_bar, pct_badge,
)

st.set_page_config(page_title="SP", page_icon="📊", layout="wide")
inject_css()
require_auth()

page_header("Metas Off Trade SP", "Faturamento e Positivação · Mês Atual")

# ── Metas ──────────────────────────────────────────────────────────────────────

METAS_FILE = Path(__file__).parent.parent / "metas_config.json"

_MESES = ["Jan", "Fev", "Mar", "Abr", "Mai", "Jun", "Jul", "Ago", "Set", "Out", "Nov", "Dez"]
_hoje  = date.today()
_mes_key = f"{_MESES[_hoje.month - 1]}/{str(_hoje.year)[2:]}"


def _carregar_metas() -> dict[str, dict]:
    if not METAS_FILE.exists():
        return {}
    cfg = json.loads(METAS_FILE.read_text(encoding="utf-8"))
    result: dict[str, dict] = {}
    for v in cfg.get("vendedores", []):
        m = v.get("metas_por_mes", {}).get(_mes_key)
        if m:
            result[v["nome"]] = m
    return result


metas_map = _carregar_metas()

# ── Query SPON ─────────────────────────────────────────────────────────────────

_SQL_VENDAS = """
    SELECT
        M.CODCLI, C.CLIENTE,
        U.NOME      AS NOME_ORACLE,
        F.FANTASIA,
        M.DESCRICAO AS PRODUTO,
        (M.PUNIT * M.QT) AS VALOR
    FROM SPON.PCMOV M
    JOIN  SPON.PCUSUARI U  ON M.CODUSUR  = U.CODUSUR
    LEFT JOIN SPON.PCCLIENT C  ON M.CODCLI   = C.CODCLI
    JOIN  SPON.PCFORNEC F  ON M.CODFORNEC = F.CODFORNEC
    WHERE TRUNC(M.DTMOV, 'MM') = TRUNC(SYSDATE, 'MM')
      AND M.CODOPER IN ('S','SB')
      AND M.NUMNOTADEV IS NULL
      AND M.DTCANCEL  IS NULL
      AND (U.NOME LIKE '%OFF TRADE%' OR U.NOME = 'W.S')
"""


@st.cache_data(ttl=300, show_spinner="Carregando dados SP…")
def _carregar_vendas() -> pd.DataFrame:
    try:
        with get_conn("sp") as conn:
            df = pd.read_sql(_SQL_VENDAS, conn)
            df.columns  = df.columns.str.upper()
            df["VALOR"]    = pd.to_numeric(df["VALOR"],   errors="coerce").fillna(0)
            df["FANTASIA"] = df["FANTASIA"].fillna("").str.upper()
            df["PRODUTO"]  = df["PRODUTO"].fillna("").str.upper()
            df["CLIENTE"]  = df["CLIENTE"].fillna(df["CODCLI"].astype(str))
            return df
    except Exception as e:
        st.toast(f"[aviso] SP: {e}", icon="⚠️")
        return pd.DataFrame()


# ── Não positivados ────────────────────────────────────────────────────────────

@st.cache_data(ttl=300)
def _carregar_nao_pos(nomes_sp: frozenset) -> pd.DataFrame:
    mes_str   = _hoje.strftime("%m-%Y")
    xlsx_path = Path(__file__).parent.parent / f"nao_positivados_{mes_str}.xlsx"
    if not xlsx_path.exists():
        return pd.DataFrame()
    try:
        df = pd.read_excel(xlsx_path)
        df.columns = df.columns.str.upper()
        for col in ("NOME_RCA", "NOME_RCA2"):
            if col not in df.columns:
                df[col] = ""
        df["NOME_RCA"]  = df["NOME_RCA"].fillna("")
        df["NOME_RCA2"] = df["NOME_RCA2"].fillna("")
        mask = df["NOME_RCA"].isin(nomes_sp) | df["NOME_RCA2"].isin(nomes_sp)
        return df[mask].copy()
    except Exception as e:
        st.toast(f"[aviso] Não positivados: {e}", icon="⚠️")
        return pd.DataFrame()


# ── Métricas ───────────────────────────────────────────────────────────────────

def _metricas(df: pd.DataFrame) -> dict:
    if df.empty:
        return {"fat_tt": 0, "pos_tt": 0}

    def fat(mask=None):
        s = df[mask] if mask is not None else df
        return round(float(s["VALOR"].sum()), 2)

    def pos(mask=None):
        s = df[mask] if mask is not None else df
        return int(s["CODCLI"].nunique())

    return {
        "fat_tt": fat(),
        "pos_tt": pos(),
    }


def _pct(realizado, meta) -> float:
    return round(realizado / meta * 100, 1) if meta and meta > 0 else 0.0


def _linha_metrica(label: str, realizado, meta, is_brl: bool = True) -> str:
    pct   = _pct(realizado, meta)
    r_fmt = fmt_brl(realizado) if is_brl else str(int(realizado))
    m_fmt = fmt_brl(meta)      if is_brl else str(int(meta))
    return (
        f'<div style="margin-bottom:10px">'
        f'<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:3px">'
        f'<span style="font-size:.75rem;color:#94a3b8">{label}</span>'
        f'<span style="display:flex;align-items:center;gap:6px">'
        f'<span style="font-size:.75rem">{r_fmt} / {m_fmt}</span>'
        f'{pct_badge(pct)}'
        f'</span></div>'
        f'{progress_bar(pct)}'
        f'</div>'
    )


def _nao_pos_html(df: pd.DataFrame) -> str:
    cols = {
        "CLIENTE":   "Cliente",
        "BAIRROENT": "Bairro",
        "DTULTCOMP": "Últ. Compra",
    }
    headers = "".join(f'<th>{v}</th>' for k, v in cols.items() if k in df.columns)
    rows = []
    for _, r in df.iterrows():
        cells = "".join(
            f'<td>{r[k] if k in df.columns else ""}</td>'
            for k in cols
            if k in df.columns
        )
        rows.append(f"<tr>{cells}</tr>")
    body = "".join(rows)
    return (
        f'<div style="overflow-y:auto;max-height:260px;margin-top:6px">'
        f'<table class="custom-table"><thead><tr>{headers}</tr></thead>'
        f'<tbody>{body}</tbody></table></div>'
    )


# ── Render ─────────────────────────────────────────────────────────────────────

df_all       = _carregar_vendas()
nomes_oracle = sorted(df_all["NOME_ORACLE"].dropna().unique()) if not df_all.empty else []
nomes_sp_set = frozenset(nomes_oracle)

metas_eq = {
    "fat_tt": sum(metas_map.get(n, {}).get("fat_tt", 0) for n in nomes_oracle),
    "pos_tt": sum(metas_map.get(n, {}).get("pos_tt", 0) for n in nomes_oracle),
}

met_total = _metricas(df_all)
hoje_str  = _hoje.strftime("%d/%m/%Y")
pct_fat   = _pct(met_total["fat_tt"], metas_eq["fat_tt"])
pct_pos   = _pct(met_total["pos_tt"], metas_eq["pos_tt"])

section_title(f"Equipe SP — resumo do mês · {hoje_str}")

c1, c2, c3, c4 = st.columns(4)
c1.markdown(f'<div class="metric-card"><div class="metric-label">Faturamento</div><div class="metric-value">{fmt_brl(met_total["fat_tt"])}</div></div>', unsafe_allow_html=True)
c2.markdown(f'<div class="metric-card"><div class="metric-label">Meta Faturamento</div><div class="metric-value">{fmt_brl(metas_eq["fat_tt"])}</div></div>', unsafe_allow_html=True)
c3.markdown(f'<div class="metric-card"><div class="metric-label">Positivação</div><div class="metric-value">{met_total["pos_tt"]}</div></div>', unsafe_allow_html=True)
c4.markdown(f'<div class="metric-card"><div class="metric-label">Meta Positivação</div><div class="metric-value">{metas_eq["pos_tt"]}</div></div>', unsafe_allow_html=True)

st.markdown(f"""
<div style="margin:12px 0 24px">
    <div style="font-size:.72rem;color:#94a3b8;margin-bottom:4px">Faturamento SP · {pct_fat:.1f}%</div>
    {progress_bar(pct_fat)}
    <div style="font-size:.72rem;color:#94a3b8;margin:8px 0 4px">Positivação SP · {pct_pos:.1f}%</div>
    {progress_bar(pct_pos)}
</div>
""", unsafe_allow_html=True)

# ── Não positivados (carregado uma vez, filtrado por vendedor) ─────────────────

df_nao_pos = _carregar_nao_pos(nomes_sp_set)

# ── Cards por vendedor ────────────────────────────────────────────────────────

section_title("Vendedores SP")

if not nomes_oracle:
    st.info("Nenhum dado encontrado. Verifique a conexão Oracle (SPON).")

for nome_oracle in nomes_oracle:
    df_v  = df_all[df_all["NOME_ORACLE"] == nome_oracle]
    met   = _metricas(df_v)
    metas = metas_map.get(nome_oracle, {})

    nome_display = (
        nome_oracle
        .replace(" OFF TRADE SP", "")
        .replace(" OFF TRADE", "")
        .replace(" - OFF TRADE", "")
        .strip()
        .title()
    )

    fat_meta = metas.get("fat_tt", 0)
    pos_meta = metas.get("pos_tt", 0)
    pct_f    = _pct(met["fat_tt"], fat_meta)

    _icon = "🟢" if pct_f >= 80 else ("🟡" if pct_f >= 50 else "🔴")
    with st.expander(
        f"**{nome_display}** · {fmt_brl(met['fat_tt'])} · {met['pos_tt']} clientes · {_icon} {pct_f:.0f}%",
        expanded=False,
    ):
        col_esq, col_dir = st.columns([3, 2])

        with col_esq:
            st.markdown(f"""
<div class="vendor-card">
<div class="vendor-name">{nome_display}</div>
{_linha_metrica("Faturamento TT", met["fat_tt"], fat_meta)}
{_linha_metrica("Positivação TT", met["pos_tt"], pos_meta, is_brl=False)}
</div>
""".strip(), unsafe_allow_html=True)

        with col_dir:
            if not df_nao_pos.empty:
                mask_v = (
                    df_nao_pos["NOME_RCA"].eq(nome_oracle)
                    | df_nao_pos["NOME_RCA2"].eq(nome_oracle)
                )
                df_np_v = df_nao_pos[mask_v]
            else:
                df_np_v = pd.DataFrame()

            n_np      = len(df_np_v)
            cor_np    = "#ef4444" if n_np > 0 else "#22c55e"
            corpo_np  = (
                _nao_pos_html(df_np_v) if n_np > 0
                else '<div style="font-size:.8rem;color:#22c55e;margin-top:6px">Todos positivados ✓</div>'
            )
            st.markdown(f"""
<div class="card">
<div style="font-size:.7rem;color:#94a3b8;text-transform:uppercase;margin-bottom:6px">
    Não Positivados &nbsp;<span style="color:{cor_np};font-weight:700">{n_np}</span>
</div>
{corpo_np}
</div>
""".strip(), unsafe_allow_html=True)

if not metas_map:
    st.info("ℹ️ Metas SP não configuradas. Acesse **⚙️ Admin Metas** no menu lateral.")
