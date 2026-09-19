"""Gera estoque_mercos_data.js cruzando o catalogo de produtos cadastrados
na Mercos (representada SPON DISTRIBUIDORA) com o estoque real do SPON
(Winthor, ROTINA_105) na filial 1.

O catalogo da Mercos nao tem API/export direto pra isso (ver CLAUDE.md) —
foi lido direto das paginas de
https://app.mercos.com/424258/representadas/707619/?p=N&status=1 (497
produtos, 34 paginas) e salvo em produtos_mercos_spon.csv. Pra atualizar:
reabrir aquela tela (Representadas > SPON DISTRIBUIDORA > Produtos e
tabelas), regerar o CSV com codigo;nome;preco_tabela;preco_promo, rodar
este script e depois sync_mercos_exports_vps.py (pro cron da VPS, a cada
30min, ter o snapshot mais recente).

Rodando na VPS (cron, OFFTRADE_RUNTIME=vps): le o CSV de
MERCOS_EXPORTS_DIR (default /opt/mercos-exports) e se autopublica direto
em /opt/offtrade-static (mesmo padrao de exportacao_meta.py) — o estoque
em si vem sempre ao vivo do Oracle a cada execucao, só o catalogo de quais
produtos existem fica preso ao último CSV sincronizado.
"""
import json
import os
import re
from datetime import datetime
from pathlib import Path

import pandas as pd

from meta import engine_spon, carregar_dados
from mercos_api import login as _mercos_login, EMPRESA_ID as _MERCOS_EMPRESA_ID, BASE_URL as _MERCOS_BASE_URL

_RUNTIME = os.getenv("OFFTRADE_RUNTIME", "local")
_EXPORTS_DIR = os.getenv(
    "MERCOS_EXPORTS_DIR",
    "/opt/mercos-exports" if _RUNTIME == "vps" else r"C:\Users\LeonardoCampos\Downloads",
)
CATALOGO_PATH = os.path.join(_EXPORTS_DIR, "produtos_mercos_spon.csv")
OUT_JS = str(Path(__file__).parent / "estoque_mercos_data.js")

CODFILIAL = 1


def _carregar_catalogo_mercos():
    df = pd.read_csv(CATALOGO_PATH, sep=";")
    df["codigo"] = df["codigo"].astype(str)
    return df


def _carregar_estoque_spon():
    query = f"""
        SELECT CODPROD, DESCRICAO, QTESTOQUE, QTPENDENTE, QTBLOQUEADA, QTRESERV,
               PVENDA_2, DTULTENT, DTULTSAIDA
        FROM SPON.ROTINA_105
        WHERE CODFILIAL = {CODFILIAL}
    """
    df = carregar_dados(query, engine_spon, "estoque_spon_filial1")
    df.columns = df.columns.str.upper()
    df["CODPROD"] = df["CODPROD"].astype(int).astype(str)
    return df.set_index("CODPROD")


# ── Escreve o estoque de volta na Mercos (tela "Ajuste de estoque") ────────
# Sem API oficial pra isso (mesma ressalva de mercos_api.py) — descoberto
# inspecionando a tela https://app.mercos.com/424258/representadas/707619/
# atualizar-estoque/ em 2026-09-04 (pedido do usuario: os vendedores da
# Mercos viam saldo desatualizado/zerado na hora de montar pedido). Cada
# input de estoque tem id="estoque_<produto_id>" (produto_id da Mercos, NAO
# e o CODPROD/codigo do Winthor) e dispara POST pra
# /{empresa}/atualizar-estoque/<produto_id>/ com body form-urlencoded
# quantidade=<valor> — sem CSRF token no body nem em header (view parece
# csrf_exempt, so' depende do cookie de sessao). Repagina com
# ?p=N&ordenar_por=0 (mesmo padrao do catalogo antigo), 50 produtos/pagina.
#
# Valor enviado = QTESTOQUE bruto do Winthor (SPON.ROTINA_105), sem descontar
# QTRESERV — pedido explicito do usuario em 2026-09-04 (nao "disponivel pra
# vender", o estoque fisico mesmo). Negativo trava em 0: nao existe estoque
# negativo do lado da Mercos.
def _scrape_produto_ids_mercos(sessao):
    mapping = {}
    p = 1
    while p <= 30:  # 500 produtos / 50 por pagina ~= 10 paginas; 30 e' folga de seguranca
        resp = sessao.get(
            f"{_MERCOS_BASE_URL}/{_MERCOS_EMPRESA_ID}/representadas/707619/atualizar-estoque/",
            params={"p": p, "ordenar_por": 0}, timeout=30,
        )
        resp.raise_for_status()
        linhas = re.findall(r'id="estoque_(\d+)".*?</td>\s*<td>(\d+)</td>', resp.text, re.DOTALL)
        if not linhas:
            break
        for produto_id, codigo in linhas:
            mapping[codigo] = produto_id
        p += 1
    return mapping


def _atualizar_estoque_mercos(estoque_df):
    """Retorna um resumo {ok, falhas, mapeados} — usado tanto pelo cron
    (só imprime) quanto pelo botão "Sincronizar estoque" de estoque_mercos.html
    (via pedidos_mercos_api.py, que devolve esse resumo pro navegador)."""
    try:
        sessao = _mercos_login()
    except Exception as e:
        msg = f"login na Mercos falhou — estoque nao sera empurrado pra la ({e})."
        print(f"[AVISO] {msg}")
        return {"ok": 0, "falhas": 0, "mapeados": 0, "erro": msg}

    mapping = _scrape_produto_ids_mercos(sessao)
    if not mapping:
        msg = "nao consegui mapear produtos na tela de estoque da Mercos — pulando push."
        print(f"[AVISO] {msg}")
        return {"ok": 0, "falhas": 0, "mapeados": 0, "erro": msg}

    ok, falhas = 0, 0
    for codigo, produto_id in mapping.items():
        if codigo not in estoque_df.index:
            continue
        qtestoque = float(estoque_df.loc[codigo, "QTESTOQUE"] or 0)
        valor = max(0, int(round(qtestoque)))
        try:
            resp = sessao.post(
                f"{_MERCOS_BASE_URL}/{_MERCOS_EMPRESA_ID}/atualizar-estoque/{produto_id}/",
                data={"quantidade": valor},
                headers={"X-Requested-With": "XMLHttpRequest"},
                timeout=20,
            )
            ok += resp.status_code == 200
            falhas += resp.status_code != 200
        except Exception:
            falhas += 1

    print(f"OK - estoque empurrado pra Mercos: {ok} produto(s), {falhas} falha(s) (de {len(mapping)} mapeados).")
    return {"ok": ok, "falhas": falhas, "mapeados": len(mapping), "erro": None}


def _publicar_static():
    if _RUNTIME != "vps":
        return
    import shutil
    destino = "/opt/offtrade-static"
    origem = Path(OUT_JS)
    if not origem.exists():
        return
    tmp = os.path.join(destino, ".estoque_mercos_data.js.tmp_publish")
    shutil.copy(origem, tmp)
    os.replace(tmp, os.path.join(destino, "estoque_mercos_data.js"))
    print(f"OK - estoque_mercos_data.js publicado em {destino}")


def main():
    if not os.path.exists(CATALOGO_PATH):
        print(f"[AVISO] catalogo da Mercos nao encontrado em {CATALOGO_PATH} — "
              f"pulando geracao de estoque_mercos_data.js (rode sync_mercos_exports_vps.py "
              f"apos reexportar da Mercos). Push de estoque pra Mercos nao depende desse "
              f"catalogo — roda do mesmo jeito, abaixo.")
        estoque = _carregar_estoque_spon()
        try:
            return _atualizar_estoque_mercos(estoque)
        except Exception as e:
            import traceback
            print("[AVISO] push de estoque pra Mercos falhou — ignorado.")
            traceback.print_exc()
            return {"ok": 0, "falhas": 0, "mapeados": 0, "erro": str(e)[:200]}

    catalogo = _carregar_catalogo_mercos()
    estoque = _carregar_estoque_spon()

    produtos = []
    for _, row in catalogo.iterrows():
        codigo = row["codigo"]
        est = estoque.loc[codigo] if codigo in estoque.index else None
        item = {
            "codigo": codigo,
            "nome": row["nome"],
            "preco_tabela_mercos": float(row["preco_tabela"]),
            "preco_promo_mercos": float(row["preco_promo"]),
            "encontrado_spon": est is not None,
        }
        if est is not None:
            item.update({
                "descricao_spon": est["DESCRICAO"],
                "qtestoque": float(est["QTESTOQUE"] or 0),
                "qtpendente": float(est["QTPENDENTE"] or 0),
                "qtbloqueada": float(est["QTBLOQUEADA"] or 0),
                "qtreserv": float(est["QTRESERV"] or 0),
                "preco_venda_spon": float(est["PVENDA_2"] or 0),
                "dtultent": est["DTULTENT"].strftime("%d/%m/%Y") if pd.notna(est["DTULTENT"]) else None,
                "dtultsaida": est["DTULTSAIDA"].strftime("%d/%m/%Y") if pd.notna(est["DTULTSAIDA"]) else None,
            })
            # QTBLOQUEADA (avariado/quarentena) é um saldo à parte no Winthor,
            # não um subconjunto de QTESTOQUE — confirmado com o usuário em
            # 2026-09-01 (código 40/AMARULA: QTESTOQUE=0, QTBLOQUEADA=77,
            # sistema mostra o produto zerado, não com -77; código 5143/
            # BALLANTINE'S: sistema mostra 16 disponível, QTESTOQUE=16, mas a
            # fórmula antiga subtraía QTBLOQUEADA=60 e dava -44). Só QTRESERV
            # é de fato reservado dentro do próprio QTESTOQUE.
            item["qtdisponivel"] = round(item["qtestoque"] - item["qtreserv"], 2)
        else:
            item.update({
                "descricao_spon": None, "qtestoque": None, "qtpendente": None,
                "qtbloqueada": None, "qtreserv": None, "preco_venda_spon": None,
                "dtultent": None, "dtultsaida": None, "qtdisponivel": None,
            })
        produtos.append(item)

    produtos.sort(key=lambda p: p["nome"])

    payload = {
        "atualizado_em": datetime.now().strftime("%d/%m/%Y %H:%M"),
        "filial": CODFILIAL,
        "fontes_indisponiveis": [],
        "produtos": produtos,
    }

    tmp = OUT_JS + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write("const ESTOQUE_MERCOS_DATA = ")
        json.dump(payload, f, ensure_ascii=False)
        f.write(";\n")
    os.replace(tmp, OUT_JS)

    print(f"{len(produtos)} produtos gravados em {OUT_JS}")
    print(f"  - encontrados no SPON: {sum(1 for p in produtos if p['encontrado_spon'])}")
    print(f"  - nao encontrados no SPON: {sum(1 for p in produtos if not p['encontrado_spon'])}")
    _publicar_static()

    try:
        return _atualizar_estoque_mercos(estoque)
    except Exception as e:
        import traceback
        print("[AVISO] push de estoque pra Mercos falhou — ignorado.")
        traceback.print_exc()
        return {"ok": 0, "falhas": 0, "mapeados": 0, "erro": str(e)[:200]}


if __name__ == "__main__":
    main()
