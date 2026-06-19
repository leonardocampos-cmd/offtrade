import os
import unicodedata
import calendar
from pathlib import Path
from datetime import datetime

import pandas as pd
import oracledb
from sqlalchemy import create_engine
from dotenv import load_dotenv
import streamlit as st

load_dotenv()

st.set_page_config(page_title="Metas Gerais", page_icon="📊", layout="wide")
st.markdown("""
<style>
#MainMenu {visibility: hidden;}
header[data-testid="stHeader"] {display: none;}
div.block-container {padding-top: 0.001rem;}
</style>
""", unsafe_allow_html=True)

# Oracle: usa env var, igual ao utils.py
_ORACLE_LIB   = os.getenv("ORACLE_LIB", r"C:\instantclient")
_oracle_ready = False

def _init_oracle():
    global _oracle_ready
    if not _oracle_ready:
        oracledb.init_oracle_client(lib_dir=_ORACLE_LIB)
        _oracle_ready = True

user     = os.getenv("DB_USER",      os.getenv("VPN_USER",     "vpn"))
password = os.getenv("DB_PASSWORD",  os.getenv("VPN_PASSWORD", ""))

BASES = {
    "SP": {"dsn": os.getenv("DSN_SP"), "schema": "SPON", "filiais": None},
    "RJ": {"dsn": os.getenv("DSN_RJ"), "schema": "CRC",  "filiais": ["2", "4"]},
    "MG": {"dsn": os.getenv("DSN_MG"), "schema": "MGON", "filiais": None},
    "ES": {"dsn": os.getenv("DSN_ES"), "schema": "CRC",  "filiais": ["1"]},
}

# METAS_DIR: configurável via env var para funcionar tanto no Windows quanto na VPS
_METAS_DIR_DEFAULT = r"G:\Drives compartilhados\Off Trade\Campanhas e Metas\METAS"
METAS_DIR = Path(os.getenv("METAS_DIR", _METAS_DIR_DEFAULT))

METAS_FAT_ESTADO = {
    "RJ": 3_900_000.0,
    "ES": 1_800_000.0,
    "MG": 2_100_000.0,
    "SP": 6_600_000.0,
}

CALCULOS_META: dict = {
    "FATURAMENTO":              lambda df: df.groupby(["CODUSUR", "ESTADO"])["FATURAMENTO"].sum(),
    "POSITIVACAO":              lambda df: df.groupby(["CODUSUR", "ESTADO"])["CODCLI"].nunique(),
    "FATURAMENTO CASTAS":       lambda df: (
        df[df["FANTASIA"].str.contains("CASTAS", na=False, case=False)]
        .groupby(["CODUSUR", "ESTADO"])["FATURAMENTO"].sum()
    ),
    "FATURAMENTO AZEITE":       lambda df: (
        df[df["DESCRICAO"].str.contains("AZEITE", na=False, case=False)]
        .groupby(["CODUSUR", "ESTADO"])["FATURAMENTO"].sum()
    ),
    "POSITIVACAO HOB + AZEITE": lambda df: (
        df[
            df["FANTASIA"].str.contains("HOB", na=False, case=False) |
            df["DESCRICAO"].str.contains("AZEITE", na=False, case=False)
        ]
        .groupby(["CODUSUR", "ESTADO"])["CODCLI"].nunique()
    ),
    "POSITIVACAO RECKIT":       lambda df: (
        df[df["FANTASIA"].str.contains("RECKITT", na=False, case=False)]
        .groupby(["CODUSUR", "ESTADO"])["CODCLI"].nunique()
    ),
    "POSITIVACAO TIAL":         lambda df: (
        df[df["FANTASIA"].str.contains("TIAL", na=False, case=False)]
        .groupby(["CODUSUR", "ESTADO"])["CODCLI"].nunique()
    ),
    "POSITIVACAO TATUZINHO":    lambda df: (
        df[df["FANTASIA"].str.contains("TATUZINHO", na=False, case=False)]
        .groupby(["CODUSUR", "ESTADO"])["CODCLI"].nunique()
    ),
    "POSITIVACAO RED BULL":     lambda df: (
        df[df["FANTASIA"].str.contains("RED BULL", na=False, case=False)]
        .groupby(["CODUSUR", "ESTADO"])["CODCLI"].nunique()
    ),
    "POSITIVACAO PINATTI":      lambda df: (
        df[df["FANTASIA"].str.contains("PINATI", na=False, case=False)]
        .groupby(["CODUSUR", "ESTADO"])["CODCLI"].nunique()
    ),
    "FATURAMENTO PERNOD":       lambda df: (
        df[df["FANTASIA"].str.contains("PERNOD", na=False, case=False)]
        .groupby(["CODUSUR", "ESTADO"])["FATURAMENTO"].sum()
    ),
    "FATURAMENTO CRS":          lambda df: (
        df[df["FANTASIA"].str.contains("CRS BRANDS", na=False, case=False)]
        .groupby(["CODUSUR", "ESTADO"])["FATURAMENTO"].sum()
    ),
    "FATURAMENTO ESSENZA":      lambda df: (
        df[df["DESCRICAO"].str.contains("ESSENZA", na=False, case=False)]
        .groupby(["CODUSUR", "ESTADO"])["FATURAMENTO"].sum()
    ),
}


# ── Helpers ────────────────────────────────────────────────────────────────────

def brl(val: float) -> str:
    return f"R$ {val:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def norm_cols(df: pd.DataFrame) -> pd.DataFrame:
    def _n(s):
        return unicodedata.normalize("NFKD", str(s)).encode("ascii", "ignore").decode().strip().upper()
    df.columns = [_n(c) for c in df.columns]
    return df


def is_fat(tipo: str) -> bool:
    return "FATURAMENTO" in tipo or tipo.startswith("FAT ")


# ── Consulta Oracle ────────────────────────────────────────────────────────────

def build_query(schema: str, mes_offset: int = 0, limit_day: bool = False) -> str:
    ref = "SYSDATE" if mes_offset == 0 else f"ADD_MONTHS(SYSDATE, {mes_offset})"
    day_filter = "AND EXTRACT(DAY FROM PCMOV.DTMOV) <= EXTRACT(DAY FROM SYSDATE)" if limit_day else ""
    return f"""
        SELECT PCMOV.CODUSUR            AS CODUSUR,
               PCMOV.CODFILIAL          AS CODFILIAL,
               PCUSUARI.NOME            AS NOME,
               PCCLIENT.CODCLI          AS CODCLI,
               PCCLIENT.CLIENTE         AS CLIENTE,
               PCCLIENT.ESTENT          AS UF_CLIENTE,
               PCFORNEC.FANTASIA        AS FANTASIA,
               PCPRODUT.DESCRICAO       AS DESCRICAO,
               (PCMOV.PUNIT * PCMOV.QT) AS FATURAMENTO
        FROM {schema}.PCMOV
        JOIN {schema}.PCUSUARI ON PCMOV.CODUSUR   = PCUSUARI.CODUSUR
        JOIN {schema}.PCPRODUT ON PCMOV.CODPROD    = PCPRODUT.CODPROD
        JOIN {schema}.PCFORNEC ON PCMOV.CODFORNEC  = PCFORNEC.CODFORNEC
        JOIN {schema}.PCCLIENT ON PCMOV.CODCLI     = PCCLIENT.CODCLI
        WHERE TRUNC(PCMOV.DTMOV, 'MM') = TRUNC({ref}, 'MM')
          {day_filter}
          AND PCMOV.CODOPER    = 'S'
          AND PCMOV.NUMNOTADEV IS NULL
          AND PCMOV.DTCANCEL   IS NULL
          AND (PCUSUARI.NOME LIKE '%OFF TRADE%' OR PCUSUARI.NOME LIKE '%W.S%')
    """


def _query_vendas(mes_offset: int = 0, limit_day: bool = False) -> pd.DataFrame:
    frames = []
    seen: dict = {}
    for estado, cfg in BASES.items():
        dsn, schema, filiais = cfg["dsn"], cfg["schema"], cfg["filiais"]
        if not dsn:
            continue
        if dsn not in seen:
            try:
                _init_oracle()
                engine = create_engine(f"oracle+oracledb://{user}:{password}@{dsn}")
                df_raw = pd.read_sql(build_query(schema, mes_offset, limit_day), con=engine, dtype=str)
                df_raw.columns = df_raw.columns.str.upper()
                seen[dsn] = df_raw
            except Exception as e:
                st.warning(f"⚠️ Erro ao carregar **{estado}**: {e}")
                continue
        df = seen[dsn].copy()
        if filiais:
            codfilial_norm = df["CODFILIAL"].str.split(".").str[0].str.strip()
            df = df[codfilial_norm.isin(filiais)]
        df["ESTADO"] = estado
        frames.append(df)
    if not frames:
        return pd.DataFrame()
    result = pd.concat(frames, ignore_index=True)
    result["FATURAMENTO"] = pd.to_numeric(result["FATURAMENTO"], errors="coerce").fillna(0)
    result["CODUSUR"]     = result["CODUSUR"].str.split(".").str[0].str.strip()
    return result


@st.cache_data(ttl=300, show_spinner="Carregando vendas...")
def carregar_vendas() -> pd.DataFrame:
    return _query_vendas(0)


@st.cache_data(ttl=3600, show_spinner="Carregando mês anterior...")
def carregar_vendas_anterior() -> pd.DataFrame:
    return _query_vendas(-1, limit_day=True)


# ── Metas Excel ────────────────────────────────────────────────────────────────

@st.cache_data(ttl=600, show_spinner="Carregando metas...")
def carregar_metas_excel(mes: int, ano: int) -> pd.DataFrame:
    frames = []

    rj_path = METAS_DIR / "METAS RJ.xlsx"
    if rj_path.exists():
        try:
            df = norm_cols(pd.read_excel(rj_path, sheet_name=0))
            id_cols   = [c for c in ["VENDEDOR", "RCA", "MES"] if c in df.columns]
            meta_cols = [c for c in df.columns if c.startswith("META ")]
            df_long = df.melt(id_vars=id_cols, value_vars=meta_cols,
                              var_name="TIPO_META", value_name="VALOR")
            df_long["TIPO_META"] = df_long["TIPO_META"].str.replace("META ", "", regex=False).str.strip()
            df_long = df_long.rename(columns={"VENDEDOR": "NOME", "RCA": "CODUSUR", "MES": "DATA_META"})
            df_long["ESTADO"] = "RJ"
            frames.append(df_long)
        except Exception as e:
            st.warning(f"Erro METAS RJ: {e}")

    sp_path = METAS_DIR / "METAS SP.xlsx"
    if sp_path.exists():
        try:
            df = norm_cols(pd.read_excel(sp_path, sheet_name=0))
            meta_cols = [c for c in df.columns if c.startswith("META ")]
            id_cols   = [c for c in df.columns if c not in meta_cols]
            date_col  = next((c for c in id_cols if c in ("MES", "MS")), None)
            name_col  = next((c for c in id_cols if c in ("NOME", "VENDEDOR")), None)
            melt_id   = ([name_col] if name_col else []) + \
                        (["RCA"] if "RCA" in df.columns else []) + \
                        ([date_col] if date_col else [])
            df_long = df.melt(id_vars=melt_id, value_vars=meta_cols,
                              var_name="TIPO_META", value_name="VALOR")
            df_long["TIPO_META"] = df_long["TIPO_META"].str.replace("META ", "", regex=False).str.strip()
            rename_map = {}
            if name_col and name_col != "NOME":
                rename_map[name_col] = "NOME"
            if "RCA" in df.columns:
                rename_map["RCA"] = "CODUSUR"
            if date_col:
                rename_map[date_col] = "DATA_META"
            df_long = df_long.rename(columns=rename_map)
            df_long["ESTADO"] = "SP"
            frames.append(df_long)
        except Exception as e:
            st.warning(f"Erro METAS SP: {e}")

    if not frames:
        return pd.DataFrame()

    df_all = pd.concat(frames, ignore_index=True)
    if "DATA_META" in df_all.columns:
        df_all["DATA_META"] = pd.to_datetime(df_all["DATA_META"], errors="coerce")
        df_all = df_all[
            (df_all["DATA_META"].dt.month == mes) &
            (df_all["DATA_META"].dt.year  == ano)
        ]
    df_all["VALOR"]   = pd.to_numeric(df_all["VALOR"], errors="coerce")
    df_all["NOME"]    = df_all["NOME"].str.strip()
    df_all["CODUSUR"] = df_all["CODUSUR"].astype(str).str.split(".").str[0].str.strip()
    df_all = df_all[~df_all["TIPO_META"].str.startswith("PESO")]
    return df_all.dropna(subset=["VALOR"]).reset_index(drop=True)


# ── Carrega dados ──────────────────────────────────────────────────────────────

hoje        = datetime.today()
df          = carregar_vendas()
df_anterior = carregar_vendas_anterior()
df_metas    = carregar_metas_excel(hoje.month, hoje.year)

if df.empty:
    st.error("Nenhum dado de vendas disponível.")
    st.stop()

titulo = st.empty()

# ── Filtros sidebar ────────────────────────────────────────────────────────────

with st.sidebar:
    st.header("Filtros")
    estados_disp = sorted(df["ESTADO"].unique())
    estados_sel  = st.multiselect("Estado", estados_disp, default=estados_disp)

    df_est          = df[df["ESTADO"].isin(estados_sel)]
    vendedores_disp = sorted(df_est["NOME"].unique())
    vendedores_sel  = st.multiselect("Vendedor", vendedores_disp, default=vendedores_disp)

df_f          = df_est[df_est["NOME"].isin(vendedores_sel)]
estado_label  = " | ".join(estados_sel) if estados_sel else "—"
titulo.markdown(f"## Metas — Off Trade | {estado_label}")

# ── Helpers de cálculo ─────────────────────────────────────────────────────────

def calcular_reais(df_vendas: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for tipo, fn in CALCULOS_META.items():
        try:
            serie = fn(df_vendas)
            real  = serie.reset_index()
            real.columns = ["CODUSUR", "ESTADO", "REAL"]
            real["TIPO_META"] = tipo
            rows.append(real)
        except Exception:
            pass
    return pd.concat(rows, ignore_index=True) if rows else pd.DataFrame()


def cor_pct(val):
    if val >= 100:
        return "background-color: #c6efce; color: #276221"
    elif val >= 70:
        return "background-color: #ffeb9c; color: #9c6500"
    return "background-color: #ffc7ce; color: #9c0006"


def card_html(tipo, meta, real, mes_ant, pct, nec, currency=None):
    fmt = brl if (currency if currency is not None else is_fat(tipo)) else lambda v: f"{int(v):,}"
    bar = min(pct, 100)
    if pct >= 100:
        border, accent, pct_cls = "#10b981", "#10b981", "#34d399"
    elif pct >= 70:
        border, accent, pct_cls = "#f59e0b", "#f59e0b", "#fbbf24"
    else:
        border, accent, pct_cls = "#ef4444", "#ef4444", "#f87171"
    ant_cor = "#f87171" if mes_ant > real else "#34d399"
    return f"""
    <div style="border:1px solid {border}55;border-radius:8px;
                padding:12px 10px;margin-bottom:2px;">
        <div style="font-size:10px;font-weight:700;text-transform:uppercase;
                    letter-spacing:.6px;color:{accent};margin-bottom:6px;">{tipo}</div>
        <div style="display:flex;justify-content:space-between;align-items:baseline;margin-bottom:2px;">
            <span style="color:#ccc;font-size:11px;">Real</span>
            <span style="display:flex;align-items:center;gap:6px;">
                <span style="color:#fff;font-size:13px;font-weight:700;">{fmt(real)}</span>
                <span style="background:{border};color:#000;font-size:10px;font-weight:700;
                             border-radius:4px;padding:1px 5px;">{pct:.1f}%</span>
            </span>
        </div>
        <div style="display:flex;justify-content:space-between;margin-bottom:2px;">
            <span style="color:#fff;font-size:10px;">Meta {fmt(meta)}</span>
            <span style="color:{ant_cor};font-size:10px;font-weight:600;">Ant. {fmt(mes_ant)}</span>
        </div>
        <div style="background:#ffffff18;border-radius:4px;height:4px;margin:6px 0;">
            <div style="background:{accent};width:{bar:.1f}%;height:4px;border-radius:4px;"></div>
        </div>
        <div style="color:{pct_cls};font-size:10px;font-weight:600;text-align:right;">
            Nec./dia: {fmt(nec)}
        </div>
    </div>"""


# ── Dias restantes no mês ──────────────────────────────────────────────────────

dias_no_mes    = calendar.monthrange(hoje.year, hoje.month)[1]
dias_restantes = max(dias_no_mes - hoje.day, 1)

# ── Comparativo (metas × realizado) ───────────────────────────────────────────

cmp = pd.DataFrame()
if not df_metas.empty:
    reais        = calcular_reais(df_f)
    codusur_sel  = df_f["CODUSUR"].unique()

    df_f_ant  = df_anterior[
        df_anterior["ESTADO"].isin(estados_sel) &
        df_anterior["CODUSUR"].isin(codusur_sel)
    ]
    reais_ant = calcular_reais(df_f_ant)

    cmp = (
        df_metas.rename(columns={"VALOR": "META"})
        .merge(reais, on=["CODUSUR", "ESTADO", "TIPO_META"], how="left")
        .merge(reais_ant.rename(columns={"REAL": "MES_ANT"}),
               on=["CODUSUR", "ESTADO", "TIPO_META"], how="left")
    )
    cmp["REAL"]       = cmp["REAL"].fillna(0)
    cmp["MES_ANT"]    = cmp["MES_ANT"].fillna(0)
    cmp["% ATINGIDO"] = (cmp["REAL"] / cmp["META"] * 100).round(1)
    cmp["NECESSARIO"] = ((cmp["META"] - cmp["REAL"]) / dias_restantes).clip(lower=0).round(1)
    cmp = cmp[cmp["ESTADO"].isin(estados_sel) & cmp["CODUSUR"].isin(codusur_sel)]
    cmp = cmp[["NOME", "ESTADO", "TIPO_META", "META", "REAL", "% ATINGIDO", "MES_ANT", "NECESSARIO"]]
    cmp = cmp.sort_values(["NOME", "TIPO_META"]).reset_index(drop=True)


# ── Cards: faturamento por estado ──────────────────────────────────────────────

fat_estado     = df_f.groupby("ESTADO")["FATURAMENTO"].sum()
fat_ant_estado = (
    df_anterior[df_anterior["ESTADO"].isin(estados_sel)]
    .groupby("ESTADO")["FATURAMENTO"].sum()
)
ESTADO_LABEL = {
    "RJ": "RIO DE JANEIRO", "ES": "ESPÍRITO SANTO",
    "MG": "MINAS GERAIS",   "SP": "SÃO PAULO",
}

with st.expander("Faturamento Total por Estado", expanded=True):
    estados_cards = [e for e in estados_sel if e in METAS_FAT_ESTADO]
    if estados_cards:
        cols_fat = st.columns(len(estados_cards))
        for j, estado in enumerate(estados_cards):
            meta_est = METAS_FAT_ESTADO[estado]
            real_est = fat_estado.get(estado, 0.0)
            ant_est  = fat_ant_estado.get(estado, 0.0)
            pct_est  = (real_est / meta_est * 100) if meta_est else 0.0
            nec_est  = max((meta_est - real_est) / dias_restantes, 0.0)
            cols_fat[j].markdown(
                card_html(ESTADO_LABEL.get(estado, estado), meta_est, real_est,
                          ant_est, pct_est, nec_est, currency=True),
                unsafe_allow_html=True,
            )
    else:
        st.info("Nenhum estado com meta de faturamento selecionado.")

st.divider()

# ── Cards: resumo por tipo de meta ─────────────────────────────────────────────

if not cmp.empty:
    totais = (
        cmp.groupby("TIPO_META")
        .agg(META=("META", "sum"), REAL=("REAL", "sum"), MES_ANT=("MES_ANT", "sum"))
        .reset_index()
    )
    totais["PCT"]        = (totais["REAL"] / totais["META"] * 100).round(1)
    totais["NECESSARIO"] = ((totais["META"] - totais["REAL"]) / dias_restantes).clip(lower=0).round(1)

    with st.expander("Resumo por Tipo de Meta", expanded=True):
        N = 4
        for chunk_start in range(0, len(totais), N):
            chunk = totais.iloc[chunk_start:chunk_start + N]
            cols  = st.columns(N)
            for j, (_, r) in enumerate(chunk.iterrows()):
                cols[j].markdown(
                    card_html(r["TIPO_META"], r["META"], r["REAL"],
                              r["MES_ANT"], r["PCT"], r["NECESSARIO"]),
                    unsafe_allow_html=True,
                )
            st.markdown("<div style='margin-bottom:8px'></div>", unsafe_allow_html=True)

st.divider()

# ── Tabela: real vs meta por vendedor ──────────────────────────────────────────

st.subheader("Real vs Meta")

if cmp.empty:
    st.info(f"Nenhum arquivo de metas encontrado em:\n`{METAS_DIR}`")
else:
    def _fmt_val(row, col):
        v = row[col]
        return brl(v) if is_fat(row["TIPO_META"]) else f"{int(v):,}"

    cmp_disp = cmp.copy()
    for _col in ["META", "REAL", "MES_ANT", "NECESSARIO"]:
        cmp_disp[_col] = cmp.apply(lambda r, c=_col: _fmt_val(r, c), axis=1)

    st.dataframe(
        cmp_disp.style
                .format({"% ATINGIDO": "{:.1f}%"})
                .map(cor_pct, subset=["% ATINGIDO"]),
        use_container_width=True,
        hide_index=True,
        column_config={
            "NOME":       st.column_config.TextColumn("Vendedor"),
            "ESTADO":     st.column_config.TextColumn("Estado"),
            "TIPO_META":  st.column_config.TextColumn("Tipo de Meta"),
            "META":       st.column_config.TextColumn("Meta"),
            "REAL":       st.column_config.TextColumn("Realizado"),
            "% ATINGIDO": st.column_config.NumberColumn("% Atingido", format="%.1f%%"),
            "MES_ANT":    st.column_config.TextColumn("Mês Anterior"),
            "NECESSARIO": st.column_config.TextColumn("Nec./Dia"),
        },
    )

st.divider()

# ── Tabela: metas por tipo ─────────────────────────────────────────────────────

if not df_metas.empty:
    st.subheader("Metas por Tipo")

    tipos_disp = sorted(df_metas["TIPO_META"].unique())
    tipos_sel  = st.multiselect("Tipo de Meta", tipos_disp, default=tipos_disp,
                                label_visibility="collapsed")

    df_metas_f = df_metas[
        df_metas["TIPO_META"].isin(tipos_sel) &
        df_metas["ESTADO"].isin(estados_sel) &
        df_metas["NOME"].isin(vendedores_sel)
    ]

    pivot_metas = df_metas_f.pivot_table(
        index="NOME", columns="TIPO_META", values="VALOR", aggfunc="sum"
    )
    st.dataframe(pivot_metas.style.format("{:,.0f}"), use_container_width=True)
