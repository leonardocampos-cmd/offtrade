"""
Gera metas_gerais_data.js com:
  - Faturamento por estado (vs metas fixas)
  - Faturamento por indústria (fantasia) com breakdown por estado
  - Comparativo com mesmo período do mês anterior
"""
import json
import calendar
import time
from datetime import datetime, date
from pathlib import Path

import oracledb
import pandas as pd
from sqlalchemy import create_engine
from dotenv import load_dotenv
import os

load_dotenv(Path(__file__).parent / ".env")

from urllib.parse import quote_plus

from utils import ORACLE_LIB, git_commit_push
# meta.py já chama oracledb.init_oracle_client() — importar antes evita o erro
# "Oracle Client library has already been initialized".
from meta import _com_timeout_forcado

USER     = os.environ["VPN_USER"]
PASSWORD = os.environ["VPN_PASSWORD"]
CRC_USER = os.getenv("CRC_USER",     USER)
CRC_PASS = os.getenv("CRC_PASSWORD", PASSWORD)
_CRC_DSN = os.getenv("DSN_CRC", "crc_oci")
SPON_USER = os.getenv("SPON_USER",     USER)
SPON_PASS = os.getenv("SPON_PASSWORD", PASSWORD)
_SPON_DSN = os.getenv("DSN_SP", "spon_oci")

# Bases: dsn → lista de (estado, filiais_or_None, estado_filtro_or_None)
# RJ e ES compartilham o mesmo Oracle (crc_oci), diferem pela filial.
#
# SP não vem só de SPON: existem vendedores ESTADO='SP' "escondidos" em CRC
# e CASTAS também (confirmado em 2026-08-13: 1 RCA em CRC, 1 em CASTAS —
# thekings/MGON não têm nenhum hoje, mas ficam configurados por segurança),
# e BLENDED é uma fonte adicional nova (banco BLENDED_OCI, schema BLENDED) —
# pedido do usuário: "as vendas de SP devem ser buscadas em todos os
# sistemas". GARRIDO fica de fora: não tem PCUSUARI.ESTADO preenchido em
# nenhuma linha, não dá pra distinguir SP ali sem outro sinal. Essas fontes
# extras usam "estado_filtro" (WHERE PCUSUARI.ESTADO = ...) em vez de
# filiais, porque não sabemos a filial certa dessas linhas nesses schemas —
# BLENDED nem tem ESTADO preenchido (todos os OFF TRADE lá são nulos),
# então é tratada como schema inteiro = SP, sem estado_filtro (mesma lógica
# que CASTAS/GARRIDO já usam em exportacao_meta.py: schema inteiro = uma
# empresa/região, sem precisar filtrar por estado).
BASES = [
    {"dsn": os.getenv("DSN_RJ", "crc_oci"),   "schema": "CRC",      "estado": "RJ", "filiais": ["2", "4"], "estado_filtro": None},
    {"dsn": os.getenv("DSN_ES", "crc_oci"),   "schema": "CRC",      "estado": "ES", "filiais": ["1"],      "estado_filtro": None},
    {"dsn": os.getenv("DSN_SP", "spon_oci"),  "schema": "SPON",     "estado": "SP", "filiais": ["1", "2"], "estado_filtro": None},
    {"dsn": os.getenv("DSN_MG", "mgon_oci"),  "schema": "MGON",     "estado": "MG", "filiais": ["1","2"],  "estado_filtro": None},
    {"dsn": os.getenv("DSN_RJ", "crc_oci"),        "schema": "CRC",      "estado": "SP", "filiais": None, "estado_filtro": "SP"},
    {"dsn": os.getenv("DSN_THEKING", "theking_oci"), "schema": "THEKINGS", "estado": "SP", "filiais": None, "estado_filtro": "SP"},
    {"dsn": os.getenv("DSN_CASTAS", "10.131.62.40:1576/?service_name=CASTASPRD"), "schema": "CASTAS", "estado": "SP", "filiais": None, "estado_filtro": "SP"},
    {"dsn": os.getenv("DSN_MG", "mgon_oci"),       "schema": "MGON",     "estado": "SP", "filiais": None, "estado_filtro": "SP"},
    {"dsn": os.getenv("DSN_BLENDED", "blended_oci"), "schema": "BLENDED", "estado": "SP", "filiais": None, "estado_filtro": None},
]

# RJ e SP têm meta mensal cadastrada por vendedor nas planilhas do Drive
# (METAS RJ.xlsx / METAS SP.xlsx) — a meta do estado é a soma dessas metas
# pro mês atual (ver _meta_estado_do_mes). ES e MG ainda não têm esse
# processo de meta mensal, então continuam com valor fixo.
METAS_FAT_ESTADO_FALLBACK = {
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

# Estados cuja base falhou em pelo menos uma consulta — exibido como aviso em
# metas_gerais.html via fontes_alert.js (mesmo padrão de meta.py/exportacao_meta.py).
FONTES_INDISPONIVEIS: list[str] = []


def _ler_com_retry(engine, query: str, estado: str, max_tentativas: int = 3) -> pd.DataFrame:
    def _fazer_query():
        with engine.connect() as conn:
            return pd.read_sql(query, conn)

    for tentativa in range(1, max_tentativas + 1):
        try:
            return _com_timeout_forcado(_fazer_query, 90)
        except Exception as e:
            print(f"    [tentativa {tentativa}/{max_tentativas}] {estado}: {str(e)[:100]}")
            engine.dispose()
            if tentativa < max_tentativas:
                time.sleep(10)
            else:
                raise


# ── Query agregada por indústria ───────────────────────────────────────────────

def _where(schema: str, filiais: list | None, mes_offset: int, limit_day: bool, estado_filtro: str | None = None) -> tuple[str, str, str, str]:
    p          = f"{schema}."
    ref        = "SYSDATE" if mes_offset == 0 else f"ADD_MONTHS(SYSDATE, {mes_offset})"
    fil_clause = f"AND PCMOV.CODFILIAL IN ({','.join(filiais)})" if filiais else ""
    day_clause = "AND EXTRACT(DAY FROM PCMOV.DTMOV) <= EXTRACT(DAY FROM SYSDATE)" if limit_day else ""
    est_clause = f"AND PCUSUARI.ESTADO = '{estado_filtro}'" if estado_filtro else ""
    return p, ref, fil_clause, day_clause, est_clause


def _query_industria(schema: str, filiais: list | None, mes_offset: int = 0, limit_day: bool = False, estado_filtro: str | None = None) -> str:
    p, ref, fil_clause, day_clause, est_clause = _where(schema, filiais, mes_offset, limit_day, estado_filtro)
    return f"""
        SELECT PCFORNEC.FANTASIA                     AS FANTASIA,
               SUM(PCMOV.PUNIT * PCMOV.QT)          AS FATURAMENTO,
               COUNT(DISTINCT CASE WHEN PCCLIENT.OFFTRADE = 'S' THEN PCCLIENT.CODCLI END) AS POSITIVADOS
        FROM {p}PCMOV
        JOIN {p}PCUSUARI  ON PCMOV.CODUSUR      = PCUSUARI.CODUSUR
        JOIN {p}PCPRODUT  ON PCMOV.CODPROD      = PCPRODUT.CODPROD
        JOIN {p}PCFORNEC  ON PCPRODUT.CODFORNEC = PCFORNEC.CODFORNEC
        JOIN {p}PCCLIENT  ON PCMOV.CODCLI       = PCCLIENT.CODCLI
        WHERE TRUNC(PCMOV.DTMOV, 'MM') = TRUNC({ref}, 'MM')
          {day_clause} {fil_clause} {est_clause}
          AND PCMOV.CODOPER = 'S' AND PCMOV.NUMNOTADEV IS NULL AND PCMOV.DTCANCEL IS NULL
          AND (PCUSUARI.NOME LIKE '%OFF TRADE%' OR PCUSUARI.NOME LIKE '%W.S%')
        GROUP BY PCFORNEC.FANTASIA
    """


def _query_totais(schema: str, filiais: list | None, mes_offset: int = 0, limit_day: bool = False, estado_filtro: str | None = None) -> str:
    """Totais reais (positivação sem dupla contagem entre indústrias)."""
    p, ref, fil_clause, day_clause, est_clause = _where(schema, filiais, mes_offset, limit_day, estado_filtro)
    return f"""
        SELECT SUM(PCMOV.PUNIT * PCMOV.QT)    AS FATURAMENTO,
               COUNT(DISTINCT CASE WHEN PCCLIENT.OFFTRADE = 'S' THEN PCCLIENT.CODCLI END) AS POSITIVADOS
        FROM {p}PCMOV
        JOIN {p}PCUSUARI ON PCMOV.CODUSUR = PCUSUARI.CODUSUR
        JOIN {p}PCCLIENT ON PCMOV.CODCLI  = PCCLIENT.CODCLI
        WHERE TRUNC(PCMOV.DTMOV, 'MM') = TRUNC({ref}, 'MM')
          {day_clause} {fil_clause} {est_clause}
          AND PCMOV.CODOPER = 'S' AND PCMOV.NUMNOTADEV IS NULL AND PCMOV.DTCANCEL IS NULL
          AND (PCUSUARI.NOME LIKE '%OFF TRADE%' OR PCUSUARI.NOME LIKE '%W.S%')
    """


def _carregar(mes_offset: int = 0, limit_day: bool = False) -> pd.DataFrame:
    frames  = []
    engines = {}   # cache de engines por dsn

    for cfg in BASES:
        dsn, schema, estado, filiais, estado_filtro = cfg["dsn"], cfg["schema"], cfg["estado"], cfg["filiais"], cfg.get("estado_filtro")
        if not dsn:
            print(f"  [skip] {estado}: DSN não configurado")
            continue

        if dsn not in engines:
            try:
                if dsn == _CRC_DSN:
                    _u, _p = CRC_USER, CRC_PASS
                elif dsn == _SPON_DSN:
                    _u, _p = SPON_USER, SPON_PASS
                else:
                    _u, _p = USER, PASSWORD
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
            print(f"  Consultando {estado} ({schema} filiais={filiais} estado_filtro={estado_filtro})…")
            df = _ler_com_retry(engines[dsn], _query_industria(schema, filiais, mes_offset, limit_day, estado_filtro), f"{estado}/{schema}")
            df.columns   = df.columns.str.upper()
            df["ESTADO"] = estado
            df["FATURAMENTO"] = pd.to_numeric(df["FATURAMENTO"], errors="coerce").fillna(0)
            df["POSITIVADOS"] = pd.to_numeric(df["POSITIVADOS"], errors="coerce").fillna(0).astype(int)
            df["FANTASIA"]    = df["FANTASIA"].fillna("SEM FORNECEDOR").str.strip().str.upper()
            frames.append(df)
            print(f"    OK {len(df)} indústrias, fat={df['FATURAMENTO'].sum():,.0f}")
        except Exception as e:
            print(f"  [aviso] {estado}/{schema}: falhou após retries — {str(e)[:100]} — desconsiderado, cálculo segue com os demais estados.")
            if f"{estado}/{schema}" not in FONTES_INDISPONIVEIS:
                FONTES_INDISPONIVEIS.append(f"{estado}/{schema}")

    if not frames:
        return pd.DataFrame(columns=["FANTASIA", "FATURAMENTO", "POSITIVADOS", "ESTADO"])
    return pd.concat(frames, ignore_index=True)


def _carregar_totais(mes_offset: int = 0, limit_day: bool = False) -> dict:
    """Retorna {estado: {fat, pos}} com positivação sem dupla contagem.
    Vários registros de BASES podem apartar pro mesmo estado (ex: SP vem de
    SPON + CRC + CASTAS + BLENDED) — acumula em vez de sobrescrever."""
    result  = {}
    engines = {}
    for cfg in BASES:
        dsn, schema, estado, filiais, estado_filtro = cfg["dsn"], cfg["schema"], cfg["estado"], cfg["filiais"], cfg.get("estado_filtro")
        if not dsn:
            continue
        if dsn not in engines:
            try:
                if dsn == _CRC_DSN:
                    _u, _p = CRC_USER, CRC_PASS
                elif dsn == _SPON_DSN:
                    _u, _p = SPON_USER, SPON_PASS
                else:
                    _u, _p = USER, PASSWORD
                engines[dsn] = create_engine(
                    f"oracle+oracledb://{_u}:{quote_plus(_p)}@{dsn}",
                    pool_pre_ping=True, pool_recycle=3600,
                    connect_args={"expire_time": 2},
                )
            except Exception:
                continue
        try:
            row = _ler_com_retry(engines[dsn], _query_totais(schema, filiais, mes_offset, limit_day, estado_filtro), f"{estado}/{schema}")
            row.columns = row.columns.str.upper()
            # "or 0" não pega NaN — NaN é truthy em Python (nan != 0), então
            # SUM(...) vindo NULL do Oracle (estado sem venda no período)
            # passava direto como NaN em vez de cair no fallback, e
            # propagava (sum([nan, ...]) = nan) até o total geral — bug
            # real confirmado em 2026-08-16 (metas_gerais.html mostrando
            # "Faturamento Total R$ NaN").
            _fat_num = pd.to_numeric(row["FATURAMENTO"].iloc[0], errors="coerce")
            _pos_num = pd.to_numeric(row["POSITIVADOS"].iloc[0], errors="coerce")
            fat = float(_fat_num) if pd.notna(_fat_num) else 0.0
            pos = int(_pos_num) if pd.notna(_pos_num) else 0
            acc = result.setdefault(estado, {"fat": 0.0, "pos": 0})
            acc["fat"] = round(acc["fat"] + fat, 2)
            # Positivação soma direto (não deduplica cliente entre schemas
            # diferentes) — mesmo cliente cadastrado em duas bases distintas
            # do Winthor é caso raro o bastante pra não valer a complexidade
            # de cruzar CODCLI entre schemas aqui.
            acc["pos"] = acc["pos"] + pos
        except Exception as e:
            print(f"  [aviso totais] {estado}/{schema}: falhou após retries — {str(e)[:100]} — desconsiderado, cálculo segue com os demais estados.")
            if f"{estado}/{schema}" not in FONTES_INDISPONIVEIS:
                FONTES_INDISPONIVEIS.append(f"{estado}/{schema}")
    return result


def _norm_col(s) -> str:
    import unicodedata
    return unicodedata.normalize('NFKD', str(s)).encode('ascii', 'ignore').decode().upper().strip()


def _meta_estado_do_mes(caminho_fn, fallback_path: str, mes_ym: str, fallback_valor: float) -> float:
    """Soma META FATURAMENTO de todos os vendedores no mês atual, a partir da
    planilha de metas (METAS RJ.xlsx / METAS SP.xlsx no Drive) — cai no valor
    fixo se a planilha, a coluna ou o mês não estiverem disponíveis."""
    try:
        import baixar_planilhas_drive as _bpd
        df = pd.read_excel(_bpd.com_fallback(caminho_fn, fallback_path))
        df.columns = df.columns.str.strip()
        cols_norm = {_norm_col(c): c for c in df.columns}
        col_mes  = cols_norm.get('MES')
        col_meta = cols_norm.get('META FATURAMENTO')
        if not col_mes or not col_meta:
            raise ValueError("colunas MES/META FATURAMENTO não encontradas")
        df['_MES'] = pd.to_datetime(df[col_mes], errors='coerce')
        linhas = df[df['_MES'].dt.strftime('%Y-%m') == mes_ym]
        soma = float(pd.to_numeric(linhas[col_meta], errors='coerce').sum())
        if soma <= 0:
            raise ValueError(f"sem meta cadastrada para {mes_ym}")
        return soma
    except Exception as e:
        print(f"[AVISO] meta do mês via planilha falhou ({str(e)[:100]}) — usando valor fixo de fallback.")
        return fallback_valor


# ── Carrega mês atual e anterior ───────────────────────────────────────────────

hoje = date.today()

print("\n=== Carregando meta mensal RJ/SP (planilhas do Drive) ===")
import baixar_planilhas_drive as _bpd
_mes_ym_atual = hoje.strftime('%Y-%m')
METAS_FAT_ESTADO = dict(METAS_FAT_ESTADO_FALLBACK)
METAS_FAT_ESTADO["RJ"] = _meta_estado_do_mes(
    _bpd.caminho_metas_rj, r"G:\Drives compartilhados\Off Trade\Campanhas e Metas\METAS\METAS RJ.xlsx",
    _mes_ym_atual, METAS_FAT_ESTADO_FALLBACK["RJ"],
)
METAS_FAT_ESTADO["SP"] = _meta_estado_do_mes(
    _bpd.caminho_metas_sp, r"G:\Drives compartilhados\Off Trade\Campanhas e Metas\METAS\METAS SP.xlsx",
    _mes_ym_atual, METAS_FAT_ESTADO_FALLBACK["SP"],
)
print(f"  RJ meta {METAS_FAT_ESTADO['RJ']:,.0f} · SP meta {METAS_FAT_ESTADO['SP']:,.0f}")

print("\n=== Carregando industrias (mes atual) ===")
df_atual  = _carregar(0, False)
print("\n=== Carregando industrias (mes anterior, dias corridos p/ comparativo de ritmo) ===")
df_ant    = _carregar(-1, True)
print("\n=== Carregando totais reais (mes atual) ===")
totais_atual = _carregar_totais(0, False)
print("\n=== Carregando totais reais (mes anterior, dias corridos p/ comparativo de ritmo) ===")
totais_ant   = _carregar_totais(-1, True)
print("\n=== Carregando totais reais (mes anterior FECHADO, sem corte de dia) ===")
totais_ant_completo = _carregar_totais(-1, False)


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

fat_total              = sum(v.get("fat", 0) for v in totais_atual.values())
fat_ant_total           = sum(v.get("fat", 0) for v in totais_ant.values())
fat_ant_completo_total  = sum(v.get("fat", 0) for v in totais_ant_completo.values())
pos_total              = sum(v.get("pos", 0) for v in totais_atual.values())
pos_ant_total           = sum(v.get("pos", 0) for v in totais_ant.values())
pos_ant_completo_total  = sum(v.get("pos", 0) for v in totais_ant_completo.values())
meta_total    = sum(METAS_FAT_ESTADO.values())
pct_total     = round(fat_total / meta_total * 100, 1) if meta_total else 0.0

_MESES_PT = ['Jan','Fev','Mar','Abr','Mai','Jun','Jul','Ago','Set','Out','Nov','Dez']
mes_str     = f"{_MESES_PT[hoje.month - 1]}/{str(hoje.year)[2:]}"
mes_ant_idx = (hoje.month - 2) % 12
mes_ant_ano = hoje.year if hoje.month > 1 else hoje.year - 1
mes_ant_str = f"{_MESES_PT[mes_ant_idx]}/{str(mes_ant_ano)[2:]}"

# Atualiza fat nos estados_out com valores reais de totais_atual
for e in estados_out:
    est           = e["estado"]
    real          = totais_atual.get(est, {})
    ant           = totais_ant.get(est, {})
    ant_completo  = totais_ant_completo.get(est, {})
    e["fat"]              = real.get("fat", e["fat"])
    e["fat_ant"]           = ant.get("fat",  e["fat_ant"])
    e["fat_ant_completo"]  = ant_completo.get("fat", 0)
    e["pos"]               = real.get("pos", e["pos"])
    e["pos_ant"]           = ant.get("pos",  0)
    e["pos_ant_completo"]  = ant_completo.get("pos", 0)
    meta = e["meta"]
    e["pct"]     = round(e["fat"] / meta * 100, 1) if meta else 0.0
    e["nec_dia"] = round(max(meta - e["fat"], 0) / dias_restantes, 2)

payload = {
    "atualizado_em":  datetime.now().strftime("%d/%m/%Y %H:%M"),
    "mes":            mes_str,
    "mes_ant":        mes_ant_str,
    "dias_corridos":  dias_corridos,
    "dias_no_mes":    dias_no_mes,
    "dias_restantes": dias_restantes,
    "resumo": {
        "fat":              round(fat_total, 2),
        "fat_ant":          round(fat_ant_total, 2),
        "fat_ant_completo": round(fat_ant_completo_total, 2),
        "pos":              pos_total,
        "pos_ant":          pos_ant_total,
        "pos_ant_completo": pos_ant_completo_total,
    },
    "total": {
        "meta":             meta_total,
        "fat":              round(fat_total, 2),
        "fat_ant":          round(fat_ant_total, 2),
        "fat_ant_completo": round(fat_ant_completo_total, 2),
        "pct":              pct_total,
        "nec_dia":          round(max(meta_total - fat_total, 0) / dias_restantes, 2),
    },
    "estados":    estados_out,
    "industrias": industrias_out,
    "fontes_indisponiveis": sorted(FONTES_INDISPONIVEIS),
}

out = BASE / "metas_gerais_data.js"
out.write_text(
    f"const METAS_GERAIS_DATA = {json.dumps(payload, ensure_ascii=False, indent=2)};\n",
    encoding="utf-8",
)
print(f"\nOK metas_gerais_data.js - {len(industrias_out)} industrias -> {out}")

git_commit_push(["metas_gerais_data.js"],
                f"Atualiza metas_gerais_data.js - {hoje.strftime('%d/%m/%Y')}")
