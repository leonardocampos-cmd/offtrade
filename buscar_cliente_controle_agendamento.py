"""buscar_cliente_controle_agendamento.py — busca cliente por CODCLI exato na
base CRC pra preencher automaticamente RCA/CLIENTE/CNPJ/FILIAL(bairro) na aba
"Planilha de Agendamento" de agendamento.html quando o usuário edita a coluna
COD. Página é CRC FILIAL 4 (RJ) — diferente de buscar_cliente_preco_promo.py
(que cobre CRC/SPON/CASTAS pra um simulador multi-base), aqui só a CRC
importa, então é lookup direto por CODCLI (não busca por nome/prefixo).
Chamado como subprocesso por pedidos_mercos_api.py (mesmo padrão de
buscar_cliente_preco_promo.py — essa API roda num venv leve sem Oracle).

Colunas confirmadas batendo com o que já vem na planilha da Geovanna
(2026-09-18): CODCLI=COD, PCCLIENT.CLIENTE=CLIENTE, PCCLIENT.CGCENT=CNPJ,
PCCLIENT.BAIRROENT=FILIAL, PCUSUARI.NOME (via CODUSUR1)=RCA.

Uso: python buscar_cliente_controle_agendamento.py <codcli>
Saída: JSON {"ok": true, "cliente": {"cliente","cnpj","bairro","rca"}}
    ou {"ok": false, "motivo": "..."}
"""
import json
import sys

from meta import engine, carregar_dados


def buscar(codcli):
    codcli = str(codcli).strip()
    if not codcli.isdigit():
        return {"ok": False, "motivo": "Código de cliente inválido."}

    query = f"""
        SELECT C.CODCLI, C.CLIENTE, C.FANTASIA, C.CGCENT, C.BAIRROENT,
               COALESCE(U.NOME, '') AS NOME_RCA
        FROM CRC.PCCLIENT C
        LEFT JOIN CRC.PCUSUARI U ON C.CODUSUR1 = U.CODUSUR
        WHERE C.CODCLI = {int(codcli)}
    """
    df = carregar_dados(query, engine, "controle_agendamento_busca_cliente")
    if df.empty:
        return {"ok": False, "motivo": "Cliente não encontrado na CRC."}

    r = df.iloc[0]
    nome = str(r["CLIENTE"] or r["FANTASIA"] or "").strip()
    return {
        "ok": True,
        "cliente": {
            "cliente": nome,
            "cnpj": str(r["CGCENT"] or "").strip(),
            "bairro": str(r["BAIRROENT"] or "").strip(),
            "rca": str(r["NOME_RCA"] or "").strip(),
        },
    }


if __name__ == "__main__":
    if len(sys.argv) < 2 or not sys.argv[1].strip():
        print(json.dumps({"ok": False, "motivo": "Informe o código do cliente."}))
        sys.exit(0)
    try:
        resultado = buscar(sys.argv[1])
    except Exception as e:
        resultado = {"ok": False, "motivo": f"Erro consultando Oracle: {str(e)[:200]}"}
    print(json.dumps(resultado, ensure_ascii=False))
