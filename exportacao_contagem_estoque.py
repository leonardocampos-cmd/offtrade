"""
Gera contagem_estoque_data.js — TODO o catálogo (CRC.PCPRODUT, sem filtro de
canal/vendedor) com a data da última saída (venda) e a última
contagem/vencimento preenchidos manualmente pelo time de logística em
contagem_estoque.html.

Catálogo completo, não só o que vendeu pelo canal OFF TRADE/W.S — pedido do
usuário em 2026-09-14, depois do CODPROD 8102 (PINATI NUTS ZERO ORIGINAL,
vendido só por outro canal/vendedor) não aparecer na página com o filtro
antigo (o mesmo de exportacao_estoque_whatsapp.py::CATALOGO_QUERY). A
contagem física de logística precisa cobrir qualquer produto que exista no
estoque físico, não só o que já vendeu por um canal específico.

Substitui o fluxo por WhatsApp (exportacao_estoque_whatsapp.py) — pedido do
usuário em 2026-09-14: várias pessoas pediam/faziam contagem solta no grupo
"Estoque RJ - Rigarr" e a extração por IA acabou sendo frágil (mensagens sem
padrão fixo, e depois descobriu-se que o Evolution API estava perdendo
histórico do grupo por causa de CLEAN_STORE_MESSAGES — ver
project_evolution_clean_store_bug_e_conta_hostinger na memória). A página
nova é só uma tabela editável (quantidade contada + data de vencimento),
sem depender de parsing de mensagem nenhum.

A contagem em si (quantidade_contada/data_vencimento) NÃO é escrita por
esse script — é gravada direto por contagem_estoque.html via
POST /api/contagem-estoque/salvar (ver pedidos_mercos_api.py), no mesmo
arquivo contagem_estoque.json que esse script só LÊ aqui pra montar o
payload publicado. Esse script só atualiza a lista de produtos + última
saída (dado do Oracle, não muda por edição manual).

"Saída" = CODOPER começando com 'S' (convenção do Winthor, mesma usada em
exportacao_estoque_movimentacao.py) — venda, transferência enviada,
devolução a fornecedor etc.
"""
import json
import os
from datetime import datetime
from pathlib import Path

import pandas as pd

import meta

HERE = Path(__file__).parent
OUT_JS = HERE / "contagem_estoque_data.js"
CONTAGEM_JSON = HERE / "contagem_estoque.json"

_QUERY = """
    SELECT P.CODPROD, P.DESCRICAO,
           MAX(CASE WHEN M2.CODOPER LIKE 'S%' AND M2.DTCANCEL IS NULL THEN M2.DTMOV END) AS ULTIMA_SAIDA
    FROM CRC.PCPRODUT P
    LEFT JOIN CRC.PCMOV M2 ON M2.CODPROD = P.CODPROD
    GROUP BY P.CODPROD, P.DESCRICAO
    ORDER BY P.DESCRICAO
"""


def _carregar_contagem():
    if CONTAGEM_JSON.exists():
        try:
            with open(CONTAGEM_JSON, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def main():
    fontes_indisponiveis = []
    try:
        df = meta.carregar_dados(_QUERY, meta.engine, "PCPRODUT + ultima saida (contagem)")
    except Exception as e:
        print(f"[AVISO] CRC indisponível ({str(e)[:150]}) — pulando.")
        return

    contagem = _carregar_contagem()

    itens = []
    for r in df.to_dict("records"):
        codprod = str(int(r["CODPROD"]))
        salvo = contagem.get(codprod, {})
        ultima_saida = r["ULTIMA_SAIDA"]
        itens.append({
            "codprod": codprod,
            "descricao": r["DESCRICAO"],
            # NaT (pandas) não é falsy tipo NaN — "if ultima_saida" dava
            # True pra produto nunca vendido e quebrava no .strftime().
            "ultima_saida": None if pd.isna(ultima_saida) else ultima_saida.strftime("%d/%m/%Y"),
            "quantidade_contada": salvo.get("quantidade_contada"),
            "data_vencimento": salvo.get("data_vencimento"),
            "contagem_atualizado_em": salvo.get("atualizado_em"),
            "conferente": salvo.get("conferente"),
        })

    payload = {
        "atualizado_em": datetime.now().strftime("%d/%m/%Y %H:%M"),
        "fontes_indisponiveis": fontes_indisponiveis,
        "itens": itens,
    }

    tmp = OUT_JS.with_suffix(".js.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        f.write("const CONTAGEM_ESTOQUE_DATA = ")
        json.dump(payload, f, ensure_ascii=False)
        f.write(";\n")
    os.replace(tmp, OUT_JS)
    print(f"OK - {len(itens)} produto(s) -> {OUT_JS}")

    import subprocess
    repo_dir = str(HERE)
    try:
        subprocess.run(["git", "-C", repo_dir, "add", "contagem_estoque_data.js"], check=True)
        subprocess.run(["git", "-C", repo_dir, "commit", "-m",
                        f"Atualiza contagem_estoque_data.js - {datetime.now().strftime('%d/%m/%Y %H:%M')}"])
        subprocess.run(["git", "-C", repo_dir, "push", "origin", "master"], check=True)
        print("OK contagem_estoque_data.js enviado ao GitHub Pages.")
    except subprocess.CalledProcessError:
        print("[AVISO] git push falhou — ignorado, script continua.")


if __name__ == "__main__":
    main()
