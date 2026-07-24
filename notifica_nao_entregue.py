"""
Notifica vendedor por WhatsApp sobre as NFs "Não entregue" do dia
(entregas_data.js, já mantido por entregas.py + alerta_logistica_rj.py).

FASE DE TESTE: em vez de usar o TELEFONE real de cada vendedor, os
vendedores sorteados aleatoriamente sao enviados para os mesmos 3 numeros de
teste do report_diario_vendedor.py. Quando validado, trocar TEST_NUMBERS por
uma consulta a PCUSUARI.TELEFONE.
"""
import os
import re
import json
import random
import time
import requests
from pathlib import Path
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

TEST_NUMBERS = ["5521970922712", "5521992085320", "5521966632125"]

EVOLUTION_URL = os.getenv("EVOLUTION_BASE_URL", "http://localhost:8083")
EVOLUTION_KEY = os.getenv("EVOLUTION_KEY", "")
INSTANCE      = os.getenv("EVOLUTION_INSTANCE", "bees")


def carregar_entregas():
    texto = (Path(__file__).parent / "entregas_data.js").read_text(encoding="utf-8")
    return json.loads(re.search(r"const ENTREGAS_DATA\s*=\s*(\{.*\});", texto, re.DOTALL).group(1))


def _fmt_brl(v):
    try:
        v = float(v)
    except (TypeError, ValueError):
        return "R$ 0,00"
    return f"R$ {v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def _nf_limpa(numnota):
    try:
        return str(int(float(numnota)))
    except (TypeError, ValueError):
        return str(numnota or "?")


def montar_mensagem(vendedor):
    hoje_str = datetime.today().strftime("%d/%m/%Y")
    linhas = [
        f"*NÃO ENTREGUE — {hoje_str}*",
        f"Vendedor: *{vendedor['nome']}*\n",
    ]
    for ped in vendedor["nao_entregue"]:
        nf      = _nf_limpa(ped.get("numnota"))
        cliente = ped.get("cliente") or "(cliente não identificado)"
        motivo  = ped.get("motivo_alerta") or ped.get("motivo") or "(motivo não informado)"
        valor   = ped.get("total", 0)
        linhas.append(
            f"• *NF {nf}* — {cliente}\n"
            f"  Motivo: {motivo}\n"
            f"  Valor: {_fmt_brl(valor)}"
        )
    linhas.append("\n_Mensagem de teste — envio automatizado de não entrega._")
    return "\n".join(linhas)


def enviar_whatsapp(numero, mensagem):
    url = f"{EVOLUTION_URL}/message/sendText/{INSTANCE}"
    headers = {"apikey": EVOLUTION_KEY, "Content-Type": "application/json"}
    payload = {"number": numero, "textMessage": {"text": mensagem}}
    return requests.post(url, json=payload, headers=headers, timeout=15)


def main():
    dados = carregar_entregas()
    vendedores_com_alerta = [v for v in dados["vendedores"] if v.get("nao_entregue")]

    if not vendedores_com_alerta:
        print("Nenhum vendedor com NF não entregue hoje. Nada enviado.")
        return

    n = min(3, len(vendedores_com_alerta))
    sorteados = random.sample(vendedores_com_alerta, n)
    numeros = random.sample(TEST_NUMBERS, n)

    for numero, vendedor in zip(numeros, sorteados):
        mensagem = montar_mensagem(vendedor)
        resp = enviar_whatsapp(numero, mensagem)
        if resp.status_code in (200, 201):
            print(f"OK - enviado para {numero} (dados de {vendedor['nome']}, {len(vendedor['nao_entregue'])} NF(s))")
        else:
            print(f"Erro ao enviar para {numero}: {resp.status_code} - {resp.text[:200]}")
        time.sleep(1)


if __name__ == "__main__":
    main()
