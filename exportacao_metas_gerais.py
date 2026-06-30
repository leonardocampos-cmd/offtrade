"""
Gera metas_gerais_data.js com:
  - Faturamento por estado (vs metas fixas)
  - Faturamento por indústria (fantasia) com breakdown por estado
  - Comparativo com mesmo período do mês anterior
"""
import json
import calendar
import subprocess
from datetime import datetime, date
from pathlib import Path

import oracledb
import pandas as pd
from sqlalchemy import create_engine
from dotenv import load_dotenv
import os

load_dotenv(Path(__file__).parent / ".env")

from urllib.parse import quote_plus

oracledb.init_oracle_client(lib_dir=r"C:\instantclient")

USER     = os.getenv("VPN_USER",     "vpn")
PASSWORD = os.getenv("VPN_PASSWORD", "vpn2320vpn")
CRC_USER = os.getenv("CRC_USER",     USER)
CRC_PASS = os.getenv("CRC_PASSWORD", PASSWORD)
_CRC_DSN = os.getenv("DSN_CRC", "crc_oci")

# Bases: dsn → lista de (estado, filiais_or_None)
# RJ e ES compartilham o mesmo Oracle (crc_oci), diferem pela filial
BASES = [
    {"dsn": os.getenv("DSN_RJ", "crc_oci"),   "schema": "CRC",  "estado": "RJ", "filiais": ["2", "4"]},
    {"dsn": os.getenv("DSN_ES", "crc_oci"),   "schema": "CRC",  "estado": "ES", "filiais": ["1"]},
    {"dsn": os.getenv("DSN_SP", "spon_oci"),  "schema": "SPON", "estado": "SP", "filiais": ["1", "2"]},
    {"dsn": os.getenv("DSN_MG", "mgon_oci"),  "schema": "MGON", "estado": "MG", "filiais": ["1","2"]},
]

METAS_FAT_ESTADO = {
    "RJ": 3_900_000.0,
    "SP": 6_600_000.0,
    "ES": 1_800_000.0,
    "MG": 2_100_000.0,
}

ESTADO_LABEL = {
    "RJ": "Rio de Janeiro",
    "SP": "São Paulo",
    "ES": "Espírito Santo",
    "MG": "Minas Gerais",
}

BASE = Path(__file__).parent


# ── Query agregada por indústria ───────────────────────────────────────────────

def _where(schema: str, filiais: list | None, mes_offset: int, limit_day: bool) -> tuple[str, str, str]:
    p          = f"{schema}."
    ref        = "SYSDATE" if mes_offset == 0 else f"ADD_MONTHS(SYSDATE, {mes_offset})"
    fil_clause = f"AND PCMOV.CODFILIAL IN ({','.join(filiais)})" if filiais else ""
    day_clause = "AND EXTRACT(DAY FROM PCMOV.DTMOV) <= EXTRACT(DAY FROM SYSDATE)" if limit_day else ""
    return p, ref, fil_clause, day_clause


def _query_industria(schema: str, filiais: list | None, mes_offset: int = 0, limit_day: bool = False) -> str:
    p, ref, fil_clause, day_clause = _where(schema, filiais, mes_offset, limit_day)
    return f"""
        SELECT PCFORNEC.FANTASIA                     AS FANTASIA,
               SUM(PCMOV.PUNIT * PCMOV.QT)          AS FATURAMENTO,
               COUNT(DISTINCT PCCLIENT.CODCLI)       AS POSITIVADOS
        FROM {p}PCMOV
        JOIN {p}PCUSUARI  ON PCMOV.CODUSUR      = PCUSUARI.CODUSUR
        JOIN {p}PCPRODUT  ON PCMOV.CODPROD      = PCPRODUT.CODPROD
        JOIN {p}PCFORNEC  ON PCPRODUT.CODFORNEC = PCFORNEC.CODFORNEC
        JOIN {p}PCCLIENT  ON PCMOV.CODCLI       = PCCLIENT.CODCLI
        WHERE TRUNC(PCMOV.DTMOV, 'MM') = TRUNC({ref}, 'MM')
          {day_clause} {fil_clause}
          AND PCMOV.CODOPER = 'S' AND PCMOV.NUMNOTADEV IS NULL AND PCMOV.DTCANCEL IS NULL
          AND (PCUSUARI.NOME LIKE '%OFF TRADE%' OR PCUSUARI.NOME LIKE '%W.S%')
        GROUP BY PCFORNEC.FANTASIA
    """


def _query_totais(schema: str, filiais: list | None, mes_offset: int = 0, limit_day: bool = False) -> str:
    """Totais reais (positivação sem dupla contagem entre indústrias)."""
    p, ref, fil_clause, day_clause = _where(schema, filiais, mes_offset, limit_day)
    return f"""
        SELECT SUM(PCMOV.PUNIT * PCMOV.QT)    AS FATURAMENTO,
               COUNT(DISTINCT PCCLIENT.CODCLI) AS POSITIVADOS
        FROM {p}PCMOV
        JOIN {p}PCUSUARI ON PCMOV.CODUSUR = PCUSUARI.CODUSUR
        JOIN {p}PCCLIENT ON PCMOV.CODCLI  = PCCLIENT.CODCLI
        WHERE TRUNC(PCMOV.DTMOV, 'MM') = TRUNC({ref}, 'MM')
          {day_clause} {fil_clause}
          AND PCMOV.CODOPER = 'S' AND PCMOV.NUMNOTADEV IS NULL AND PCMOV.DTCANCEL IS NULL
          AND (PCUSUARI.NOME LIKE '%OFF TRADE%' OR PCUSUARI.NOME LIKE '%W.S%')
    """


def _carregar(mes_offset: int = 0, limit_day: bool = False) -> pd.DataFrame:
    frames  = []
    engines = {}   # cache de engines por dsn

    for cfg in BASES:
        dsn, schema, estado, filiais = cfg["dsn"], cfg["schema"], cfg["estado"], cfg["filiais"]
        if not dsn:
            print(f"  [skip] {estado}: DSN não configurado")
            continue

        if dsn not in engines:
            try:
                _u = CRC_USER if dsn == _CRC_DSN else USER
                _p = CRC_PASS if dsn == _CRC_DSN else PASSWORD
                engines[dsn] = create_engine(
                    f"oracle+oracledb://{_u}:{quote_plus(_p)}@{dsn}",
                    pool_pre_ping=True,
                    pool_recycle=3600,
                    connect_args={"expire_time": 2},
                )
            except Exception as e:
                print(f"  [erro] {estado}: engine falhou — {e}")
                continue

        try:
            print(f"  Consultando {estado} ({schema} filiais={filiais})…")
            with engines[dsn].connect() as conn:
                df = pd.read_sql(_query_industria(schema, filiais, mes_offset, limit_day), conn)
            df.columns   = df.columns.str.upper()
            df["ESTADO"] = estado
            df["FATURAMENTO"] = pd.to_numeric(df["FATURAMENTO"], errors="coerce").fillna(0)
            df["POSITIVADOS"] = pd.to_numeric(df["POSITIVADOS"], errors="coerce").fillna(0).astype(int)
            df["FANTASIA"]    = df["FANTASIA"].fillna("SEM FORNECEDOR").str.strip().str.upper()
            frames.append(df)
            print(f"    OK {len(df)} indústrias, fat={df['FATURAMENTO'].sum():,.0f}")
        except Exception as e:
            print(f"  [aviso] {estado}: {e}")

    if not frames:
        return pd.DataFrame(columns=["FANTASIA", "FATURAMENTO", "POSITIVADOS", "ESTADO"])
    return pd.concat(frames, ignore_index=True)


def _carregar_totais(mes_offset: int = 0, limit_day: bool = False) -> dict:
    """Retorna {estado: {fat, pos}} com positivação sem dupla contagem."""
    result  = {}
    engines = {}
    for cfg in BASES:
        dsn, schema, estado, filiais = cfg["dsn"], cfg["schema"], cfg["estado"], cfg["filiais"]
        if not dsn:
            continue
        if dsn not in engines:
            try:
                _u = CRC_USER if dsn == _CRC_DSN else USER
                _p = CRC_PASS if dsn == _CRC_DSN else PASSWORD
                engines[dsn] = create_engine(
                    f"oracle+oracledb://{_u}:{quote_plus(_p)}@{dsn}",
                    pool_pre_ping=True, pool_recycle=3600,
                    connect_args={"expire_time": 2},
                )
            except Exception:
                continue
        try:
            with engines[dsn].connect() as conn:
                row = pd.read_sql(_query_totais(schema, filiais, mes_offset, limit_day), conn)
            row.columns = row.columns.str.upper()
            fat = float(pd.to_numeric(row["FATURAMENTO"].iloc[0], errors="coerce") or 0)
            pos = int(pd.to_numeric(row["POSITIVADOS"].iloc[0], errors="coerce") or 0)
            result[estado] = {"fat": round(fat, 2), "pos": pos}
        except Exception as e:
            print(f"  [aviso totais] {estado}: {e}")
    return result


# ── Carrega mês atual e anterior ───────────────────────────────────────────────

hoje = date.today()
print("\n=== Carregando industrias (mes atual) ===")
df_atual  = _carregar(0, False)
print("\n=== Carregando industrias (mes anterior) ===")
df_ant    = _carregar(-1, True)
print("\n=== Carregando totais reais (mes atual) ===")
totais_atual = _carregar_totais(0, False)
print("\n=== Carregando totais reais (mes anterior) ===")
totais_ant   = _carregar_totais(-1, True)


# ── Agrega por estado ──────────────────────────────────────────────────────────

def _fat_estado(df: pd.DataFrame) -> dict:
    if df.empty:
        return {}
    return df.groupby("ESTADO")["FATURAMENTO"].sum().to_dict()

fat_atual = _fat_estado(df_atual)
fat_ant   = _fat_estado(df_ant)
# Positivação correta vem de _carregar_totais (sem dupla contagem)
pos_atual = {e: totais_atual.get(e, {}).get("pos", 0) for e in ESTADO_LABEL}

dias_no_mes    = calendar.monthrange(hoje.year, hoje.month)[1]
dias_corridos  = hoje.day
dias_restantes = max(dias_no_mes - dias_corridos, 1)

estados_out = []
for est, label in ESTADO_LABEL.items():
    meta   = METAS_FAT_ESTADO.get(est, 0)
    fat    = fat_atual.get(est, 0.0)
    ant    = fat_ant.get(est, 0.0)
    pct    = round(fat / meta * 100, 1) if meta else 0.0
    nec    = round(max(meta - fat, 0) / dias_restantes, 2)
    estados_out.append({
        "estado":   est,
        "label":    label,
        "meta":     meta,
        "fat":      round(fat, 2),
        "fat_ant":  round(ant, 2),
        "pos":      int(pos_atual.get(est, 0)),
        "pct":      pct,
        "nec_dia":  nec,
    })


# ── Agrega por indústria ───────────────────────────────────────────────────────

def _industrias(df_cur: pd.DataFrame, df_prev: pd.DataFrame) -> list:
    if df_cur.empty:
        return []

    # Faturamento atual por fantasia × estado
    piv = df_cur.pivot_table(
        index="FANTASIA", columns="ESTADO",
        values="FATURAMENTO", aggfunc="sum", fill_value=0,
    ).reset_index()
    piv["TOTAL"] = piv[[c for c in piv.columns if c != "FANTASIA"]].sum(axis=1)

    # Faturamento anterior total por fantasia
    if not df_prev.empty:
        ant_tot = df_prev.groupby("FANTASIA")["FATURAMENTO"].sum().rename("ANT")
        piv = piv.merge(ant_tot, on="FANTASIA", how="left")
        piv["ANT"] = piv["ANT"].fillna(0)
    else:
        piv["ANT"] = 0.0

    piv = piv.sort_values("TOTAL", ascending=False).head(50)

    estados_cols = [c for c in piv.columns if c in ESTADO_LABEL]
    result = []
    for _, row in piv.iterrows():
        result.append({
            "fantasia":   row["FANTASIA"],
            "fat":        round(float(row["TOTAL"]), 2),
            "fat_ant":    round(float(row["ANT"]),   2),
            "por_estado": {e: round(float(row.get(e, 0)), 2) for e in ESTADO_LABEL},
        })
    return result

industrias_out = _industrias(df_atual, df_ant)


# ── Total geral ────────────────────────────────────────────────────────────────

fat_total     = sum(v.get("fat", 0) for v in totais_atual.values())
fat_ant_total = sum(v.get("fat", 0) for v in totais_ant.values())
pos_total     = sum(v.get("pos", 0) for v in totais_atual.values())
pos_ant_total = sum(v.get("pos", 0) for v in totais_ant.values())
meta_total    = sum(METAS_FAT_ESTADO.values())
pct_total     = round(fat_total / meta_total * 100, 1) if meta_total else 0.0

mes_str = f"{['Jan','Fev','Mar','Abr','Mai','Jun','Jul','Ago','Set','Out','Nov','Dez'][hoje.month - 1]}/{str(hoje.year)[2:]}"

# Atualiza fat nos estados_out com valores reais de totais_atual
for e in estados_out:
    est  = e["estado"]
    real = totais_atual.get(est, {})
    ant  = totais_ant.get(est, {})
    e["fat"]     = real.get("fat", e["fat"])
    e["fat_ant"] = ant.get("fat",  e["fat_ant"])
    e["pos"]     = real.get("pos", e["pos"])
    e["pos_ant"] = ant.get("pos",  0)
    meta = e["meta"]
    e["pct"]     = round(e["fat"] / meta * 100, 1) if meta else 0.0
    e["nec_dia"] = round(max(meta - e["fat"], 0) / dias_restantes, 2)

payload = {
    "atualizado_em":  datetime.now().strftime("%d/%m/%Y %H:%M"),
    "mes":            mes_str,
    "dias_corridos":  dias_corridos,
    "dias_no_mes":    dias_no_mes,
    "dias_restantes": dias_restantes,
    "resumo": {
        "fat":     round(fat_total, 2),
        "fat_ant": round(fat_ant_total, 2),
        "pos":     pos_total,
        "pos_ant": pos_ant_total,
    },
    "total": {
        "meta":    meta_total,
        "fat":     round(fat_total, 2),
        "fat_ant": round(fat_ant_total, 2),
        "pct":     pct_total,
        "nec_dia": round(max(meta_total - fat_total, 0) / dias_restantes, 2),
    },
    "estados":    estados_out,
    "industrias": industrias_out,
}

out = BASE / "metas_gerais_data.js"
out.write_text(
    f"const METAS_GERAIS_DATA = {json.dumps(payload, ensure_ascii=False, indent=2)};\n",
    encoding="utf-8",
)
print(f"\nOK metas_gerais_data.js - {len(industrias_out)} industrias -> {out}")

repo_dir = str(BASE)
subprocess.run(["git", "-C", repo_dir, "add", "metas_gerais_data.js"], check=True)
subprocess.run(["git", "-C", repo_dir, "commit", "-m",
                f"Atualiza metas_gerais_data.js - {hoje.strftime('%d/%m/%Y')}"])
subprocess.run(["git", "-C", repo_dir, "push", "origin", "master"], check=True)
print("OK GitHub Pages atualizado.")
