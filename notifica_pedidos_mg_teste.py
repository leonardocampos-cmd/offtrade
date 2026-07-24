"""
Envia por WhatsApp um resumo de pedidos MG (pendentes, cortados e
cancelados) a partir do pedidos_data.js já gerado pelo pipeline.

FASE DE TESTE: envia sempre pro número de teste abaixo, não pros
vendedores reais. Quando validado, trocar NUMERO_TESTE por uma consulta
a PCUSUARI.TELEFONE por vendedor (igual planejado em notifica_nao_entregue.py).
"""
import json
import re
from pathlib import Path

from dotenv import load_dotenv
from whatsapp_evolution import enviar_whatsapp

load_dotenv()

NUMERO_TESTE = "5521992085320"


def carregar_pedidos():
    texto = (Path(__file__).parent / "pedidos_data.js").read_text(encoding="utf-8")
    return json.loads(re.search(r"const PEDIDOS_DATA\s*=\s*(\{.*\});", texto, re.DOTALL).group(1))


def _eh_mg(item):
    return (item.get("estado") or "").strip().upper() == "MG"


# "Liberado" (PCPEDC.POSICAO = 'L') é pedido aprovado seguindo o fluxo normal
# de faturamento — não é um problema. Só conta como "pendente" de verdade
# quem está parado numa posição que não avança sozinha.
_POSICOES_PROBLEMA = {"Pendente", "Bloqueado", "Bloqueado (alçada)"}


def _eh_pendente_real(item):
    return item.get("posicao") in _POSICOES_PROBLEMA


def _fmt_brl(v):
    try:
        v = float(v)
    except (TypeError, ValueError):
        return "R$ 0,00"
    return f"R$ {v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def montar_mensagem(dados):
    pendentes  = [p for p in dados["pedidos_feitos"] if _eh_mg(p) and _eh_pendente_real(p)]
    cortados   = [p for p in dados["faturados"] if _eh_mg(p) and p.get("tem_corte")]
    cancelados = [p for p in dados["cancelados"] if _eh_mg(p)]

    linhas = [
        "*PEDIDOS MG — pendentes, cortados e cancelados*",
        f"Atualizado em {dados.get('atualizado_em', '—')}\n",
        f"Pendentes: {len(pendentes)} ({_fmt_brl(sum(p['total'] for p in pendentes))})",
        f"Cortados: {len(cortados)} ({_fmt_brl(sum(p['total'] for p in cortados))})",
        f"Cancelados: {len(cancelados)} ({_fmt_brl(sum(p['total'] for p in cancelados))})",
    ]

    if pendentes:
        linhas.append("\n*Pendentes:*")
        for p in pendentes:
            linhas.append(f"• Ped {p['numped']} — {p['cliente']} — {_fmt_brl(p['total'])} ({p['nome']})")
            if p.get("motivo"):
                linhas.append(f"  Motivo: {p['motivo']}")

    if cortados:
        linhas.append("\n*Cortados:*")
        for p in cortados:
            linhas.append(f"• Ped {p['numped']} — {p['cliente']} — {_fmt_brl(p['total'])} ({p['nome']})")
            if p.get("motivo"):
                linhas.append(f"  Motivo: {p['motivo']}")

    linhas.append("\n_Mensagem de teste — envio automatizado de pedidos MG._")
    return "\n".join(linhas)


def main():
    dados = carregar_pedidos()
    mensagem = montar_mensagem(dados)
    resp = enviar_whatsapp(NUMERO_TESTE, mensagem)
    if resp.status_code in (200, 201):
        print(f"OK - enviado para {NUMERO_TESTE}")
    else:
        print(f"Erro ao enviar: {resp.status_code} - {resp.text[:300]}")


if __name__ == "__main__":
    main()
