"""
Gera perini_vendas_data.js — vendas recentes de Casa Perini (CRC + SPON,
únicas bases com os códigos mapeados em preco_promo.html) pelo time OFF
TRADE, comparadas contra o preço estipulado (a mesma escada de faixas do
quadro de condições em preco_promo.html — ver PERINI_FAIXAS abaixo, MANTER
EM SINCRONIA MANUAL com PERINI_CONDICOES em preco_promo.html: não há fonte
única hoje, o preço promo é cadastro à parte, não vem do Winthor).

Pedido do usuário em 2026-09-15: "vamos criar uma tela com as últimas
vendas do off trade que tenham vendas de perini, vamos verificar se bate
com os preços estipulados".

Regra de comparação (confirmada com o usuário): a faixa de preço é do
PEDIDO inteiro — soma a quantidade de todos os itens Perini do mesmo
NUMPED pra achar a faixa, não por item isolado (mesma regra já implementada
no simulador de preco_promo.html). Estado (RJ/SP) vem do PCUSUARI.ESTADO do
vendedor que fez a venda, não do cliente — mesma convenção usada em outras
páginas do projeto (ver project_vendedor_cliente_estado_diferente).

Pedido acima de 1.000 cxs (mesmo limite do aviso "falar com o Daniel" no
simulador) é tratado como negociação especial — preço fora da escada não
conta como divergência, é esperado.

Preço "Acelerado" só é válido se o MESMO pedido (NUMPED) também tiver uma
linha de Espumante Alud — achado real com o usuário em 2026-09-15: pedido
378000696 (13 cxs reais, faixa "10-350") foi vendido a R$32,90 (o Acelerado
dessa faixa) mas SEM nenhum Alud no pedido (só Martini + os 3 Perini,
confirmado direto no PCMOV) — R$32,90 sem Alud é divergência, não desconto
válido. Sem isso, o Acelerado de uma faixa e o Preço Tabela da faixa de
cima costumam empatar (ex: acelerado de "10-350" = 32,90 = tabela de
"acima de 350"), mascarando pedido pequeno usando preço de pedido grande.

Janela: últimos 90 dias (mov mais antigo que isso não é mais "vendas
recentes" pra fim de auditoria).
"""
import json
import os
import re
from datetime import datetime
from pathlib import Path

import pandas as pd

from meta import engine, engine_spon, engine_castas, carregar_dados

OUT_JS = str(Path(__file__).parent / "perini_vendas_data.js")

JANELA_DIAS = 90
LIMITE_NEGOCIACAO_ESPECIAL = 1000

# codprod por sistema (ver preco_promo.html::PERINI_CODIGOS — mesma lista,
# mantida à parte aqui porque esse script não roda no navegador).
PERINI_CODIGOS_POR_SISTEMA = {
    "CRC": [663, 666, 2781, 664, 665, 2780, 667, 3319],
    "SPON": [663, 666, 2781, 664,665, 2780, 667, 2779],
}

# Espumante Alud (crédito que "acelera" o pedido pro preço de faixa maior)
# — pode ter sido comprado em CRC, SPON ou CASTAS, não necessariamente na
# mesma base do pedido Perini nem no mesmo NUMPED (achado + pedido do
# usuário em 2026-09-15: casa por CNPJ do cliente + mesmo mês do pedido).
ALUD_CODIGOS_POR_SISTEMA = {
    "CRC": [3011, 3063, 7763],
    "SPON": [3146, 3286, 4011, 4012],
    "CASTAS": [3011, 3063],
}

# Mesma escada de PERINI_CONDICOES em preco_promo.html — manter em
# sincronia manual se a condição comercial mudar lá.
PERINI_FAIXAS = {
    "RJ": [
        {"volume_min": 0, "volume_max": 10, "preco": 34.30, "acelerador": 33.50},
        {"volume_min": 10, "volume_max": 350, "preco": 33.50, "acelerador": 32.90},
        {"volume_min": 350, "volume_max": None, "preco": 32.90, "acelerador": 32.75},
    ],
    "SP": [
        {"volume_min": 0, "volume_max": 100, "preco": 33.50, "acelerador": 32.90},
        {"volume_min": 100, "volume_max": None, "preco": 32.90, "acelerador": 32.75},
    ],
}

# Prazo especial NÃO é por faixa de volume, é por MÊS da venda — achado real
# corrigido com o usuário em 2026-09-15: "comprando em setembro, prazo é
# 30/60/90 ... cada mês que entra é um mês a menos pra pagamento": outubro
# só 30/60, novembro só 60, dezembro só 30. Meses fora de set-dez não têm
# prazo especial definido (a escada só existe nesses 4 meses, mesmo padrão
# do rodapé original da planilha "Set/Out/Nov e Dez").
PRAZO_ESPECIAL_POR_MES = {
    9: (30, 60, 90),
    10: (30, 60),
    11: (60,),
    12: (30,),
}

# Achado real testando com dado de venda (2026-09-15): comparar só contra
# "Preço Tabela" dava divergência em ~97% dos pedidos — na prática quase
# toda venda usa o preço ACELERADO (com Alud), não o tabela puro, e boa
# parte nem é venda com promo (cliente não elegível pro OTD/OTI, ver
# project_otd_oti_bases — paga tabela cheia normal, sem relação com essa
# escada). Os PUNIT reais formam um gap limpo entre 34,90 e 35,90 — abaixo
# disso é tentativa de promo (compara contra tabela OU acelerador da faixa),
# acima é tabela cheia (nem entra na comparação, não é divergência).
CORTE_PRECO_PROMO = 35.0
TOLERANCIA = 0.05

_BASES = [("CRC", engine), ("SPON", engine_spon)]
_BASES_ALUD = [("CRC", engine), ("SPON", engine_spon), ("CASTAS", engine_castas)]


def _faixa_para_volume(faixas, qtd):
    for f in faixas:
        if qtd >= f["volume_min"] and (f["volume_max"] is None or qtd <= f["volume_max"]):
            return f
    return None


def _dias_do_prazo(texto):
    """Extrai a sequência de dias de um texto de prazo ('Set: 30/60/90' ou
    'PCPLPAG.DESCRICAO' tipo '30/60/90 DIAS') pra comparar os dois sem
    depender do rótulo de mês na frente — pedido do usuário em 2026-09-15:
    "precisa respeitar o prazo especial", não só o preço. 'A VISTA'/sem
    nenhum número vira (0,) (pagamento imediato); None se o texto vier vazio."""
    if not texto:
        return None
    dias = tuple(int(n) for n in re.findall(r"\d+", texto))
    if dias:
        return dias
    return (0,) if "VISTA" in texto.upper() else None


def _cnpjs_mes_com_alud():
    """Devolve um dict {(cnpj_limpo, 'AAAA-MM'): [compras de Alud]} — usado
    pra validar o preço Acelerado de um pedido Perini de outro sistema/base
    (o cliente pode comprar o Alud numa base e o Perini em outra) e pra
    mostrar o detalhe (sistema, pedido, valor, quantidade) quando o usuário
    clica no ✅ da coluna Alud — pedido do usuário em 2026-09-15."""
    compras = {}
    for sistema, eng in _BASES_ALUD:
        codigos = ",".join(str(c) for c in ALUD_CODIGOS_POR_SISTEMA[sistema])
        query = f"""
            SELECT M.DTMOV, M.NUMPED, M.NUMNOTA, M.QT, M.PUNIT, P.QTUNITCX,
                   REGEXP_REPLACE(C.CGCENT, '[^0-9]', '') AS CNPJ
            FROM {sistema}.PCMOV M
            LEFT JOIN {sistema}.PCCLIENT C ON C.CODCLI = M.CODCLI
            LEFT JOIN {sistema}.PCPRODUT P ON P.CODPROD = M.CODPROD
            WHERE M.CODPROD IN ({codigos})
              AND M.CODOPER IN ('S', 'SB')
              AND M.NUMNOTADEV IS NULL
              AND M.DTCANCEL IS NULL
              AND M.DTMOV >= SYSDATE - {JANELA_DIAS}
        """
        try:
            df = carregar_dados(query, eng, f"perini_vendas_alud_{sistema}")
        except Exception as e:
            print(f"[AVISO] Alud ({sistema}) indisponível ({str(e)[:150]}) — ignorado (pode gerar falso divergente)")
            continue
        df.columns = df.columns.str.upper()
        df["QTUNITCX"] = pd.to_numeric(df["QTUNITCX"], errors="coerce")
        df["QT"] = pd.to_numeric(df["QT"], errors="coerce").fillna(0)
        df["PUNIT"] = pd.to_numeric(df["PUNIT"], errors="coerce").fillna(0)
        df["QT_CX"] = df["QT"] / df["QTUNITCX"].where(df["QTUNITCX"] > 0, 1)
        for _, r in df.iterrows():
            if not r["CNPJ"]:
                continue
            mes = pd.Timestamp(r["DTMOV"]).strftime("%Y-%m")
            compras.setdefault((r["CNPJ"], mes), []).append({
                "sistema": sistema,
                "numped": str(int(r["NUMPED"])) if pd.notna(r["NUMPED"]) else "",
                "numnota": str(int(r["NUMNOTA"])) if pd.notna(r["NUMNOTA"]) else "",
                "qt": round(float(r["QT_CX"]), 2),
                "qt_un": float(r["QT"]),
                "valor": round(float(r["QT"]) * float(r["PUNIT"]), 2),
            })
    return compras


def _carregar_vendas(sistema, eng):
    codigos = ",".join(str(c) for c in PERINI_CODIGOS_POR_SISTEMA[sistema])
    # M.QT é em UNIDADE (garrafa), não caixa — achado real testando com o
    # usuário em 2026-09-15, ~6x inflava a quantidade total do pedido e
    # jogava pedido pequeno numa faixa bem maior que a real. PCPRODUT.QTUNITCX
    # dá quantas unidades tem 1 caixa desse produto (6 pra todo Perini,
    # confirmado em CRC e SPON) — converte pra caixa antes de somar.
    query = f"""
        SELECT M.DTMOV, M.NUMNOTA, M.NUMPED, M.CODPROD, M.DESCRICAO, M.QT, M.PUNIT,
               M.CODCLI, C.CLIENTE, C.FANTASIA, REGEXP_REPLACE(C.CGCENT, '[^0-9]', '') AS CNPJ,
               M.CODUSUR, U.NOME AS VENDEDOR, U.ESTADO, P.QTUNITCX,
               M.CODPLPAG, PL.DESCRICAO AS PLANO_PAGAMENTO
        FROM {sistema}.PCMOV M
        LEFT JOIN {sistema}.PCCLIENT C ON C.CODCLI = M.CODCLI
        LEFT JOIN {sistema}.PCUSUARI U ON U.CODUSUR = M.CODUSUR
        LEFT JOIN {sistema}.PCPRODUT P ON P.CODPROD = M.CODPROD
        LEFT JOIN {sistema}.PCPLPAG PL ON PL.CODPLPAG = M.CODPLPAG
        WHERE M.CODPROD IN ({codigos})
          AND M.CODOPER IN ('S', 'SB')
          AND M.NUMNOTADEV IS NULL
          AND M.DTCANCEL IS NULL
          AND M.DTMOV >= SYSDATE - {JANELA_DIAS}
          AND U.NOME LIKE '%OFF TRADE%'
    """
    df = carregar_dados(query, eng, f"perini_vendas_{sistema}")
    df.columns = df.columns.str.upper()
    df["SISTEMA"] = sistema
    return df


def _montar_pedidos(df, cnpjs_mes_com_alud):
    pedidos = []
    df = df.copy()
    df["QT"] = pd.to_numeric(df["QT"], errors="coerce").fillna(0)
    df["PUNIT"] = pd.to_numeric(df["PUNIT"], errors="coerce").fillna(0)
    # QT vem em unidade (garrafa) — converte pra caixa usando QTUNITCX do
    # cadastro do produto (fallback 1 = trata como já sendo caixa, só se o
    # cadastro não tiver o dado, pra nunca dividir por zero/None).
    df["QTUNITCX"] = pd.to_numeric(df["QTUNITCX"], errors="coerce")
    df["QT_CX"] = df["QT"] / df["QTUNITCX"].where(df["QTUNITCX"] > 0, 1)

    for (sistema, numped), grupo in df.groupby(["SISTEMA", "NUMPED"]):
        primeira = grupo.iloc[0]
        estado = (primeira["ESTADO"] or "").strip().upper()
        estado = estado if estado in ("RJ", "SP") else None
        qtd_total = round(float(grupo["QT_CX"].sum()), 2)
        negociacao_especial = qtd_total > LIMITE_NEGOCIACAO_ESPECIAL

        # Preço Acelerado só conta se o cliente (por CNPJ, em CRC/SPON/CASTAS
        # — não precisa ser o mesmo pedido nem a mesma base do Perini)
        # comprou Espumante Alud no mesmo mês do pedido — achado real com o
        # usuário em 2026-09-15: sem isso, Acelerado de uma faixa empata
        # com o Preço Tabela da faixa de cima e mascara pedido pequeno
        # "pagando" preço de pedido grande sem nunca ter levado Alud junto.
        cnpj = (primeira["CNPJ"] or "").strip()
        data_pedido = pd.Timestamp(grupo["DTMOV"].max())
        mes_pedido = data_pedido.strftime("%Y-%m")
        compras_alud = cnpjs_mes_com_alud.get((cnpj, mes_pedido), []) if cnpj else []
        tem_alud = bool(compras_alud)

        faixa = _faixa_para_volume(PERINI_FAIXAS[estado], qtd_total) if estado else None
        precos_validos = {faixa["preco"]} if faixa else set()
        if faixa and tem_alud:
            precos_validos.add(faixa["acelerador"])

        # Prazo especial é por mês da venda, não pela faixa de volume (ver
        # PRAZO_ESPECIAL_POR_MES) — meses fora de set-dez não têm escada,
        # dias_esperado fica None (não checa prazo nesses meses).
        dias_esperado = PRAZO_ESPECIAL_POR_MES.get(data_pedido.month)
        prazo_especial_esperado = "/".join(str(d) for d in dias_esperado) + " dias" if dias_esperado else None

        itens = []
        tem_divergencia = False
        for _, r in grupo.iterrows():
            punit = round(float(r["PUNIT"]), 2)
            if negociacao_especial or faixa is None:
                status_item = "negociacao_especial" if negociacao_especial else "sem_faixa"
            elif punit >= CORTE_PRECO_PROMO:
                status_item = "sem_promo"  # tabela cheia, cliente não usou a escada
            elif any(abs(punit - p) <= TOLERANCIA for p in precos_validos):
                status_item = "ok"
            else:
                status_item = "divergente"
                tem_divergencia = True

            # Prazo especial é independente do preço — checa mesmo quando o
            # pedido pagou tabela cheia (achado com o usuário em 2026-09-15:
            # "sem promo" na coluna de preço não deve pular a checagem de
            # prazo, são coisas separadas). Só pula mesmo pra negociação
            # especial e pedido sem faixa (estado desconhecido), onde não
            # há prazo especial de referência pra comparar.
            plano_pagamento = (r["PLANO_PAGAMENTO"] or "").strip()
            if status_item in ("negociacao_especial", "sem_faixa"):
                status_prazo = status_item
            elif dias_esperado is None:
                status_prazo = "fora_da_escada"  # mês sem prazo especial definido (jan-ago)
            else:
                dias_vendido = _dias_do_prazo(plano_pagamento)
                if dias_vendido is None:
                    status_prazo = "sem_dado"
                # Prazo MENOR que o esperado é OK — cliente pagando mais
                # rápido que o máximo permitido nunca é problema, só o
                # contrário (pedido do usuário em 2026-09-15: "28 DIAS" <
                # "Dez: 30d" e "21/28/35 DIAS" < "Out: 30/60" são OK, porque
                # o prazo vendido é menor/igual, não maior). Compara pelo
                # último parcelamento (o mais longo) de cada lado.
                elif max(dias_vendido) <= max(dias_esperado):
                    status_prazo = "ok"
                else:
                    status_prazo = "divergente"
                    tem_divergencia = True

            itens.append({
                "codprod": str(int(r["CODPROD"])),
                "descricao": (r["DESCRICAO"] or "").strip(),
                "qt": round(float(r["QT_CX"]), 2),
                # Venda fracionada (menos de 1 caixa) fica melhor em unidade
                # (garrafa) do que "0,17 cxs" — pedido do usuário em
                # 2026-09-15. Front-end decide qual mostrar conforme qt < 1.
                "qt_un": float(r["QT"]),
                "punit": punit,
                "preco_tabela": faixa["preco"] if faixa else None,
                "preco_acelerado": faixa["acelerador"] if faixa else None,
                "status": status_item,
                "numnota": str(int(r["NUMNOTA"])) if pd.notna(r["NUMNOTA"]) else "",
                "plano_pagamento": plano_pagamento,
                "status_prazo": status_prazo,
            })

        pedidos.append({
            "sistema": sistema,
            "numped": str(int(numped)) if pd.notna(numped) else "",
            "data": pd.Timestamp(grupo["DTMOV"].max()).strftime("%d/%m/%Y"),
            "codcli": str(int(primeira["CODCLI"])) if pd.notna(primeira["CODCLI"]) else "",
            "cliente": (primeira["CLIENTE"] or primeira["FANTASIA"] or "").strip() or "—",
            "codusur": str(int(primeira["CODUSUR"])) if pd.notna(primeira["CODUSUR"]) else "",
            "vendedor": (primeira["VENDEDOR"] or "").strip(),
            "estado": estado,
            "qtd_total": qtd_total,
            "qtd_total_un": round(float(grupo["QT"].sum()), 2),
            "faixa_esperada": (
                f"{faixa['volume_min']}-{faixa['volume_max'] or '+'}" if faixa else None
            ),
            "preco_tabela": faixa["preco"] if faixa else None,
            "preco_acelerado": faixa["acelerador"] if faixa else None,
            "prazo_especial_esperado": prazo_especial_esperado,
            "tem_alud": tem_alud,
            "alud_compras": compras_alud,
            "negociacao_especial": negociacao_especial,
            "divergente": tem_divergencia,
            "itens": itens,
        })

    return pedidos


def main():
    fontes_indisponiveis = []
    partes = []
    for sistema, eng in _BASES:
        try:
            partes.append(_carregar_vendas(sistema, eng))
        except Exception as e:
            print(f"[AVISO] vendas Perini ({sistema}) indisponível ({str(e)[:150]}) — ignorado")
            fontes_indisponiveis.append(f"perini_vendas_{sistema}")

    if not partes:
        print("[AVISO] Nenhuma base disponível — perini_vendas_data.js não será atualizado.")
        return

    cnpjs_mes_com_alud = _cnpjs_mes_com_alud()
    print(f"OK - {len(cnpjs_mes_com_alud)} combinação(ões) cliente/mês com Alud comprado (CRC+SPON+CASTAS)")

    df = pd.concat(partes, ignore_index=True)
    pedidos = _montar_pedidos(df, cnpjs_mes_com_alud)
    # "data" é string dd/mm/yyyy — ordenar a string crua dá prioridade ao
    # dia, ignorando mês/ano (mesmo bug já corrigido em
    # gerar_pedidos_mercos_data.py::_data_ordenavel).
    def _data_ordenavel(data_br):
        d, _, resto = data_br.partition("/")
        m, _, a = resto.partition("/")
        return (a, m, d)

    pedidos.sort(key=lambda p: _data_ordenavel(p["data"]), reverse=True)

    payload = {
        "atualizado_em": datetime.now().strftime("%d/%m/%Y %H:%M"),
        "janela_dias": JANELA_DIAS,
        "fontes_indisponiveis": fontes_indisponiveis,
        "pedidos": pedidos,
    }

    tmp = OUT_JS + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write("const PERINI_VENDAS_DATA = ")
        json.dump(payload, f, ensure_ascii=False)
        f.write(";\n")
    os.replace(tmp, OUT_JS)

    total_divergentes = sum(1 for p in pedidos if p["divergente"])
    print(f"OK - {len(pedidos)} pedido(s) Perini, {total_divergentes} com divergência de preço")

    import subprocess
    from pathlib import Path as _Path
    repo_dir = str(_Path(__file__).parent)
    subprocess.run(["git", "-C", repo_dir, "add", "perini_vendas_data.js"], check=False)
    subprocess.run(["git", "-C", repo_dir, "commit", "-m",
                     f"Atualiza perini_vendas_data.js - {datetime.now().strftime('%d/%m/%Y %H:%M')}"], check=False)
    subprocess.run(["git", "-C", repo_dir, "push", "origin", "master"], check=False)


if __name__ == "__main__":
    main()
