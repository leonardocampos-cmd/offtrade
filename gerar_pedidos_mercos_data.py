"""Gera pedidos_mercos_data.js a partir dos relatorios "Vendas detalhadas" e
"Produtos por pedido" da Mercos, e cruza cada pedido com o Winthor (SPON)
pra saber se ja foi faturado.

Ate 2026-08-31 isso exigia exportar manualmente (login em app.mercos.com >
Indicadores > Relatorios > Exportar Excel > salvar em Downloads > sincronizar
pra VPS). A partir de 2026-08-31, busca os dois relatorios sozinho via
mercos_api.py (chamadas HTTP diretas, engenharia reversa do painel — ver
docstring de mercos_api.py) — MERCOS_USER/MERCOS_PASS no .env, sem depender
de navegador nem de ninguem exportar nada na mao. O fluxo manual antigo
continua funcionando como fallback (se o download automatico falhar, usa
qualquer relatorio*.xls / "Vendas detalhadas.xls" que ja exista em
MERCOS_EXPORTS_DIR) — sync_mercos_exports_vps.py fica sem uso na pratica,
mas nao foi removido.

Pedidos cancelados (bug reportado pelo usuario em 2026-09-02: sumiam da
pagina inteiros) sao DOIS casos diferentes, cada um com sua fonte:

1. Cancelado no MERCOS (nunca chegou a virar pedido no Winthor) — ver
   _carregar_pedidos_cancelados e mercos_api.py::baixar_vendas_canceladas.
   So da pra pegar cabecalho (cliente/valor/vendedor), sem itens: o
   relatorio de produtos da Mercos so devolve pedido confirmado.
   status_spon = "cancelado".

2. Cancelado no WINTHOR (chegou a ganhar NUMPED no SPON via integracao,
   foi cancelado la — ex: bloqueio de credito) — a linha some de
   PCPEDC/PBI_PCPEDI inteira (sem DTCANCEL, literalmente apagada; PCPEDCAN,
   a tabela "oficial" de cancelamento do Winthor, esta vazia/nao usada
   nessa base) mas fica registrada em SPON.PEDIDOS_CANCELADOS (achada em
   2026-09-02 cruzando os 2 exemplos que o usuario deu, que eram NUMPED do
   SPON, nao numero de pedido do Mercos — provavelmente a fonte da Rotina
   335 do Winthor). Tem item/valor/motivo/quem cancelou, mas NAO tem
   CODCLI/cliente (nenhuma tabela do Winthor que cruza com ela pelo NUMPED
   tinha CODCLI tambem) — ver _carregar_cancelados_spon. Por isso o
   cruzamento e feito de volta pro pedido MERCOS por ASSINATURA de item
   (conjunto de CODPROD + quantidade — ver _casar_cancelados_spon_com_mercos):
   o pedido some do SPON, mas continua "confirmado" do lado do Mercos (a
   integracao nao avisa o Mercos que foi cancelado no Winthor), entao da
   pra achar o pedido Mercos original comparando os itens. Quando acha,
   so marca esse pedido existente como "cancelado_spon" (fica com cliente,
   vendedor, numero do pedido Mercos etc. — pedido do usuario em
   2026-09-02: "precisa ser com base nos pedidos Mercos", nao um registro
   orfao). So cai no fallback orfao (numped = o proprio NUMPED do SPON,
   cliente "nao identificado") quando nao acha nenhum pedido Mercos com os
   mesmos itens.

Rodando na VPS (cron, OFFTRADE_RUNTIME=vps): se autopublica direto em
/opt/offtrade-static (mesmo padrao de exportacao_meta.py::_publicar_static).

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
from datetime import date, datetime
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
VENDAS_CANCELADAS_PATH = os.path.join(_EXPORTS_DIR, "Vendas detalhadas - cancelados.xls")
OUT_JS = str(Path(__file__).parent / "pedidos_mercos_data.js")

# Canal W.S/Mercos comecou em 19/11/2025 (confirmado em 2026-08-27 apos
# exportar todo o historico disponivel na Mercos, semestre a semestre —
# 09/2024 a 08/2025 veio vazio, dado real so aparece a partir de 11/2025).
DATA_INICIAL = "2025-11-01"

RE_NUMPEDCLI = re.compile(r"^\s*(\d+)\s*/\s*(\S+)\s*$")

MERCOS_AUTO_FETCH = os.getenv("MERCOS_AUTO_FETCH", "1") != "0"


def _baixar_exports_automatico():
    """Login + download automatico dos dois relatorios via mercos_api.py,
    escrevendo nos MESMOS caminhos que o fluxo manual ja usava (VENDAS_PATH
    e PRODUTOS_GLOB) — zero mudanca no resto do parsing. Limpa os
    relatorio*.xls antigos antes de escrever os novos pra nao duplicar
    pedido/item quando as janelas se sobrepoem (setdefault so cria a
    entrada uma vez, mas os itens seriam anexados de novo por arquivo).
    Retorna True se conseguiu, False se falhou (quem chamou decide se cai
    pro fallback dos arquivos manuais que ja existirem)."""
    if not MERCOS_AUTO_FETCH:
        return False
    import mercos_api
    try:
        sessao = mercos_api.login()
    except Exception as e:
        print(f"[AVISO] login automatico na Mercos falhou ({str(e)[:150]}) — usando exports manuais existentes, se houver.")
        return False
    try:
        hoje = date.today()
        data_ini = datetime.strptime(DATA_INICIAL, "%Y-%m-%d").date()

        vendas_bytes = mercos_api.baixar_vendas_detalhadas(sessao, data_ini, hoje)
        os.makedirs(_EXPORTS_DIR, exist_ok=True)
        with open(VENDAS_PATH, "wb") as f:
            f.write(vendas_bytes)

        for antigo in glob.glob(PRODUTOS_GLOB):
            os.remove(antigo)
        partes = mercos_api.baixar_produtos_por_pedido_periodo_completo(sessao, data_ini, hoje)
        for i, conteudo in enumerate(partes, 1):
            caminho = os.path.join(_EXPORTS_DIR, f"relatorio_auto_{i:02d}.xls")
            with open(caminho, "wb") as f:
                f.write(conteudo)

        # Pedidos cancelados no Mercos somem do relatório "Produtos por
        # pedido" (só aceita status=confirmado) e da "Vendas detalhadas"
        # normal (status=confirmado também) — ficavam invisíveis na página
        # inteiros (bug reportado pelo usuário em 2026-09-02, ex: pedido
        # 3525). Busca à parte, só cabeçalho (sem itens, ver
        # _carregar_pedidos_cancelados).
        canceladas_bytes = mercos_api.baixar_vendas_canceladas(sessao, data_ini, hoje)
        with open(VENDAS_CANCELADAS_PATH, "wb") as f:
            f.write(canceladas_bytes)

        print(f"OK - exports da Mercos baixados automaticamente ({len(partes)} parte(s) de produtos, {len(vendas_bytes)} bytes de vendas, {len(canceladas_bytes)} bytes de cancelados)")
        return True
    except Exception as e:
        print(f"[AVISO] download automatico da Mercos falhou ({str(e)[:150]}) — usando exports existentes, se houver.")
        return False


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


def _carregar_pedidos_cancelados():
    """Monta um "pedido" (mesmo formato de _montar_pedidos, pra caber na
    mesma tabela/filtros do front-end) pra cada linha de
    VENDAS_CANCELADAS_PATH — só dá pra preencher cabeçalho (cliente, valor,
    vendedor), sem itens: o relatório de itens não devolve nada pra pedido
    cancelado (ver mercos_api.py::baixar_vendas_canceladas). status_spon
    fica "cancelado", um status novo, à parte do cruzamento com o SPON (esses
    pedidos nunca chegam lá). Best-effort: se o arquivo nao existe (fallback
    manual sem esse export, ou download automatico falhou), devolve lista
    vazia — cancelados simplesmente nao aparecem, igual antes."""
    if not os.path.exists(VENDAS_CANCELADAS_PATH):
        return []
    try:
        vd = pd.read_excel(VENDAS_CANCELADAS_PATH, header=None)
        header_idx = vd[vd[0] == "Data de emissão"].index[0]
        vd_data = vd.iloc[header_idx + 1:].copy()
        vd_data.columns = vd.iloc[header_idx]
        vd_data = vd_data.dropna(subset=["Pedido"])
    except Exception as e:
        print(f"[AVISO] Vendas detalhadas (cancelados) indisponivel ({str(e)[:100]}) — pedidos cancelados nao aparecem")
        return []

    cancelados = []
    for _, row in vd_data.iterrows():
        criador = str(row["Vendedor(a)"]) if pd.notna(row["Vendedor(a)"]) else ""
        cod_vend, _, nome_vend = criador.partition(" - ")
        cancelados.append({
            "numped": str(int(row["Pedido"])),
            "data": str(row["Data de emissão"]),
            "cod_vendedor": cod_vend.strip(),
            "vendedor": nome_vend.strip(),
            "cnpj": str(row["CNPJ/CPF"]),
            "cliente": row["Razão Social"] if pd.notna(row["Razão Social"]) else row["Nome Fantasia"],
            "representada": row["Representada"],
            "itens": [],
            "subtotal_pedido": round(float(row["Total do pedido"]), 2) if pd.notna(row["Total do pedido"]) else 0.0,
            "qt_pedido": 0,
            "status_spon": "cancelado",
            "valor_spon": None,
            "numped_spon": [],
            "numnota_spon": [],
            "itens_cortados": [],
        })
    return cancelados


# CODUSUR do usuário Winthor "W.S" (canal Mercos) é 588 (mesmo valor usado
# em pedidos_mercos.html::RCA_MERCOS pro gate de acesso) — NUMPED no SPON
# é "<CODUSUR><sequencial de 6 dígitos>", confirmado em 2026-09-02 (range
# observado pra pedidos recentes: 588003046-588003531).
_CODUSUR_WS = 588
_NUMPED_MIN_WS = _CODUSUR_WS * 1_000_000
_NUMPED_MAX_WS = _NUMPED_MIN_WS + 999_999


def _carregar_cancelados_spon():
    """Pedidos que chegaram a ganhar NUMPED no SPON (passaram pela
    integração Mercos->Winthor) e foram cancelados LÁ — a fonte é
    SPON.PCNFCANITEM (achada em 2026-09-02 investigando 2 exemplos que o
    usuário deu, que eram NUMPED do SPON, não número de pedido do Mercos).
    Consulta a TABELA BASE direto, não a view SPON.PEDIDOS_CANCELADOS
    (provavelmente a fonte da Rotina 335 do Winthor) — a view tem exatamente
    as mesmas linhas mas NÃO seleciona CODCLI/CODUSUR, que a tabela base tem
    (achado em 2026-09-02, pedido do usuário "não tem o número do pedido do
    cliente?" — a resposta era não, mas isso levou a olhar a DDL da view e
    achar que a tabela por trás tem CODCLI, que a view descarta). Com
    CODCLI dá pra resolver cliente/CNPJ de verdade (JOIN PCCLIENT), sem
    depender do casamento por assinatura de item (_casar_cancelados_spon_com_mercos)
    pra isso — esse casamento continua rodando por cima, só que agora pra
    achar o PEDIDO Mercos (número, vendedor), não mais o cliente.
    status_spon = "cancelado_spon"."""
    query = f"""
        SELECT N.NUMPED, N.CODPROD, PR.DESCRICAO, N.QT, N.PVENDA,
               (NVL(N.QT, 0) * NVL(N.PVENDA, 0)) AS SUBTOT,
               N.MOTIVO, N.DATACANC, N.CODCLI,
               C.CLIENTE, C.FANTASIA, C.CGCENT,
               E.NOME AS FUNCCANCELA
        FROM SPON.PCNFCANITEM N
        JOIN SPON.PCPRODUT PR ON PR.CODPROD = N.CODPROD
        LEFT JOIN SPON.PCCLIENT C ON C.CODCLI = N.CODCLI
        LEFT JOIN SPON.PCEMPR E ON E.MATRICULA = N.CODFUNCCANC
        WHERE N.NUMTRANSVENDA IS NULL
          AND N.NUMPED BETWEEN {_NUMPED_MIN_WS} AND {_NUMPED_MAX_WS}
          AND N.DATACANC >= TO_DATE('{DATA_INICIAL}', 'YYYY-MM-DD')
    """
    try:
        df = carregar_dados(query, engine_spon, "spon_pcnfcanitem")
    except Exception as e:
        print(f"[AVISO] SPON.PCNFCANITEM indisponivel ({str(e)[:100]}) — cancelados do Winthor nao aparecem")
        return []
    if df.empty:
        return []
    df.columns = df.columns.str.upper()
    df["QT"] = pd.to_numeric(df["QT"], errors="coerce").fillna(0)
    df["SUBTOT"] = pd.to_numeric(df["SUBTOT"], errors="coerce").fillna(0)
    df["PVENDA"] = pd.to_numeric(df["PVENDA"], errors="coerce").fillna(0)

    cancelados = []
    for numped, grupo in df.groupby("NUMPED"):
        itens = [{
            "codprod": str(int(r["CODPROD"])) if pd.notna(r["CODPROD"]) else "",
            "descricao": r["DESCRICAO"] or "",
            "qt": float(r["QT"]),
            "preco_liquido": round(float(r["PVENDA"]), 2),
            "subtotal": round(float(r["SUBTOT"]), 2),
        } for _, r in grupo.iterrows()]
        primeira = grupo.iloc[0]
        numped_str = str(int(numped))
        cnpj = re.sub(r"\D", "", str(primeira["CGCENT"])) if pd.notna(primeira["CGCENT"]) else ""
        cliente = primeira["CLIENTE"] or primeira["FANTASIA"]
        cancelados.append({
            "numped": numped_str,
            "data": _fmt_data_spon(primeira["DATACANC"]),
            "cod_vendedor": "",
            "vendedor": "",
            "cnpj": cnpj,
            "cliente": str(cliente).strip() if pd.notna(cliente) and str(cliente).strip() else "Cliente não identificado",
            "representada": "SPON DISTRIBUIDORA",
            "itens": itens,
            "subtotal_pedido": round(sum(it["subtotal"] for it in itens), 2),
            "qt_pedido": sum(it["qt"] for it in itens),
            "status_spon": "cancelado_spon",
            "valor_spon": None,
            "numped_spon": [numped_str],
            "numnota_spon": [],
            "itens_cortados": [],
            "motivo_cancelamento": primeira["MOTIVO"] or "",
            "cancelado_por": primeira["FUNCCANCELA"] or "",
        })
    return cancelados


def _fmt_data_spon(dt):
    if pd.isna(dt):
        return ""
    return pd.Timestamp(dt).strftime("%d/%m/%Y")


def _casar_cancelados_spon_com_mercos(lista_pedidos, cancelados_spon):
    """Casa cada cancelado do SPON (sem cliente) de volta pro pedido MERCOS
    original (com cliente, vendedor etc.) por ASSINATURA de item — conjunto
    de CODPROD é a chave (Oracle raramente repete o mesmo conjunto exato de
    produtos em pedidos diferentes do mesmo período), desempate por
    quantidade batendo exato item a item quando mais de um pedido Mercos
    tem o mesmo conjunto de produtos. Confirmado em 2026-09-02 com os 2
    exemplos do usuário: 588003527 bateu 100% (3/3 itens) com o pedido
    Mercos #3867; 588003525 bateu 3/4 com o #3864 (1 item com quantidade
    diferente — pedido deve ter sido editado no Mercos DEPOIS de já ter
    sido cancelado no Winthor, a integração não avisa o Mercos do
    cancelamento). Exige pelo menos metade dos itens batendo em quantidade
    pra aceitar o match (senão fica no fallback órfão — ver
    _carregar_cancelados_spon) e nunca usa o mesmo pedido Mercos pra mais
    de um cancelado do SPON.

    Muta os pedidos casados em `lista_pedidos` (status_spon vira
    "cancelado_spon" + motivo/quem cancelou/NUMPED do SPON), devolve
    (órfãos, quantidade_casada) — quem chama decide o que fazer com os
    órfãos."""
    def _assinatura(itens):
        return frozenset(it["codprod"] for it in itens)

    indice = {}
    for p in lista_pedidos:
        if p["status_spon"] == "cancelado":  # cancelado no Mercos não tem item pra casar
            continue
        indice.setdefault(_assinatura(p["itens"]), []).append(p)

    usados = set()
    orfaos = []
    casados = 0
    for c in cancelados_spon:
        alvo_qt = {it["codprod"]: it["qt"] for it in c["itens"]}
        candidatos = [p for p in indice.get(frozenset(alvo_qt), []) if p["numped"] not in usados]
        if not candidatos:
            orfaos.append(c)
            continue
        melhor, melhor_score = None, -1
        for p in candidatos:
            qtmap = {it["codprod"]: it["qt"] for it in p["itens"]}
            score = sum(1 for cp, qt in alvo_qt.items() if qtmap.get(cp) == qt)
            if score > melhor_score:
                melhor, melhor_score = p, score
        if melhor_score / len(alvo_qt) < 0.5:
            orfaos.append(c)
            continue
        usados.add(melhor["numped"])
        melhor["status_spon"] = "cancelado_spon"
        melhor["valor_spon"] = None
        melhor["numped_spon"] = c["numped_spon"]
        melhor["numnota_spon"] = []
        melhor["itens_cortados"] = []
        melhor["motivo_cancelamento"] = c["motivo_cancelamento"]
        melhor["cancelado_por"] = c["cancelado_por"]
        casados += 1
    return orfaos, casados


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


def _carregar_inadimplencia_por_codcli():
    """Le inadimplencia_data.js (gerado por exportacao_inadimplencia.py, mesmo
    diretorio — local ou VPS, escrita atomica igual a esse script) e devolve
    so os titulos do sistema SPON (unico sistema que da pra cruzar por CODCLI
    aqui, ver _anexar_inadimplencia), somados por CODCLI. Best-effort: se o
    arquivo nao existe ainda ou os dois pipelines rodaram fora de ordem,
    simplesmente nao marca ninguem como inadimplente (mesmo padrao de
    _carregar_status_logistica)."""
    caminho = Path(__file__).parent / "inadimplencia_data.js"
    if not caminho.exists():
        return {}
    try:
        texto = caminho.read_text(encoding="utf-8")
        payload = json.loads(texto[texto.index("=") + 1: texto.rindex(";")])
    except Exception as e:
        print(f"[AVISO] inadimplencia_data.js indisponivel pra cruzar com pedidos_mercos ({str(e)[:100]}) — ignorado")
        return {}
    por_codcli = {}
    for vend in payload.get("vendedores") or []:
        for t in vend.get("titulos") or []:
            if t.get("sistema") != "SPON" or not t.get("codcli"):
                continue
            acumulado = por_codcli.setdefault(t["codcli"], {"valor_aberto": 0.0, "qtd_titulos": 0, "dias_atraso_max": 0})
            acumulado["valor_aberto"] += float(t.get("valor_aberto") or 0)
            acumulado["qtd_titulos"] += 1
            acumulado["dias_atraso_max"] = max(acumulado["dias_atraso_max"], int(t.get("dias_atraso") or 0))
    for v in por_codcli.values():
        v["valor_aberto"] = round(v["valor_aberto"], 2)
    return por_codcli


def _anexar_inadimplencia(lista_pedidos):
    """Marca cada pedido com inadimplencia = titulo vencido em aberto do
    MESMO cliente (por CNPJ) no SPON — pedido do usuario em 2026-09-02, pra
    ver na hora de mandar o pedido/cobranca se o cliente esta devendo.
    Resolve CODCLI a partir do CNPJ (a mesma tecnica de "cliente nao
    cadastrado" acima, so que pra todos os pedidos, nao so os nao
    encontrados no SPON — o titulo em aberto existe independente do pedido
    do Mercos ja ter sido faturado ou nao)."""
    cnpjs_unicos = sorted({p["cnpj"] for p in lista_pedidos if p.get("cnpj")})
    if not cnpjs_unicos:
        return
    TAMANHO_LOTE = 900
    cnpj_para_codcli = {}
    for inicio in range(0, len(cnpjs_unicos), TAMANHO_LOTE):
        lote = cnpjs_unicos[inicio:inicio + TAMANHO_LOTE]
        cnpjs_sql = ",".join(f"'{c}'" for c in lote)
        query_codcli = f"""
            SELECT DISTINCT REGEXP_REPLACE(CGCENT, '[^0-9]', '') AS CNPJ_LIMPO, CODCLI
            FROM SPON.PCCLIENT
            WHERE REGEXP_REPLACE(CGCENT, '[^0-9]', '') IN ({cnpjs_sql})
        """
        try:
            df_codcli = carregar_dados(query_codcli, engine_spon, "spon_cnpj_codcli")
            df_codcli.columns = df_codcli.columns.str.upper()
            for _, row in df_codcli.iterrows():
                cnpj_para_codcli[row["CNPJ_LIMPO"]] = str(int(row["CODCLI"]))
        except Exception as e:
            print(f"[AVISO] resolucao de CODCLI por CNPJ indisponivel ({str(e)[:100]}) — pedidos ficam sem inadimplencia")
            return

    inadimplencia_por_codcli = _carregar_inadimplencia_por_codcli()
    for p in lista_pedidos:
        codcli = cnpj_para_codcli.get(p["cnpj"])
        info = inadimplencia_por_codcli.get(codcli) if codcli else None
        p["inadimplente"] = info is not None
        p["inadimplencia_valor"] = info["valor_aberto"] if info else None
        p["inadimplencia_qtd_titulos"] = info["qtd_titulos"] if info else None
        p["inadimplencia_dias_atraso"] = info["dias_atraso_max"] if info else None


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
    _baixar_exports_automatico()

    if not _listar_arquivos_produtos() or not os.path.exists(VENDAS_PATH):
        print(f"[AVISO] arquivos de exportacao da Mercos nao encontrados em {_EXPORTS_DIR} — "
              f"pulando (download automatico falhou e nao ha export manual pra usar de fallback).")
        return

    pedido_info = _carregar_pedido_info()
    lista_pedidos = _montar_pedidos(pedido_info)
    _cruzar_com_spon(lista_pedidos)
    _anexar_status_logistica(lista_pedidos)

    # Cancelados entram DEPOIS do cruzamento com o SPON (eles nunca chegam
    # lá — _cruzar_com_spon ia sobrescrever o status_spon="cancelado" pra
    # "nao_encontrado") e DEPOIS de _anexar_status_logistica (que também
    # itera lista_pedidos inteira; cancelado não tem nota, fica só com os
    # campos de logística vazios, que é o esperado).
    numpeds_existentes = {p["numped"] for p in lista_pedidos}
    cancelados = [c for c in _carregar_pedidos_cancelados() if c["numped"] not in numpeds_existentes]
    if cancelados:
        print(f"{len(cancelados)} pedido(s) cancelado(s) no Mercos incluido(s)")
    lista_pedidos += cancelados
    numpeds_existentes.update(c["numped"] for c in cancelados)

    orfaos_spon, casados_spon = _casar_cancelados_spon_com_mercos(lista_pedidos, _carregar_cancelados_spon())
    if casados_spon:
        print(f"{casados_spon} pedido(s) cancelado(s) no SPON (Winthor) casado(s) com pedido Mercos existente")
    orfaos_spon = [c for c in orfaos_spon if c["numped"] not in numpeds_existentes]
    if orfaos_spon:
        print(f"{len(orfaos_spon)} pedido(s) cancelado(s) no SPON sem pedido Mercos correspondente (fica so com o NUMPED do SPON)")
    lista_pedidos += orfaos_spon

    _anexar_inadimplencia(lista_pedidos)

    def _data_ordenavel(data_br):
        # "data" vem como string dd/mm/yyyy (ver _montar_pedidos) — ordenar
        # a string crua dá prioridade ao dia, ignorando mês/ano (bug real:
        # "01/09/2026" ordenava ANTES de "31/08/2026" porque "0" < "3",
        # escondendo pedidos do dia atual no meio da lista em vez do topo).
        d, _, resto = data_br.partition("/")
        m, _, a = resto.partition("/")
        return (a, m, d)

    lista_pedidos.sort(key=lambda p: (_data_ordenavel(p["data"]), p["numped"]), reverse=True)

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
