"""
Gera estoque_data.js com a posição de estoque atual (ROTINA_105) de todas as
bases Oracle disponíveis — CRC (RJ/ES), THEKINGS, GARRIDO, SPON (SP) e MGON (MG).
CASTAS não tem ROTINA_105 (view/tabela não existe lá) e é ignorada.

Cruza com vendas dos últimos 30 dias (PCMOV) por CODFILIAL+CODPROD pra
calcular a média de venda diária e projetar a data de esgotamento do estoque
(hoje + qtestoque / média diária).
"""
import json
import subprocess
from datetime import datetime, date
from pathlib import Path

import pandas as pd
from sqlalchemy import text
from meta import engine, engine_theking, engine_garrido, engine_spon, engine_mgon, carregar_dados

BASE = Path(__file__).parent

# (rótulo, engine, schema, mapeamento de CODFILIAL -> estado/empresa exibido)
_SOURCES = [
    ("CRC",      engine,          "CRC",      {"1": "ES"}),   # demais filiais caem no default "RJ"
    ("THEKINGS", engine_theking,  "THEKINGS", {}),
    ("GARRIDO",  engine_garrido,  "GARRIDO",  {}),
    ("SPON",     engine_spon,     "SPON",     {}),
    ("MGON",     engine_mgon,     "MGON",     {}),
]

_DEFAULT_ROTULO = {"CRC": "RJ", "THEKINGS": "Thekings", "GARRIDO": "Garrido", "SPON": "SP", "MGON": "MG"}

DIAS_MEDIA_VENDA = 30  # janela pra calcular a média de venda diária


def _query_vendas(schema: str) -> str:
    return f"""
        SELECT CODFILIAL, CODPROD, SUM(QT) AS QT_VENDIDA
        FROM {schema}.PCMOV
        WHERE DTMOV >= TRUNC(SYSDATE) - {DIAS_MEDIA_VENDA}
          AND CODOPER IN ('S', 'SB')
          AND NUMNOTADEV IS NULL
          AND DTCANCEL IS NULL
        GROUP BY CODFILIAL, CODPROD
    """


def _query(schema: str) -> str:
    # PTABELA/VLPVENDA na ROTINA_105 já vêm pré-multiplicados por QTESTOQUE
    # (conferido: PTABELA / QTESTOQUE == PVENDA_2 exatamente, em vários
    # produtos reais) — PVENDA_2 é o preço unitário de fato; o valor de
    # estoque é calculado aqui como QTESTOQUE * PVENDA_2, não usamos PTABELA
    # direto pra não depender desse comportamento não-documentado da view.
    return f"""
        SELECT
            CODFILIAL, CODPROD, DESCRICAO, DEPARTAMENTO, SECAO, CLASSEVENDA,
            FORNECEDOR, FANTASIA, EMBALAGEM, QTUNITCX,
            QTESTOQUECX, QTESTOQUE, QTPENDENTE, QTBLOQUEADA, QTRESERV,
            DTULTENT, DTULTSAIDA, PVENDA_2, FORALINHA
        FROM {schema}.ROTINA_105
        WHERE DESCRICAO IS NOT NULL
    """


def _carregar() -> tuple[pd.DataFrame, list[str]]:
    frames = []
    indisponiveis = []
    for rotulo, eng, schema, filial_map in _SOURCES:
        try:
            df = carregar_dados(_query(schema), eng, f"estoque_{rotulo}")
            df.columns = df.columns.str.upper()

            try:
                df_v = carregar_dados(_query_vendas(schema), eng, f"vendas30d_{rotulo}")
                df_v.columns = df_v.columns.str.upper()
                df_v["QT_VENDIDA"] = pd.to_numeric(df_v["QT_VENDIDA"], errors="coerce").fillna(0)
                df = df.merge(
                    df_v[["CODFILIAL", "CODPROD", "QT_VENDIDA"]],
                    on=["CODFILIAL", "CODPROD"], how="left"
                )
            except Exception as e:
                print(f"[AVISO] vendas30d_{rotulo} falhou ({str(e)[:100]}) — média de venda ficará zerada")
                df["QT_VENDIDA"] = 0

            df["_ROTULO"] = rotulo
            df["_ESTADO"] = df["CODFILIAL"].astype(str).map(filial_map).fillna(_DEFAULT_ROTULO[rotulo])
            frames.append(df)
        except Exception as e:
            print(f"[AVISO] estoque_{rotulo} falhou ({str(e)[:100]}) — ignorado")
            indisponiveis.append(rotulo)
    if not frames:
        return pd.DataFrame(), indisponiveis
    return pd.concat(frames, ignore_index=True), indisponiveis


print("\n=== Carregando posição de estoque (ROTINA_105) ===")
df, fontes_indisponiveis = _carregar()

if df.empty:
    produtos_out = []
    departamentos_out = []
    estados_out = []
else:
    for col in ["QTUNITCX", "QTESTOQUECX", "QTESTOQUE", "QTPENDENTE", "QTBLOQUEADA", "QTRESERV", "PVENDA_2", "QT_VENDIDA"]:
        df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0)
    df["MEDIA_VENDA_DIA"] = df["QT_VENDIDA"] / DIAS_MEDIA_VENDA
    for col in ["DESCRICAO", "DEPARTAMENTO", "SECAO", "FORNECEDOR", "FANTASIA", "EMBALAGEM", "CLASSEVENDA", "FORALINHA"]:
        df[col] = df[col].fillna("").astype(str).str.strip()
    df["DTULTENT"]   = pd.to_datetime(df["DTULTENT"], errors="coerce")
    df["DTULTSAIDA"] = pd.to_datetime(df["DTULTSAIDA"], errors="coerce")

    # Só produtos com algum movimento de estoque relevante (evita lixo de
    # cadastro sem giro nenhum e sem posição nenhuma)
    df = df[(df["QTESTOQUE"] != 0) | (df["QTPENDENTE"] != 0) | (df["QTBLOQUEADA"] != 0) | (df["QTRESERV"] != 0) | (df["DTULTSAIDA"].notna())]

    hoje = date.today()
    produtos_out = []
    for _, r in df.iterrows():
        media_dia = float(r["MEDIA_VENDA_DIA"])
        qtestoque = float(r["QTESTOQUE"])
        dias_para_esgotar = round(qtestoque / media_dia, 1) if media_dia > 0 else None
        previsao_esgotar = (hoje + pd.Timedelta(days=dias_para_esgotar)).strftime("%d/%m/%Y") \
            if dias_para_esgotar is not None and qtestoque > 0 else ""
        dias_em_estoque = (hoje - r["DTULTENT"].date()).days if pd.notna(r["DTULTENT"]) else None
        dias_sem_venda  = (hoje - r["DTULTSAIDA"].date()).days if pd.notna(r["DTULTSAIDA"]) else None
        produtos_out.append({
            "empresa":     r["_ROTULO"],
            "estado":      r["_ESTADO"],
            "codfilial":   str(r["CODFILIAL"]),
            "codprod":     str(int(r["CODPROD"])),
            "descricao":   r["DESCRICAO"],
            "departamento": r["DEPARTAMENTO"],
            "secao":       r["SECAO"],
            "classevenda": r["CLASSEVENDA"],
            "fornecedor":  r["FORNECEDOR"],
            "fantasia":    r["FANTASIA"],
            "embalagem":   r["EMBALAGEM"],
            "qtunitcx":    int(r["QTUNITCX"]),
            "qtestoquecx": round(float(r["QTESTOQUECX"]), 2),
            "qtestoque":   round(float(r["QTESTOQUE"]), 2),
            "qtpendente":  round(float(r["QTPENDENTE"]), 2),
            "qtbloqueada": round(float(r["QTBLOQUEADA"]), 2),
            "qtreserv":    round(float(r["QTRESERV"]), 2),
            "dtultent":    r["DTULTENT"].strftime("%d/%m/%Y") if pd.notna(r["DTULTENT"]) else "",
            "dtultsaida":  r["DTULTSAIDA"].strftime("%d/%m/%Y") if pd.notna(r["DTULTSAIDA"]) else "",
            "precounit":   round(float(r["PVENDA_2"]), 2),
            "valorestoque": round(float(r["QTESTOQUE"]) * float(r["PVENDA_2"]), 2),
            "mediavendadia": round(media_dia, 2),
            "diasparaesgotar": dias_para_esgotar,
            "previsaoesgotar": previsao_esgotar,
            "diasemestoque": dias_em_estoque,
            "diassemvenda": dias_sem_venda,
            "foralinha":   r["FORALINHA"] == "S",
        })

    departamentos_out = sorted({p["departamento"] for p in produtos_out if p["departamento"]})
    estados_out        = sorted({p["estado"] for p in produtos_out if p["estado"]})

payload = {
    "atualizado_em":        datetime.now().strftime("%d/%m/%Y %H:%M"),
    "total":                len(produtos_out),
    "produtos":             produtos_out,
    "departamentos":        departamentos_out,
    "estados":              estados_out,
    "fontes_indisponiveis": fontes_indisponiveis,
}

out_path = BASE / "estoque_data.js"
with open(out_path, "w", encoding="utf-8") as f:
    f.write(f"// Gerado automaticamente\nconst ESTOQUE_DATA = {json.dumps(payload, ensure_ascii=False, separators=(',', ':'))};\n")

tamanho_mb = out_path.stat().st_size / (1024 * 1024)
print(f"OK estoque_data.js — {len(produtos_out)} produtos, {tamanho_mb:.1f}MB")
if fontes_indisponiveis:
    print(f"[AVISO] Fontes indisponíveis: {fontes_indisponiveis} — resultados podem estar incompletos.")

repo_dir = str(BASE)
subprocess.run(["git", "-C", repo_dir, "add", "estoque_data.js", "estoque.html"], check=False)
subprocess.run(["git", "-C", repo_dir, "commit", "-m",
                f"Atualiza estoque_data.js - {date.today().strftime('%d/%m/%Y')}"])
subprocess.run(["git", "-C", repo_dir, "push", "origin", "master"], check=False)
print("OK GitHub Pages atualizado.")
