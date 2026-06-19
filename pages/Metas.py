"""Metas Off Trade RJ — consulta Oracle direto, metas de metas_config.json."""
import json, re
from datetime import date
from pathlib import Path

import pandas as pd
import streamlit as st

from utils import inject_css, page_header, section_title, require_auth, get_conn, fmt_brl, progress_bar, pct_badge

st.set_page_config(page_title="Metas Off Trade", page_icon="📊", layout="wide")
inject_css()
require_auth()

page_header("Metas Off Trade RJ", "Faturamento e Positivação · Mês Atual")

# ── Config de metas ───────────────────────────────────────────────────────────

METAS_FILE = Path(__file__).parent.parent / "metas_config.json"

def _carregar_metas() -> dict:
    if METAS_FILE.exists():
        return json.loads(METAS_FILE.read_text(encoding="utf-8"))
    return {"vendedores": []}

_MESES = ["Jan", "Fev", "Mar", "Abr", "Mai", "Jun", "Jul", "Ago", "Set", "Out", "Nov", "Dez"]
_hoje_admin = date.today()
_mes_atual_key = f"{_MESES[_hoje_admin.month - 1]}/{str(_hoje_admin.year)[2:]}"

metas_cfg = _carregar_metas()
metas_map: dict[str, dict] = {}
for v in metas_cfg.get("vendedores", []):
    meses = v.get("metas_por_mes", {})
    if _mes_atual_key in meses:
        metas_map[v["nome"]] = meses[_mes_atual_key]

# ── Queries Oracle ─────────────────────────────────────────────────────────────

_FILTRO_FILIAL = "(1, 2, 4)"

def _q_vendas(schema: str, filtro_filial: str | None = _FILTRO_FILIAL, filtro_estent: str | None = None) -> str:
    p = f"{schema.upper()}."
    extra_fil = f"\n      AND M.CODFILIAL IN {filtro_filial}" if filtro_filial else ""
    extra_est = f"\n      AND C.ESTENT = '{filtro_estent}'" if filtro_estent else ""
    join_cli  = f"\n    JOIN {p}PCCLIENT C ON M.CODCLI = C.CODCLI" if filtro_estent else f"\n    LEFT JOIN {p}PCCLIENT C ON M.CODCLI = C.CODCLI"
    return f"""
    SELECT
        M.CODCLI, C.CLIENTE,
        U.NOME    AS NOME_ORACLE,
        F.FANTASIA,
        M.DESCRICAO AS PRODUTO,
        (M.PUNIT * M.QT) AS VALOR
    FROM {p}PCMOV M
    JOIN {p}PCUSUARI U ON M.CODUSUR = U.CODUSUR{join_cli}
    JOIN {p}PCFORNEC F ON M.CODFORNEC = F.CODFORNEC
    WHERE TRUNC(M.DTMOV, 'MM') = TRUNC(SYSDATE, 'MM')
      AND M.CODOPER IN ('S','SB')
      AND M.NUMNOTADEV IS NULL
      AND M.DTCANCEL IS NULL
      AND U.NOME LIKE '%OFF TRADE%'{extra_fil}{extra_est}
    """

def _q_mapa_rca(schema: str) -> str:
    return f"SELECT CODUSUR AS RCA, NOME FROM {schema.upper()}.PCUSUARI WHERE NOME LIKE '%OFF TRADE%'"


@st.cache_data(ttl=300, show_spinner="Carregando dados Oracle…")
def _carregar_vendas() -> pd.DataFrame:
    dbs = [
        ("CRC",      "crc",     _FILTRO_FILIAL, None),
        ("thekings", "theking", _FILTRO_FILIAL, None),
        ("SPON",     "sp",      None,           None),
    ]
    partes = []
    for schema, db_key, fil, est in dbs:
        try:
            with get_conn(db_key) as conn:
                df = pd.read_sql(_q_vendas(schema, fil, est), conn)
                df.columns = df.columns.str.upper()
                partes.append(df)
        except Exception as e:
            st.toast(f"[aviso] {schema}: {e}", icon="⚠️")

    if not partes:
        return pd.DataFrame()

    df = pd.concat(partes, ignore_index=True)
    df["VALOR"]    = pd.to_numeric(df["VALOR"],    errors="coerce").fillna(0)
    df["FANTASIA"] = df["FANTASIA"].fillna("").str.upper()
    df["PRODUTO"]  = df["PRODUTO"].fillna("").str.upper()
    df["CLIENTE"]  = df["CLIENTE"].fillna(df["CODCLI"].astype(str))
    return df


@st.cache_data(ttl=3600)
def _mapa_rca() -> dict[str, str]:
    dbs = [("CRC", "crc"), ("thekings", "theking"), ("SPON", "sp")]
    partes = []
    for schema, db_key in dbs:
        try:
            with get_conn(db_key) as conn:
                df = pd.read_sql(_q_mapa_rca(schema), conn)
                df.columns = df.columns.str.upper()
                partes.append(df)
        except Exception:
            pass
    if not partes:
        return {}
    df = pd.concat(partes, ignore_index=True).drop_duplicates(subset=["RCA"])
    return dict(zip(df["NOME"].astype(str), df["NOME"].astype(str)))


# ── Cálculo de métricas ────────────────────────────────────────────────────────

def _metricas(df: pd.DataFrame) -> dict:
    if df.empty:
        return dict(fat_tt=0, pos_tt=0, fat_castas=0, fat_hob_azeite=0,
                    fat_domecq_passport=0, pos_hob_azeite=0, pos_reckit=0,
                    pos_crusoe=0, pos_tatuzinho=0, pos_redbull=0, pos_pinatti=0)

    def fat(mask=None):
        s = df[mask] if mask is not None else df
        return round(float(s["VALOR"].sum()), 2)

    def pos(mask=None):
        s = df[mask] if mask is not None else df
        return int(s["CODCLI"].nunique())

    return {
        "fat_tt":              fat(),
        "fat_castas":          fat(df["FANTASIA"].str.contains("CASTAS", na=False)),
        "fat_hob_azeite":      fat(df["PRODUTO"].str.contains("AZEITE", na=False) | df["FANTASIA"].str.contains("HOB", na=False)),
        "fat_domecq_passport": fat(df["PRODUTO"].str.contains("DOMECQ|PASSPORT", na=False)),
        "pos_tt":              pos(),
        "pos_hob_azeite":      pos(df["PRODUTO"].str.contains("AZEITE", na=False) | df["FANTASIA"].str.contains("HOB", na=False)),
        "pos_reckit":          pos(df["FANTASIA"].str.contains("RECKIT", na=False)),
        "pos_crusoe":          pos(df["FANTASIA"].str.contains("ROBINSON CRUSOE", na=False)),
        "pos_tatuzinho":       pos(df["FANTASIA"].str.contains("TATUZINHO", na=False)),
        "pos_redbull":         pos(df["FANTASIA"].str.contains("RED BULL", na=False)),
        "pos_pinatti":         pos(df["FANTASIA"].str.contains("PINATI", na=False)),
    }


def _pct(realizado, meta) -> float:
    return round(realizado / meta * 100, 1) if meta and meta > 0 else 0.0


def _linha_metrica(label: str, realizado, meta, is_brl: bool = True):
    pct  = _pct(realizado, meta)
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


# ── Render ─────────────────────────────────────────────────────────────────────

df_all = _carregar_vendas()

# Totais globais
met_total = _metricas(df_all)
metas_eq  = {
    "fat_tt": sum(v.get("fat_tt", 0) for v in metas_map.values()),
    "pos_tt": sum(v.get("pos_tt", 0) for v in metas_map.values()),
}

hoje_str = date.today().strftime("%d/%m/%Y")
pct_fat  = _pct(met_total["fat_tt"], metas_eq["fat_tt"])
pct_pos  = _pct(met_total["pos_tt"], metas_eq["pos_tt"])

section_title(f"Equipe — resumo do mês · {hoje_str}")

c1, c2, c3, c4 = st.columns(4)
c1.markdown(f'<div class="metric-card"><div class="metric-label">Faturamento</div><div class="metric-value">{fmt_brl(met_total["fat_tt"])}</div></div>', unsafe_allow_html=True)
c2.markdown(f'<div class="metric-card"><div class="metric-label">Meta Faturamento</div><div class="metric-value">{fmt_brl(metas_eq["fat_tt"])}</div></div>', unsafe_allow_html=True)
c3.markdown(f'<div class="metric-card"><div class="metric-label">Positivação</div><div class="metric-value">{met_total["pos_tt"]}</div></div>', unsafe_allow_html=True)
c4.markdown(f'<div class="metric-card"><div class="metric-label">Meta Positivação</div><div class="metric-value">{metas_eq["pos_tt"]}</div></div>', unsafe_allow_html=True)

st.markdown(f"""
<div style="margin:12px 0 24px">
    <div style="font-size:.72rem;color:#94a3b8;margin-bottom:4px">Faturamento da equipe · {pct_fat:.1f}%</div>
    {progress_bar(pct_fat)}
    <div style="font-size:.72rem;color:#94a3b8;margin:8px 0 4px">Positivação da equipe · {pct_pos:.1f}%</div>
    {progress_bar(pct_pos)}
</div>
""", unsafe_allow_html=True)

# ── Cards por vendedor ────────────────────────────────────────────────────────

section_title("Vendedores")

nomes_oracle = sorted(df_all["NOME_ORACLE"].dropna().unique()) if not df_all.empty else []

if not nomes_oracle and not metas_map:
    st.info("Nenhum dado encontrado. Verifique a conexão Oracle e o arquivo metas_config.json.")

for nome_oracle in nomes_oracle:
    df_v   = df_all[df_all["NOME_ORACLE"] == nome_oracle]
    met    = _metricas(df_v)
    metas  = metas_map.get(nome_oracle, {})
    nome_display = nome_oracle.replace(" OFF TRADE", "").replace(" - OFF TRADE", "").strip().title()

    fat_meta = metas.get("fat_tt", 0)
    pos_meta = metas.get("pos_tt", 0)
    pct_f    = _pct(met["fat_tt"], fat_meta)
    pct_p    = _pct(met["pos_tt"], pos_meta)

    _icon = "🟢" if pct_f >= 80 else ("🟡" if pct_f >= 50 else "🔴")
    with st.expander(f"**{nome_display}** · {fmt_brl(met['fat_tt'])} · {met['pos_tt']} clientes · {_icon} {pct_f:.0f}%", expanded=False):
        st.markdown(f"""
<div class="vendor-card">
<div class="vendor-name">{nome_display}</div>
{_linha_metrica("Faturamento TT",          met["fat_tt"],              fat_meta)}
{_linha_metrica("Positivação TT",          met["pos_tt"],              pos_meta,             is_brl=False)}
{_linha_metrica("Fat. Castas",             met["fat_castas"],          metas.get("fat_castas", 0))}
{_linha_metrica("Fat. HOB + Azeite",       met["fat_hob_azeite"],      metas.get("fat_hob_azeite", 0))}
{_linha_metrica("Fat. Domeq + Passport",   met["fat_domecq_passport"], metas.get("fat_domecq_passport", 0))}
{_linha_metrica("Pos. HOB + Azeite",       met["pos_hob_azeite"],      metas.get("pos_hob_azeite", 0),    is_brl=False)}
{_linha_metrica("Pos. Reckit",             met["pos_reckit"],          metas.get("pos_reckit", 0),        is_brl=False)}
{_linha_metrica("Pos. Crusoe",             met["pos_crusoe"],          metas.get("pos_crusoe", 0),        is_brl=False)}
{_linha_metrica("Pos. Tatuzinho",          met["pos_tatuzinho"],       metas.get("pos_tatuzinho", 0),     is_brl=False)}
{_linha_metrica("Pos. Red Bull",           met["pos_redbull"],         metas.get("pos_redbull", 0),       is_brl=False)}
</div>
""".strip(), unsafe_allow_html=True)

# ── Sem metas configuradas ────────────────────────────────────────────────────

if not metas_map:
    st.info("ℹ️ Metas não configuradas. Acesse **⚙️ Admin Metas** no menu lateral para cadastrar as metas do mês.")
