"""
Gera estoque_tial_data.js — validação da planilha "ESTOQUE TIAL.xlsx" (Google
Drive compartilhado, pasta Off Trade/Estoque) contra o cadastro real do
Winthor (CRC), pedido do usuário em 2026-09-11: conferir se COD PROD, a
DESCRICAO e o EAN da planilha batem com o que está cadastrado antes de usar
essa planilha (a mesma que já foi mandada pro grupo "Estoque RJ - Rigarr"
via WhatsApp, ver whatsapp_evolution.py) pra pedir contagem/validade ao time.

Só essa planilha por enquanto (não é um padrão genérico pra qualquer arquivo
de contagem) — script pontual, não plugado no main.py nem em nenhum cron.
Roda manualmente quando quiser reconferir a planilha.

EAN: não existe coluna EAN/GTIN populada direto em CRC.PCPRODUT (checado em
2026-09-11 — GTINCODAUXILIAR* só guarda o TIPO do código de barras, tipo
"13" de EAN-13, não o valor) nem em CRC.PCCODBARRA (vazia pra esses
produtos). O valor real do EAN de caixa está em CRC.PCEMBALAGEM.CODAUXILIAR
(confirmado batendo exato contra o CODPROD 6515 da planilha). Nem todo
produto tem linha em PCEMBALAGEM — quando falta, o EAN fica "sem cadastro"
na validação (não é erro do script, é ausência real no Winthor).
"""
import json
from pathlib import Path

import pandas as pd

from meta import engine, carregar_dados

PLANILHA = Path(r"G:\Drives compartilhados\Off Trade\Estoque\ESTOQUE TIAL.xlsx")
OUT_JS = Path(__file__).parent / "estoque_tial_data.js"


def _norm(s):
    return " ".join(str(s or "").strip().upper().split())


def main():
    df = pd.read_excel(PLANILHA)
    df.columns = [c.strip().upper() for c in df.columns]
    df = df.rename(columns={"COD PROD": "CODPROD", "DESCRICAO": "DESCRICAO_PLANILHA", "EAN": "EAN_PLANILHA"})
    df["CODPROD"] = pd.to_numeric(df["CODPROD"], errors="coerce")
    df = df.dropna(subset=["CODPROD"])
    df["CODPROD"] = df["CODPROD"].astype(int)

    codprods = sorted(df["CODPROD"].unique().tolist())
    lista_in = ",".join(str(c) for c in codprods)

    cadastro = carregar_dados(
        f"SELECT CODPROD, DESCRICAO FROM CRC.PCPRODUT WHERE CODPROD IN ({lista_in})",
        engine, "estoque_tial_pcprodut",
    )
    cadastro.columns = cadastro.columns.str.upper()
    descricao_por_prod = dict(zip(cadastro["CODPROD"], cadastro["DESCRICAO"]))

    embalagem = carregar_dados(
        f"""SELECT DISTINCT CODPROD, CODAUXILIAR
            FROM CRC.PCEMBALAGEM
            WHERE CODPROD IN ({lista_in}) AND CODAUXILIAR IS NOT NULL""",
        engine, "estoque_tial_pcembalagem",
    )
    embalagem.columns = embalagem.columns.str.upper()
    eans_por_prod = {}
    for _, r in embalagem.iterrows():
        eans_por_prod.setdefault(int(r["CODPROD"]), set()).add(str(int(r["CODAUXILIAR"])))

    produtos = []
    for _, row in df.iterrows():
        codprod = int(row["CODPROD"])
        desc_planilha = str(row["DESCRICAO_PLANILHA"] or "").strip()
        ean_val = row["EAN_PLANILHA"]
        if pd.isna(ean_val):
            ean_planilha = ""
        elif isinstance(ean_val, float):
            ean_planilha = str(int(ean_val))
        else:
            ean_planilha = str(ean_val).strip()

        desc_cadastro = descricao_por_prod.get(codprod)
        eans_cadastro = sorted(eans_por_prod.get(codprod, set()))

        existe_no_cadastro = desc_cadastro is not None
        descricao_bate = existe_no_cadastro and _norm(desc_cadastro) == _norm(desc_planilha)
        ean_bate = bool(eans_cadastro) and ean_planilha in eans_cadastro

        produtos.append({
            "codprod": codprod,
            "descricao_planilha": desc_planilha,
            "descricao_cadastro": desc_cadastro or "",
            "ean_planilha": ean_planilha,
            "ean_cadastro": eans_cadastro,
            "existe_no_cadastro": existe_no_cadastro,
            "descricao_bate": descricao_bate,
            "ean_bate": ean_bate,
        })

    from datetime import datetime
    payload = {
        "atualizado_em": datetime.now().strftime("%d/%m/%Y %H:%M"),
        "planilha": PLANILHA.name,
        "produtos": produtos,
    }

    tmp = OUT_JS.with_suffix(".js.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(f"const ESTOQUE_TIAL_DATA = {json.dumps(payload, ensure_ascii=False, indent=2)};\n")
    tmp.replace(OUT_JS)

    sem_cadastro = sum(1 for p in produtos if not p["existe_no_cadastro"])
    desc_diverge = sum(1 for p in produtos if p["existe_no_cadastro"] and not p["descricao_bate"])
    ean_diverge = sum(1 for p in produtos if not p["ean_bate"])
    print(f"OK {OUT_JS.name} — {len(produtos)} produtos: {sem_cadastro} sem cadastro, "
          f"{desc_diverge} com descrição divergente, {ean_diverge} com EAN divergente/sem cadastro.")


if __name__ == "__main__":
    main()
