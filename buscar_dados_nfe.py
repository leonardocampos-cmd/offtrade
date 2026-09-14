"""
buscar_dados_nfe.py — busca os dados de uma ou mais NFes de saída do SPON pra montar o DANFE
em pedidos_mercos.html (botão "📄 DANFE"). Chamado como subprocesso por
pedidos_mercos_api.py::dados_nfe() (mesmo padrão de gerar_estoque_mercos_spon_data.py via
/sincronizar-estoque) — pedidos_mercos_api.py roda num venv leve sem Oracle, então toda consulta
Oracle desse serviço passa por aqui.

Views usadas (schema SPON, não OFFTRADE — o usuário Oracle deste projeto não é dono delas, por
isso todo FROM abaixo tem o prefixo SPON. explícito; sem ele dá ORA-00942, confirmado em
2026-09-11): SQL_NFE_CABECALHO_SAIDA / SQL_NFE_PRODUTO_SAIDA / SQL_NFE_RODAPE_SAIDA — views
prontas do Winthor com tudo que o DANFE precisa, sem certificado digital nem consulta à SEFAZ.

Junta por NUM_PEDIDO (não NUMERO_NOTA): confirmado em 2026-09-11 que NUMERO_NOTA se repete entre
CODIGO_FILIAL diferentes (a numeração reinicia por filial/série — ex: NUMERO_NOTA 8673 existe
tanto em 2021/filial 1 quanto em 2026/filial 2). NUM_PEDIDO é único de verdade — é o mesmo
"numped_spon" que gerar_pedidos_mercos_data.py já resolve pra cada pedido Mercos.

VALOR_COMERCIAL/VALOR_LIQUIDO em SQL_NFE_PRODUTO_SAIDA são valor UNITÁRIO, não o total da linha
(confirmado 2026-09-11 comparando contra QUANTIDADE_COMERCIAL e BASE_ICMS num pedido real de 3
unidades) — o valor total de cada item é calculado aqui (qtd × unitário − desconto), não lido
direto de nenhuma coluna.

Uso: python buscar_dados_nfe.py <numped1>,<numped2>,...
Saída: um único JSON no stdout, sem prints de diagnóstico misturados (diferente do padrão usado
em gerar_estoque_mercos_spon_data.py, que usa regex pra extrair um resumo do meio de logs) —
{"ok": true, "notas": [...]} ou {"ok": false, "motivo": "..."}, sempre saindo com código 0 (o
chamador decide o que fazer com "ok": false).
"""
import json
import sys

import pandas as pd

from meta import engine_spon, carregar_dados


def _vazio(v):
    try:
        return v is None or pd.isna(v)
    except (TypeError, ValueError):
        return False


def _num(v):
    if _vazio(v):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _int_str(v):
    if _vazio(v):
        return ""
    try:
        return str(int(v))
    except (TypeError, ValueError):
        return str(v)


def _txt(v):
    return "" if _vazio(v) else str(v)


def _dt(v, fmt="%d/%m/%Y"):
    if _vazio(v):
        return None
    try:
        return v.strftime(fmt)
    except Exception:
        return None


def _cnpj_cpf(v):
    if _vazio(v):
        return ""
    return "".join(c for c in str(v) if c.isdigit())


def _first(*vals):
    for v in vals:
        if not _vazio(v):
            return v
    return None


def _bloco_endereco(row, sufixo):
    return {
        "cnpj_cpf": _cnpj_cpf(_first(row.get(f"CNPJ_{sufixo}"), row.get(f"CNPJ_CPF_{sufixo}"))),
        "razao_social": _txt(row.get(f"RAZAO_SOCIAL_{sufixo}")),
        "nome_fantasia": _txt(row.get(f"NOME_FANTASIA_{sufixo}")),
        "logradouro": _txt(row.get(f"LOGRADOURO_{sufixo}")),
        "numero": _txt(row.get(f"NUMERO_{sufixo}")),
        "complemento": _txt(row.get(f"COMPLEMENTO_{sufixo}")),
        "bairro": _txt(row.get(f"BAIRRO_{sufixo}")),
        "municipio": _txt(row.get(f"NOME_MUNICIPIO_{sufixo}")),
        "uf": _txt(row.get(f"SIGLA_UF_{sufixo}")),
        "cep": _txt(row.get(f"CEP_{sufixo}")),
        "telefone": _txt(row.get(f"TELEFONE_{sufixo}")),
        "ie": _txt(row.get(f"INSCRICAO_ESTADUAL_{sufixo}")),
    }


def buscar(numpeds):
    numpeds_int = sorted({int(n) for n in numpeds if str(n).strip().isdigit()})
    if not numpeds_int:
        return {"ok": False, "motivo": "Nenhum NUM_PEDIDO válido informado."}

    lista_in = ",".join(str(n) for n in numpeds_int)
    cab = carregar_dados(
        f"SELECT * FROM SPON.SQL_NFE_CABECALHO_SAIDA WHERE NUM_PEDIDO IN ({lista_in})",
        engine_spon, "nfe_cabecalho",
    )
    if cab.empty:
        return {"ok": False, "motivo": "Nenhuma nota encontrada pra esse(s) pedido(s) no SPON."}
    cab.columns = cab.columns.str.upper()

    transacoes = sorted({int(t) for t in cab["NUM_TRANSACAO"].tolist()})
    lista_trans = ",".join(str(t) for t in transacoes)

    prod = carregar_dados(
        f"""SELECT NUM_TRANSACAO, NUMERO_SEQUENCIA, CODPROD, PRODUTO, NCM, CFOP,
                   SITUACAO_TRIBUTARIA, UNIDADE_COMERCIAL, QUANTIDADE_COMERCIAL,
                   VALOR_COMERCIAL, VALOR_DESCONTO, BASE_ICMS, VALOR_ICMS, ALIQUOTA_ICMS,
                   VALOR_IPI, ALIQUOTA_IPI
            FROM SPON.SQL_NFE_PRODUTO_SAIDA WHERE NUM_TRANSACAO IN ({lista_trans})
            ORDER BY NUM_TRANSACAO, NUMERO_SEQUENCIA""",
        engine_spon, "nfe_produtos",
    )
    prod.columns = prod.columns.str.upper()

    rod = carregar_dados(
        f"""SELECT NUM_TRANSACAO, VALOR_TOTAL_PRODUTOS, VALOR_FRETE, VALOR_DESCONTO,
                   VALOR_IPI, VALOR_TOTAL, BASE_ICMS, VALOR_ICMS, BASE_ICMS_ST, VALOR_ICMS_ST,
                   INFORMACAOADICIONALFISCO, TRANSPORTADOR, CNPJ_TRANSPORTADOR,
                   END_TRANSPORTADOR, CIDADE_TRANSPORTADOR, ESTADO_TRANSPORTADOR,
                   MODALIDADE_FRETE, PLACA, PLACA_UF, NUM_VOLUME, PESO_BRUTO, PESO_LIQUIDO
            FROM SPON.SQL_NFE_RODAPE_SAIDA WHERE NUM_TRANSACAO IN ({lista_trans})""",
        engine_spon, "nfe_rodape",
    )
    rod.columns = rod.columns.str.upper()

    # Fatura/Duplicata (rodapé faltando no DANFE reconstruído — pedido do
    # usuário em 2026-09-14, comparando contra o modelo oficial). Winthor
    # divide por forma de cobrança em 6 views separadas (uma nota só bate em
    # UMA delas, dependendo de boleto/cartão/dinheiro/etc) — UNION ALL nas
    # 6 pra não ter que adivinhar qual usar.
    parc = carregar_dados(
        f"""SELECT NUM_TRANSACAO, PREST, DTVENC, VALOR FROM SPON.SQL_NFE_PARCELA_SAIDA_NORMAL WHERE NUM_TRANSACAO IN ({lista_trans})
            UNION ALL
            SELECT NUM_TRANSACAO, PREST, DTVENC, VALOR FROM SPON.SQL_NFE_PARCELA_SAIDA_CARTAO WHERE NUM_TRANSACAO IN ({lista_trans})
            UNION ALL
            SELECT NUM_TRANSACAO, PREST, DTVENC, VALOR FROM SPON.SQL_NFE_PARCELA_SAIDA_DESD WHERE NUM_TRANSACAO IN ({lista_trans})
            UNION ALL
            SELECT NUM_TRANSACAO, PREST, DTVENC, VALOR FROM SPON.SQL_NFE_PARCELA_SAIDA_DIN WHERE NUM_TRANSACAO IN ({lista_trans})
            UNION ALL
            SELECT NUM_TRANSACAO, PREST, DTVENC, VALOR FROM SPON.SQL_NFE_PARCELA_SAIDA_ST WHERE NUM_TRANSACAO IN ({lista_trans})
            UNION ALL
            SELECT NUM_TRANSACAO, PREST, DTVENC, VALOR FROM SPON.SQL_NFE_PARCELA_SAIDA_TROCO WHERE NUM_TRANSACAO IN ({lista_trans})
            ORDER BY 1, 2""",
        engine_spon, "nfe_parcelas",
    )
    parc.columns = parc.columns.str.upper()

    prod_por_transacao = {}
    for _, r in prod.iterrows():
        prod_por_transacao.setdefault(int(r["NUM_TRANSACAO"]), []).append(r)
    rod_por_transacao = {int(r["NUM_TRANSACAO"]): r for _, r in rod.iterrows()}
    parc_por_transacao = {}
    for _, r in parc.iterrows():
        parc_por_transacao.setdefault(int(r["NUM_TRANSACAO"]), []).append(r)

    notas = []
    for _, c in cab.iterrows():
        nt = int(c["NUM_TRANSACAO"])
        itens = []
        for it in prod_por_transacao.get(nt, []):
            qt = _num(it["QUANTIDADE_COMERCIAL"]) or 0.0
            valor_unit = _num(it["VALOR_COMERCIAL"]) or 0.0
            desconto = _num(it["VALOR_DESCONTO"]) or 0.0
            itens.append({
                "codigo": _int_str(it["CODPROD"]),
                "descricao": _txt(it["PRODUTO"]),
                "ncm": _txt(it["NCM"]),
                "cfop": _int_str(it["CFOP"]),
                "cst": _txt(it["SITUACAO_TRIBUTARIA"]),
                "unidade": _txt(it["UNIDADE_COMERCIAL"]),
                "quantidade": qt,
                "valor_unitario": valor_unit,
                "valor_total": round(qt * valor_unit - desconto, 2),
                "valor_desconto": desconto,
                "base_icms": _num(it["BASE_ICMS"]) or 0.0,
                "valor_icms": _num(it["VALOR_ICMS"]) or 0.0,
                "aliquota_icms": _num(it["ALIQUOTA_ICMS"]) or 0.0,
                "valor_ipi": _num(it["VALOR_IPI"]) or 0.0,
                "aliquota_ipi": _num(it["ALIQUOTA_IPI"]) or 0.0,
            })

        r = rod_por_transacao.get(nt)
        if r is None:
            r = pd.Series(dtype=object)

        notas.append({
            "numped": _int_str(c["NUM_PEDIDO"]),
            "numero": _int_str(c["NUMERO_NOTA"]),
            "serie": _txt(c["SERIE"]),
            "chave": _txt(c["CHAVENFE"]),
            "protocolo": _txt(c["PROTOCOLONFE"]),
            "dthora_autorizacao": _dt(c["DTHORA_AUTORIZACAO"], "%d/%m/%Y %H:%M:%S"),
            "data_emissao": _dt(c["DATA_EMISSAO"]),
            "data_saida": _dt(c["DATA_SAIDA"]),
            "hora_saida": _txt(c["HORA"]),
            "natureza_operacao": _txt(c["NATUREZA_OP"]),
            "emitente": _bloco_endereco(c, "E"),
            "destinatario": _bloco_endereco(c, "D"),
            "fatura": [
                {
                    "prestacao": _int_str(p.get("PREST")),
                    "vencimento": _dt(p.get("DTVENC")),
                    "valor": _num(p.get("VALOR")) or 0.0,
                }
                for p in parc_por_transacao.get(nt, [])
            ],
            "itens": itens,
            "totais": {
                "valor_produtos": _num(r.get("VALOR_TOTAL_PRODUTOS")) or 0.0,
                "valor_frete": _num(r.get("VALOR_FRETE")) or 0.0,
                "valor_desconto": _num(r.get("VALOR_DESCONTO")) or 0.0,
                "valor_ipi": _num(r.get("VALOR_IPI")) or 0.0,
                "valor_total": _num(r.get("VALOR_TOTAL")) or 0.0,
                "base_icms": _num(r.get("BASE_ICMS")) or 0.0,
                "valor_icms": _num(r.get("VALOR_ICMS")) or 0.0,
                "base_icms_st": _num(r.get("BASE_ICMS_ST")) or 0.0,
                "valor_icms_st": _num(r.get("VALOR_ICMS_ST")) or 0.0,
            },
            "transportador": {
                "nome": _txt(r.get("TRANSPORTADOR")),
                "cnpj": _cnpj_cpf(r.get("CNPJ_TRANSPORTADOR")),
                "endereco": _txt(r.get("END_TRANSPORTADOR")),
                "municipio": _txt(r.get("CIDADE_TRANSPORTADOR")),
                "uf": _txt(r.get("ESTADO_TRANSPORTADOR")),
                "placa": _txt(r.get("PLACA")),
                "placa_uf": _txt(r.get("PLACA_UF")),
                "volumes": _int_str(r.get("NUM_VOLUME")),
                "peso_bruto": _num(r.get("PESO_BRUTO")) or 0.0,
                "peso_liquido": _num(r.get("PESO_LIQUIDO")) or 0.0,
            },
            "informacoes_complementares": _txt(r.get("INFORMACAOADICIONALFISCO")),
        })

    return {"ok": True, "notas": notas}


if __name__ == "__main__":
    if len(sys.argv) < 2 or not sys.argv[1].strip():
        print(json.dumps({"ok": False, "motivo": "Informe os NUM_PEDIDO separados por vírgula."}))
        sys.exit(0)
    try:
        resultado = buscar(sys.argv[1].split(","))
    except Exception as e:
        resultado = {"ok": False, "motivo": f"Erro consultando Oracle: {str(e)[:200]}"}
    print(json.dumps(resultado, ensure_ascii=False))
