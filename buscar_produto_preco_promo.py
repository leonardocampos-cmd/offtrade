"""buscar_produto_preco_promo.py — busca produto por código/nome em várias
bases (CRC, SPON, GARRIDO) pro cadastro de Preço Promo
(preco_promo.html). Chamado como subprocesso por pedidos_mercos_api.py
(mesmo padrão de buscar_dados_nfe.py) — a API roda num venv leve sem Oracle.

Pedido do usuário em 2026-09-14, depois de descobrir uma colisão real de
CODPROD entre bases (5487 é "TERMINAL DE CONTATO" na CRC mas "CERESER MACA
ZERO S/ALCOOL 24X275ML" na SPON) — cada resultado vem com o campo "base",
que preco_promo.html grava junto com o codprod (chave composta) pra nunca
mais confundir produto de bases diferentes com o mesmo número.

thekings/CASTAS/BLENDED ficaram de fora por enquanto (erro genérico ORA- ao
consultar PCPRODUT sem prefixo — schema/nome de tabela diferente, não
investigado ainda); adicionar aqui se precisar buscar produto dessas bases.

Uso: python buscar_produto_preco_promo.py <termo>
Saída: JSON {"ok": true, "produtos": [{"codprod","descricao","base"}, ...]}
"""
import json
import sys

from meta import engine, engine_spon, engine_garrido, carregar_dados

# MGON/thekings/CASTAS/BLENDED ficaram de fora por enquanto — erro ao
# consultar PCPRODUT sem prefixo (schema/nome de tabela diferente, não
# investigado ainda). Adicionar aqui se precisar buscar produto dessas bases.
BASES = [
    ("CRC", engine, "CRC.PCPRODUT"),
    ("SPON", engine_spon, "PCPRODUT"),
    ("GARRIDO", engine_garrido, "PCPRODUT"),
]


def buscar(termo):
    termo = termo.strip()
    if not termo:
        return {"ok": False, "motivo": "Termo vazio."}

    # meta.carregar_dados não aceita bind params (só query+engine, ver
    # assinatura em meta.py) — interpola direto na string, mesmo padrão do
    # resto do projeto (ex: buscar_dados_nfe.py). Escapa aspas simples pra
    # não quebrar a query nem abrir injeção.
    termo_sql = termo.replace("'", "''")

    produtos = []
    for nome_base, eng, tabela in BASES:
        if termo.isdigit():
            query = f"SELECT CODPROD, DESCRICAO FROM {tabela} WHERE CODPROD = {int(termo)} OR TO_CHAR(CODPROD) LIKE '{termo_sql}%' FETCH FIRST 15 ROWS ONLY"
        else:
            query = f"SELECT CODPROD, DESCRICAO FROM {tabela} WHERE UPPER(DESCRICAO) LIKE UPPER('%{termo_sql}%') FETCH FIRST 15 ROWS ONLY"
        try:
            df = carregar_dados(query, eng, f"preco_promo_busca_{nome_base}")
        except Exception:
            continue
        for _, r in df.iterrows():
            produtos.append({
                "codprod": str(int(r["CODPROD"])),
                "descricao": str(r["DESCRICAO"]).strip(),
                "base": nome_base,
            })
    return {"ok": True, "produtos": produtos}


if __name__ == "__main__":
    if len(sys.argv) < 2 or not sys.argv[1].strip():
        print(json.dumps({"ok": False, "motivo": "Informe um termo de busca."}))
        sys.exit(0)
    try:
        resultado = buscar(sys.argv[1])
    except Exception as e:
        resultado = {"ok": False, "motivo": f"Erro consultando Oracle: {str(e)[:200]}"}
    print(json.dumps(resultado, ensure_ascii=False))
