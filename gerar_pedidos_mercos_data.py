"""Gera pedidos_mercos_data.js a partir de dois relatorios exportados
manualmente da Mercos (nao tem API/scraping automatizado - ver CLAUDE.md)
e cruza cada pedido com o Winthor (SPON) pra saber se ja foi faturado.

Como rodar:
  1. Login manual em app.mercos.com > Indicadores > Relatorios.
  2. Exportar "Produtos por pedido" (Excel) -> salvar como
     C:\\Users\\LeonardoCampos\\Downloads\\relatorio.xls
  3. Exportar "Vendas detalhadas" (Excel, mesmo periodo) -> salvar como
     C:\\Users\\LeonardoCampos\\Downloads\\Vendas detalhadas.xls
  4. python gerar_pedidos_mercos_data.py

Cruzamento com o SPON: pedidos lancados pelo usuario Winthor "W.S" (canal
Mercos) guardam em PCPEDC.NUMPEDCLI o padrao "<numero_pedido_mercos>/
<codigo_vendedor>" (confirmado em 2026-08-27, ex: "3637/007"). E o unico
jeito de saber se um pedido do Mercos ja foi faturado sem precisar abrir
pedido por pedido no Winthor.
"""
import json
import re
from datetime import datetime

import pandas as pd

from meta import engine_spon, carregar_dados

PRODUTOS_PATH = r"C:\Users\LeonardoCampos\Downloads\relatorio.xls"
VENDAS_PATH = r"C:\Users\LeonardoCampos\Downloads\Vendas detalhadas.xls"
OUT_JS = r"G:\Meu Drive\offtrade\pedidos_mercos_data.js"

DATA_INICIAL = "2026-08-01"  # ajustar junto com o periodo exportado da Mercos

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


def _montar_pedidos(pedido_info):
    pp = pd.read_excel(PRODUTOS_PATH, header=None)
    pedidos = {}
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
    query = f"""
        SELECT PED.NUMPED, PC.NUMPEDCLI, PED.CODPROD, SUM(PED.QT) AS QT, SUM(PED.TOTAL) AS TOTAL
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
            p["itens_cortados"] = []
            nao_encontrados.append(p)
            continue
        total_spon = round(sum(float(c["TOTAL"]) for c in candidatos), 2)
        diff = round(total_spon - p["subtotal_pedido"], 2)
        p["valor_spon"] = total_spon
        p["numped_spon"] = sorted({str(int(c["NUMPED"])) for c in candidatos})
        if diff < -0.5:
            p["status_spon"] = "corte"
        elif diff > 0.5:
            p["status_spon"] = "excesso"
        else:
            p["status_spon"] = "integral"

        qt_spon_por_produto = {}
        for c in candidatos:
            codprod = str(int(c["CODPROD"]))
            qt_spon_por_produto[codprod] = qt_spon_por_produto.get(codprod, 0.0) + float(c["QT"] or 0)

        itens_cortados = []
        for it in p["itens"]:
            qt_spon = qt_spon_por_produto.get(str(it["codprod"]), 0.0)
            if qt_spon < it["qt"] - 0.01:
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
        cnpjs_sql = ",".join(f"'{p['cnpj']}'" for p in nao_encontrados)
        query_clientes = f"""
            SELECT DISTINCT REGEXP_REPLACE(CGCENT, '[^0-9]', '') AS CNPJ_LIMPO
            FROM SPON.PCCLIENT
            WHERE REGEXP_REPLACE(CGCENT, '[^0-9]', '') IN ({cnpjs_sql})
        """
        try:
            df_clientes = carregar_dados(query_clientes, engine_spon, "spon_clientes_cadastrados")
            df_clientes.columns = df_clientes.columns.str.upper()
            cnpjs_cadastrados = set(df_clientes["CNPJ_LIMPO"])
        except Exception as e:
            print(f"[AVISO] checagem de cadastro de cliente indisponivel ({str(e)[:100]}) — ignorado")
            cnpjs_cadastrados = None
        if cnpjs_cadastrados is not None:
            for p in nao_encontrados:
                if p["cnpj"] not in cnpjs_cadastrados:
                    p["status_spon"] = "cliente_nao_cadastrado"


def main():
    pedido_info = _carregar_pedido_info()
    lista_pedidos = _montar_pedidos(pedido_info)
    _cruzar_com_spon(lista_pedidos)
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
    import os
    os.replace(tmp, OUT_JS)

    print(f"{len(lista_pedidos)} pedidos gravados em {OUT_JS}")


if __name__ == "__main__":
    main()
