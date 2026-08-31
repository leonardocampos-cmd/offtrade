"""
Gera estoque_movimentacao_data.js — linha do tempo de entrada/saída (com
saldo após cada movimento) por produto, últimos 90 dias, pra alimentar o
botão "Linha do tempo" em estoque.html.

Direção do movimento vem do CODOPER (PCMOV) — convenção do Winthor: começa
com 'E' é entrada (compra, transferência recebida, devolução de cliente
etc.), começa com 'S' é saída (venda, transferência enviada, devolução a
fornecedor etc.) — confirmado consultando os CODOPER reais em uso
(2026-08-21): E, ED, EN, ER, EB, EA (entrada) / S, ST, SC, SB, SD, SA, SR,
SV (saída).

Saldo por movimento é calculado de trás pra frente a partir do QTESTOQUE
atual (ROTINA_105): saldo_inicial_do_periodo = qtestoque_atual - soma das
entradas do período + soma das saídas do período; depois anda em ordem
cronológica somando/subtraindo cada movimento.
"""
import json
from datetime import datetime
from pathlib import Path

import pandas as pd
from meta import engine, engine_theking, engine_garrido, engine_spon, engine_mgon, carregar_dados

BASE = Path(__file__).parent

_SOURCES = [
    ("CRC",      engine,          "CRC"),
    ("THEKINGS", engine_theking,  "THEKINGS"),
    ("GARRIDO",  engine_garrido,  "GARRIDO"),
    ("SPON",     engine_spon,     "SPON"),
    ("MGON",     engine_mgon,     "MGON"),
]

DIAS_JANELA = 90


def _query_movimentos(schema: str) -> str:
    return f"""
        SELECT CODFILIAL, CODPROD, DTMOV, CODOPER, QT
        FROM {schema}.PCMOV
        WHERE DTMOV >= TRUNC(SYSDATE) - {DIAS_JANELA}
          AND DTCANCEL IS NULL
    """


def _query_saldo_atual(schema: str) -> str:
    return f"SELECT CODFILIAL, CODPROD, QTESTOQUE FROM {schema}.ROTINA_105 WHERE DESCRICAO IS NOT NULL"


def main():
    movimentos_por_produto = {}
    fontes_indisponiveis = []

    for rotulo, eng, schema in _SOURCES:
        try:
            df_mov = carregar_dados(_query_movimentos(schema), eng, f"movimentacao_{rotulo}")
            df_mov.columns = df_mov.columns.str.upper()
            df_saldo = carregar_dados(_query_saldo_atual(schema), eng, f"saldo_atual_{rotulo}")
            df_saldo.columns = df_saldo.columns.str.upper()
        except Exception as e:
            print(f"[AVISO] {rotulo} falhou ({str(e)[:100]}) — ignorado")
            fontes_indisponiveis.append(rotulo)
            continue

        if df_mov.empty:
            continue

        df_mov["CODFILIAL"] = df_mov["CODFILIAL"].astype(str)
        df_mov["CODPROD"]   = df_mov["CODPROD"].astype(str)
        df_mov["QT"]        = pd.to_numeric(df_mov["QT"], errors="coerce").fillna(0)
        df_mov["DTMOV"]     = pd.to_datetime(df_mov["DTMOV"], errors="coerce")
        df_mov = df_mov[df_mov["DTMOV"].notna()]
        df_mov["TIPO"]      = df_mov["CODOPER"].str.upper().str.startswith("E").map({True: "ENTRADA", False: "SAIDA"})
        df_mov["DELTA"]     = df_mov.apply(lambda r: r["QT"] if r["TIPO"] == "ENTRADA" else -r["QT"], axis=1)

        df_saldo["CODFILIAL"] = df_saldo["CODFILIAL"].astype(str)
        df_saldo["CODPROD"]   = df_saldo["CODPROD"].astype(str)
        saldo_atual_map = {
            (r["CODFILIAL"], r["CODPROD"]): float(r["QTESTOQUE"])
            for _, r in df_saldo.iterrows()
        }

        df_mov.sort_values(["CODFILIAL", "CODPROD", "DTMOV"], inplace=True)

        for (codfilial, codprod), grupo in df_mov.groupby(["CODFILIAL", "CODPROD"], sort=False):
            saldo_atual = saldo_atual_map.get((codfilial, codprod))
            if saldo_atual is None:
                continue
            net_periodo   = grupo["DELTA"].sum()
            saldo_corrente = saldo_atual - net_periodo  # saldo antes do 1º movimento do período

            linhas = []
            for _, mv in grupo.iterrows():
                saldo_corrente += mv["DELTA"]
                linhas.append({
                    "data":       mv["DTMOV"].strftime("%d/%m/%Y"),
                    "tipo":       mv["TIPO"],
                    "codoper":    mv["CODOPER"],
                    "qt":         float(mv["QT"]),
                    "saldo_apos": round(saldo_corrente, 2),
                })
            linhas.reverse()  # mais recente primeiro, igual ao resto do site
            chave = f"{rotulo}|{codfilial}|{codprod}"
            movimentos_por_produto[chave] = linhas

    payload = {
        "atualizado_em": datetime.now().strftime("%d/%m/%Y %H:%M"),
        "dias_janela": DIAS_JANELA,
        "fontes_indisponiveis": fontes_indisponiveis,
        "movimentos": movimentos_por_produto,
    }

    output_path = BASE / "estoque_movimentacao_data.js"
    tmp_path = output_path.with_suffix(".js.tmp")
    with open(tmp_path, "w", encoding="utf-8") as f:
        f.write(f"const ESTOQUE_MOVIMENTACAO_DATA = {json.dumps(payload, ensure_ascii=False)};\n")
    import os
    os.replace(tmp_path, output_path)
    print(f"OK estoque_movimentacao_data.js — {len(movimentos_por_produto)} produto(s) com movimentação nos últimos {DIAS_JANELA} dias")


if __name__ == "__main__":
    main()
