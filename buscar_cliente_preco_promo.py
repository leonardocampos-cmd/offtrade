"""buscar_cliente_preco_promo.py — busca cliente por código ou nome em
CRC/SPON/CASTAS pro campo "Cliente" da intenção de venda em
preco_promo.html (mesmo simulador Perini). Chamado como subprocesso por
pedidos_mercos_api.py (mesmo padrão de buscar_produto_preco_promo.py e
buscar_dados_nfe.py — a API roda num venv leve sem Oracle).

Mesma colisão de código entre bases que já existe pra produto (ver
buscar_produto_preco_promo.py) pode acontecer com CODCLI — por isso cada
resultado vem com "base" junto, gravado com o cliente pra nunca confundir
cliente de base diferente com o mesmo número.

Uso: python buscar_cliente_preco_promo.py <termo>
Saída: JSON {"ok": true, "clientes": [{"codcli","nome","cnpj","base"}, ...]}
"""
import json
import sys

from meta import engine, engine_spon, engine_castas, carregar_dados

BASES = [
    ("CRC", engine, "CRC.PCCLIENT"),
    ("SPON", engine_spon, "PCCLIENT"),
    ("CASTAS", engine_castas, "CASTAS.PCCLIENT"),
]


def buscar(termo):
    termo = termo.strip()
    if not termo:
        return {"ok": False, "motivo": "Termo vazio."}

    # meta.carregar_dados não aceita bind params — interpola direto na
    # string, mesmo padrão do resto do projeto. Escapa aspas simples pra
    # não quebrar a query nem abrir injeção.
    termo_sql = termo.replace("'", "''")

    clientes = []
    for nome_base, eng, tabela in BASES:
        if termo.isdigit():
            query = f"""
                SELECT CODCLI, CLIENTE, FANTASIA, CGCENT FROM {tabela}
                WHERE CODCLI = {int(termo)} OR TO_CHAR(CODCLI) LIKE '{termo_sql}%'
                FETCH FIRST 15 ROWS ONLY
            """
        else:
            query = f"""
                SELECT CODCLI, CLIENTE, FANTASIA, CGCENT FROM {tabela}
                WHERE UPPER(CLIENTE) LIKE UPPER('%{termo_sql}%')
                   OR UPPER(FANTASIA) LIKE UPPER('%{termo_sql}%')
                FETCH FIRST 15 ROWS ONLY
            """
        try:
            df = carregar_dados(query, eng, f"preco_promo_busca_cliente_{nome_base}")
        except Exception:
            continue
        for _, r in df.iterrows():
            nome = str(r["CLIENTE"] or r["FANTASIA"] or "").strip()
            clientes.append({
                "codcli": str(int(r["CODCLI"])),
                "nome": nome,
                "cnpj": str(r["CGCENT"] or "").strip(),
                "base": nome_base,
            })
    return {"ok": True, "clientes": clientes}


if __name__ == "__main__":
    if len(sys.argv) < 2 or not sys.argv[1].strip():
        print(json.dumps({"ok": False, "motivo": "Informe um termo de busca."}))
        sys.exit(0)
    try:
        resultado = buscar(sys.argv[1])
    except Exception as e:
        resultado = {"ok": False, "motivo": f"Erro consultando Oracle: {str(e)[:200]}"}
    print(json.dumps(resultado, ensure_ascii=False))
