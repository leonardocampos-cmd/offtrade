"""Gera pedidos_mercos_data.js a partir dos relatorios exportados
manualmente da Mercos (nao tem API/scraping automatizado - ver CLAUDE.md)
e cruza cada pedido com o Winthor (SPON) pra saber se ja foi faturado.

Como rodar (local, apos exportar da Mercos):
  1. Login manual em app.mercos.com > Indicadores > Relatorios.
  2. Exportar "Produtos por pedido" (Excel) -> salvar como
     C:\\Users\\LeonardoCampos\\Downloads\\relatorio.xls
     (o relatorio trunca em 5000 linhas — se o periodo desejado passar disso,
     exportar em pedacos, ex: um por semestre, e salvar como
     "relatorio_sem1.xls", "relatorio_sem2.xls" etc.; todo arquivo
     "relatorio.xls" ou "relatorio_sem*.xls" na pasta e' lido e mesclado)
  3. Exportar "Vendas detalhadas" (Excel, mesmo periodo) -> salvar como
     C:\\Users\\LeonardoCampos\\Downloads\\Vendas detalhadas.xls
  4. python gerar_pedidos_mercos_data.py
  5. python sync_mercos_exports_vps.py  -- manda os arquivos pra VPS,
     pro cron de la (a cada 30min) ter o snapshot mais recente.

Rodando na VPS (cron, OFFTRADE_RUNTIME=vps): le os mesmos 2 arquivos de
MERCOS_EXPORTS_DIR (default /opt/mercos-exports, sincronizado por
sync_mercos_exports_vps.py) e se autopublica direto em /opt/offtrade-static
(mesmo padrao de exportacao_meta.py::_publicar_static) — o cron so
atualiza o cruzamento com o SPON sobre o snapshot da Mercos que ja existe;
pedidos novos so aparecem depois de reexportar da Mercos e sincronizar.

Cruzamento com o SPON: pedidos lancados pelo usuario Winthor "W.S" (canal
Mercos) guardam em PCPEDC.NUMPEDCLI o padrao "<numero_pedido_mercos>/
<codigo_vendedor>" (confirmado em 2026-08-27, ex: "3637/007"). E o unico
jeito de saber se um pedido do Mercos ja foi faturado sem precisar abrir
pedido por pedido no Winthor.
"""
import glob
import json
import os
import re
from datetime import datetime
from pathlib import Path

import pandas as pd

from meta import engine_spon, carregar_dados

_RUNTIME = os.getenv("OFFTRADE_RUNTIME", "local")
_EXPORTS_DIR = os.getenv(
    "MERCOS_EXPORTS_DIR",
    "/opt/mercos-exports" if _RUNTIME == "vps" else r"C:\Users\LeonardoCampos\Downloads",
)
PRODUTOS_GLOB = os.path.join(_EXPORTS_DIR, "relatorio*.xls")
VENDAS_PATH = os.path.join(_EXPORTS_DIR, "Vendas detalhadas.xls")
OUT_JS = str(Path(__file__).parent / "pedidos_mercos_data.js")

# Canal W.S/Mercos comecou em 19/11/2025 (confirmado em 2026-08-27 apos
# exportar todo o historico disponivel na Mercos, semestre a semestre —
# 09/2024 a 08/2025 veio vazio, dado real so aparece a partir de 11/2025).
DATA_INICIAL = "2025-11-01"

RE_NUMPEDCLI = re.compile(r"^\s*(\d+)\s*/\s*(\S+)\s*$")


def _carregar_pedido_info():
    vd = pd.read_excel(VENDAS_PATH, header=None)
    header_idx = vd[vd[0] == "Data de emissão"].index[0]
    vd_data = vd.iloc[header_idx + 1:].copy()
    vd_data.columns = vd.iloc[header_idx]
    vd_data = vd_data.dropna(subset=["Pedido"])
    return {
        str(row["Pedido"]): {
            "cnpj": str(row["CNPJ/CPF"]),
            "representada": row["Representada"],
        }
        for _, row in vd_data.iterrows()
    }


def _listar_arquivos_produtos():
    """Todo relatorio.xls/relatorio_sem*.xls valido na pasta de export —
    o relatorio "Produtos por pedido" trunca em 5000 linhas, entao um
    periodo longo precisa vir em varios arquivos (ex: um por semestre)."""
    candidatos = sorted(glob.glob(PRODUTOS_GLOB))
    validos = []
    for caminho in candidatos:
        try:
            titulo = pd.read_excel(caminho, header=None, nrows=2).iat[1, 0]
        except Exception:
            continue
        if isinstance(titulo, str) and "Produtos por Pedido" in titulo:
            validos.append(caminho)
        else:
            print(f"[AVISO] {caminho} ignorado (nao parece ser um relatorio de Produtos por Pedido)")
    return validos


def _montar_pedidos(pedido_info):
    pedidos = {}
    for caminho in _listar_arquivos_produtos():
        pp = pd.read_excel(caminho, header=None)
        produto_atual = None
        i, n = 0, len(pp)
        while i < n:
            col0 = pp.iat[i, 0]
            if isinstance(col0, str) and col0.startswith("Produto:"):
                texto = col0[len("Produto:"):].strip()
                codigo_prod, _, desc_prod = texto.partition(" - ")
                produto_atual = (codigo_prod.strip(), desc_prod.strip())
                i += 2
                continue
            if produto_atual is not None and pd.notna(col0) and pd.notna(pp.iat[i, 1]):
                pedido = str(int(pp.iat[i, 1]))
                criador = str(pp.iat[i, 3]) if pd.notna(pp.iat[i, 3]) else ""
                cod_vend, _, nome_vend = criador.partition(" - ")
                info = pedido_info.get(pedido, {})
                p = pedidos.setdefault(pedido, {
                    "numped": pedido,
                    "data": col0,
                    "cod_vendedor": cod_vend.strip(),
                    "vendedor": nome_vend.strip(),
                    "cnpj": info.get("cnpj", ""),
                    "cliente": pp.iat[i, 2],
                    "representada": info.get("representada", ""),
                    "itens": [],
                })
                p["itens"].append({
                    "codprod": produto_atual[0],
                    "descricao": produto_atual[1],
                    "qt": float(pp.iat[i, 5]),
                    "preco_liquido": float(pp.iat[i, 4]),
                    "subtotal": float(pp.iat[i, 6]),
                })
            i += 1

    lista = list(pedidos.values())
    for p in lista:
        p["subtotal_pedido"] = round(sum(it["subtotal"] for it in p["itens"]), 2)
        p["qt_pedido"] = sum(it["qt"] for it in p["itens"])
    return lista


def _cruzar_com_spon(lista_pedidos):
    # CODPROD por item (não só o total do pedido) pra dar pra apontar
    # exatamente qual produto foi cortado — o código de produto é o mesmo
    # entre Mercos e Winthor (confirmado em 2026-08-27 comparando itens de
    # pedidos vizinhos do mesmo cliente).
    # NUMNOTA só é preenchido quando o pedido vira nota fiscal de verdade —
    # sem ele o pedido só está "montado" no Winthor, ainda não faturado
    # (bug reportado pelo usuário em 2026-08-27: pedidos recém-lançados sem
    # NUMNOTA estavam caindo em "integral"/"corte" só por já terem
    # NUMPEDCLI preenchido).
    query = f"""
        SELECT PED.NUMPED, PC.NUMPEDCLI, PED.CODPROD, SUM(PED.QT) AS QT, SUM(PED.TOTAL) AS TOTAL,
               MAX(CASE WHEN PED.NUMNOTA IS NOT NULL THEN 1 ELSE 0 END) AS TEM_NOTA,
               MAX(PED.NUMNOTA) AS NUMNOTA
        FROM SPON.PBI_PCPEDI PED
        LEFT JOIN SPON.PCPEDC PC ON PC.NUMPED = PED.NUMPED
        WHERE PED.DATA >= DATE '{DATA_INICIAL}'
          AND PED.NOME = 'W.S'
        GROUP BY PED.NUMPED, PC.NUMPEDCLI, PED.CODPROD
    """
    try:
        df = carregar_dados(query, engine_spon, "spon_ws_pedidos")
    except Exception as e:
        print(f"[AVISO] cruzamento com SPON indisponivel ({str(e)[:100]}) — pedidos ficam sem status_spon")
        for p in lista_pedidos:
            p["status_spon"] = "indisponivel"
        return
    df.columns = df.columns.str.upper()

    spon_por_mercos = {}
    for _, row in df.iterrows():
        m = RE_NUMPEDCLI.match(str(row["NUMPEDCLI"] or "").strip())
        if not m:
            continue
        spon_por_mercos.setdefault(m.group(1), []).append(row)

    nao_encontrados = []
    for p in lista_pedidos:
        candidatos = spon_por_mercos.get(p["numped"])
        if not candidatos:
            p["status_spon"] = "nao_encontrado"
            p["valor_spon"] = None
            p["numped_spon"] = []
            p["numnota_spon"] = []
            p["itens_cortados"] = []
            nao_encontrados.append(p)
            continue
        total_spon = round(sum(float(c["TOTAL"]) for c in candidatos), 2)
        diff = round(total_spon - p["subtotal_pedido"], 2)
        p["valor_spon"] = total_spon
        p["numped_spon"] = sorted({str(int(c["NUMPED"])) for c in candidatos})
        p["numnota_spon"] = sorted({
            str(int(c["NUMNOTA"])) for c in candidatos
            if c["NUMNOTA"] is not None and pd.notna(c["NUMNOTA"])
        })
        tem_nota = any(int(c["TEM_NOTA"]) == 1 for c in candidatos)
        if not tem_nota:
            p["status_spon"] = "montado"
        elif diff < -0.5:
            p["status_spon"] = "corte"
        elif diff > 0.5:
            p["status_spon"] = "excesso"
        else:
            p["status_spon"] = "integral"

        # Corte só faz sentido comparado contra o que foi de fato faturado —
        # um pedido "montado" (sem nota ainda) pode ter quantidade menor
        # simplesmente porque ainda não terminaram de montá-lo, não porque
        # cortaram produto.
        qt_spon_por_produto = {}
        if tem_nota:
            for c in candidatos:
                codprod = str(int(c["CODPROD"]))
                qt_spon_por_produto[codprod] = qt_spon_por_produto.get(codprod, 0.0) + float(c["QT"] or 0)

        itens_cortados = []
        for it in p["itens"]:
            qt_spon = qt_spon_por_produto.get(str(it["codprod"]), 0.0)
            if tem_nota and qt_spon < it["qt"] - 0.01:
                itens_cortados.append({
                    "codprod": it["codprod"],
                    "descricao": it["descricao"],
                    "qt_pedido": it["qt"],
                    "qt_faturada": round(qt_spon, 2),
                    "qt_cortada": round(it["qt"] - qt_spon, 2),
                })
        p["itens_cortados"] = itens_cortados

    # Dos "não encontrados", checa se o CNPJ nem existe como cliente
    # cadastrado no SPON (prospect novo, cadastro pendente) — diferente de
    # um pedido que só ainda não foi transcrito pro Winthor. CGCENT guarda
    # o CNPJ com máscara (XX.XXX.XXX/XXXX-XX), por isso o REGEXP_REPLACE
    # dos dois lados (confirmado em 2026-08-27).
    if nao_encontrados:
        # Oracle limita IN (...) a 1000 expressoes — com o historico completo
        # da Mercos os "nao encontrados" passam disso facilmente, entao a
        # checagem vai em lotes (bug visto em 2026-08-27 ao carregar todo o
        # historico: ORA-01795 estourava a query inteira).
        cnpjs_unicos = sorted({p["cnpj"] for p in nao_encontrados})
        TAMANHO_LOTE = 900
        cnpjs_cadastrados = set()
        erro = None
        for inicio in range(0, len(cnpjs_unicos), TAMANHO_LOTE):
            lote = cnpjs_unicos[inicio:inicio + TAMANHO_LOTE]
            cnpjs_sql = ",".join(f"'{c}'" for c in lote)
            query_clientes = f"""
                SELECT DISTINCT REGEXP_REPLACE(CGCENT, '[^0-9]', '') AS CNPJ_LIMPO
                FROM SPON.PCCLIENT
                WHERE REGEXP_REPLACE(CGCENT, '[^0-9]', '') IN ({cnpjs_sql})
            """
            try:
                df_clientes = carregar_dados(query_clientes, engine_spon, "spon_clientes_cadastrados")
                df_clientes.columns = df_clientes.columns.str.upper()
                cnpjs_cadastrados.update(df_clientes["CNPJ_LIMPO"])
            except Exception as e:
                erro = e
                print(f"[AVISO] checagem de cadastro de cliente indisponivel ({str(e)[:100]}) — ignorado")
                break
        if erro is None:
            for p in nao_encontrados:
                if p["cnpj"] not in cnpjs_cadastrados:
                    p["status_spon"] = "cliente_nao_cadastrado"


def _carregar_status_logistica():
    """Le logistica_por_nf de pedidos_data.js (gerado por pedidos.py, mesmo
    diretorio — local ou VPS) e devolve so as entradas SPON, por numnota."""
    caminho = Path(__file__).parent / "pedidos_data.js"
    if not caminho.exists():
        return {}
    try:
        texto = caminho.read_text(encoding="utf-8")
        payload = json.loads(texto[texto.index("=") + 1: texto.rindex(";")])
    except Exception as e:
        print(f"[AVISO] pedidos_data.js indisponivel pra status de logistica ({str(e)[:100]}) — ignorado")
        return {}
    prefixo = "SPON|"
    return {
        chave[len(prefixo):]: info
        for chave, info in (payload.get("logistica_por_nf") or {}).items()
        if chave.startswith(prefixo)
    }


def _anexar_status_logistica(lista_pedidos):
    logistica_por_numnota = _carregar_status_logistica()
    for p in lista_pedidos:
        notas = p.get("numnota_spon") or []
        info = next((logistica_por_numnota[n] for n in notas if n in logistica_por_numnota), None)
        p["status_logistica"] = (info or {}).get("status_log") or ""
        p["logistica_rota"] = (info or {}).get("rota") or ""
        p["logistica_data_entrega"] = (info or {}).get("data_entrega") or ""


# Mesmo padrao de exportacao_meta.py::_publicar_static — escrita sempre por
# arquivo temporario + rename atomico (evita JSON corrompido se o cron da
# VPS e uma execucao manual escreverem o mesmo arquivo ao mesmo tempo).
def _publicar_static():
    if _RUNTIME != "vps":
        return
    import shutil
    destino = "/opt/offtrade-static"
    origem = Path(OUT_JS)
    if not origem.exists():
        return
    tmp = os.path.join(destino, ".pedidos_mercos_data.js.tmp_publish")
    shutil.copy(origem, tmp)
    os.replace(tmp, os.path.join(destino, "pedidos_mercos_data.js"))
    print(f"OK - pedidos_mercos_data.js publicado em {destino}")


def main():
    if not _listar_arquivos_produtos() or not os.path.exists(VENDAS_PATH):
        print(f"[AVISO] arquivos de exportacao da Mercos nao encontrados em {_EXPORTS_DIR} — "
              f"pulando (rode sync_mercos_exports_vps.py apos reexportar da Mercos).")
        return

    pedido_info = _carregar_pedido_info()
    lista_pedidos = _montar_pedidos(pedido_info)
    _cruzar_com_spon(lista_pedidos)
    _anexar_status_logistica(lista_pedidos)
    lista_pedidos.sort(key=lambda p: (p["data"], p["numped"]), reverse=True)

    payload = {
        "atualizado_em": datetime.now().strftime("%d/%m/%Y %H:%M"),
        "periodo": f"{DATA_INICIAL[8:10]}/{DATA_INICIAL[5:7]}/{DATA_INICIAL[0:4]} em diante",
        "fontes_indisponiveis": [],
        "pedidos": lista_pedidos,
    }

    tmp = OUT_JS + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write("const PEDIDOS_MERCOS_DATA = ")
        json.dump(payload, f, ensure_ascii=False)
        f.write(";\n")
    os.replace(tmp, OUT_JS)

    print(f"{len(lista_pedidos)} pedidos gravados em {OUT_JS}")
    _publicar_static()


if __name__ == "__main__":
    main()
