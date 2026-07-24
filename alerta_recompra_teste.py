"""
Alerta de recompra — versão de TESTE.

Lê raiox_cliente_detalhe_data.js (campo atrasado_recompra, calculado por
exportacao_raiox_cliente_detalhe.py a partir do ciclo médio real de compra
de cada cliente) e monta uma mensagem resumo dos clientes mais atrasados.

IMPORTANTE: por instrução explícita, este script NÃO envia mensagem pra
cliente nem pra vendedor — só pros números de teste já usados em
report_diario_vendedor.py, pra validar o conteúdo antes de decidir se um dia
isso vira um envio de verdade (e pra quem: cliente? vendedor? gestor?).
Não está agendado em cron — rodar manualmente quando quiser gerar um novo teste.
"""
import json
import os
from pathlib import Path

import requests
from dotenv import load_dotenv

BASE = Path(__file__).parent
load_dotenv(BASE / ".env")

# Mesmos números de teste usados em report_diario_vendedor.py — nunca um
# cliente ou vendedor real.
TEST_NUMBERS = ["5521970922712", "5521992085320", "5521966632125"]

EVOLUTION_URL = os.getenv("EVOLUTION_BASE_URL", "http://localhost:8083")
EVOLUTION_KEY = os.getenv("EVOLUTION_KEY", "")
INSTANCE      = os.getenv("EVOLUTION_INSTANCE", "bees")

TOP_N = 15


def _carregar_clientes():
    path = BASE / "raiox_cliente_detalhe_data.js"
    if not path.exists():
        return []
    raw = path.read_text(encoding="utf-8")
    data = json.loads(raw.split("=", 1)[1].rstrip("; \n"))
    return data.get("clientes", [])


def montar_mensagem(clientes: list) -> str:
    atrasados = [c for c in clientes if c.get("atrasado_recompra")]
    atrasados.sort(key=lambda c: c["dias_desde_ultima_compra"], reverse=True)
    top = atrasados[:TOP_N]

    linhas = [
        "🧪 *TESTE — Alerta de Recompra (não é envio real)*",
        f"Clientes atrasados na base toda: {len(atrasados)}\n",
        f"Top {len(top)} mais atrasados (dias sem comprar vs. ciclo médio):\n",
    ]
    for c in top:
        vendedor = ", ".join(v["nome"] for v in c.get("vendedores_cadastro", [])) or "Sem vendedor"
        linhas.append(
            f"• *{c['nome']}* ({c['estado']}, {c['ramo']})\n"
            f"  Vendedor: {vendedor}\n"
            f"  {c['dias_desde_ultima_compra']}d sem comprar · ciclo médio {c['ciclo_medio_dias']}d\n"
            f"  Média mensal: R$ {c['media_mensal']:.2f}\n"
        )
    linhas.append("_Mensagem de teste — nenhum cliente ou vendedor real foi notificado._")
    return "\n".join(linhas)


def enviar(mensagem: str, numero: str) -> bool:
    url = f"{EVOLUTION_URL}/message/sendText/{INSTANCE}"
    headers = {"apikey": EVOLUTION_KEY, "Content-Type": "application/json"}
    payload = {"number": numero, "textMessage": {"text": mensagem}}
    resp = requests.post(url, json=payload, headers=headers, timeout=30)
    ok = resp.status_code in (200, 201)
    print(f"  {'OK' if ok else 'ERRO'} envio pra {numero}: {resp.status_code}")
    if not ok:
        print(f"    {resp.text[:200]}")
    return ok


if __name__ == "__main__":
    clientes = _carregar_clientes()
    if not clientes:
        print("Sem raiox_cliente_detalhe_data.js (rode exportacao_raiox_cliente_detalhe.py primeiro). Nada enviado.")
    else:
        mensagem = montar_mensagem(clientes)
        print(mensagem)
        print(f"\n-> Enviando mensagem de TESTE para {len(TEST_NUMBERS)} número(s) de teste...")
        for numero in TEST_NUMBERS:
            enviar(mensagem, numero)
        print("OK — nenhum cliente ou vendedor real foi contatado.")
