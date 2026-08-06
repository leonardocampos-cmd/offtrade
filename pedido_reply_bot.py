"""
Bot: recebe (via webhook da Evolution API) a resposta do vendedor a um
alerta de "pedido bloqueado" (ver alerta_pedidos_bloqueados.py), usa uma IA
(OpenAI) pra julgar se a justificativa dada permite liberar o pedido e, se
sim, manda uma mensagem de confirmação pro número de aprovação — NUNCA mexe
no Winthor diretamente, quem estiver nesse número libera manualmente
(pedido explícito do usuário em 2026-08-06: só avisa, humano libera).

Roda como serviço Flask atrás do nginx na VPS (mesmo padrão de login_api.py),
recebendo POST da Evolution API em /inbound sempre que uma mensagem chega na
instância. Configuração do webhook: ver configurar_webhook_evolution.py.
"""
import json
import os
import re
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
from flask import Flask, Blueprint, request

load_dotenv()

from whatsapp_evolution import enviar_whatsapp

BASE = Path(__file__).parent
AGUARDANDO_JSON = str(BASE / "pedidos_aguardando_resposta.json")
LOG_JSON        = str(BASE / "pedidos_bloqueados_decisoes.json")

NUMERO_APROVACAO = "5521992085320"

app = Flask(__name__)
bp = Blueprint("pedido_reply", __name__, url_prefix="/api/pedido-webhook")


def _normalizar_telefone(raw):
    digits = re.sub(r'\D', '', str(raw) if raw else '')
    if not digits:
        return None
    if not digits.startswith('55'):
        digits = '55' + digits
    return digits


def _extrair_remetente_e_texto(payload):
    """Evolution API (Baileys) — payload de messages.upsert. Formato varia
    um pouco entre versões; tenta os campos mais comuns e devolve None se
    não conseguir extrair (evento que não é mensagem de texto recebida)."""
    data = payload.get('data') or {}
    key = data.get('key') or {}
    if key.get('fromMe'):
        return None, None  # mensagem enviada por nós mesmos, ignora

    remote_jid = key.get('remoteJid', '') or ''
    telefone = _normalizar_telefone(remote_jid.split('@')[0])

    msg = data.get('message') or {}
    texto = (
        msg.get('conversation')
        or (msg.get('extendedTextMessage') or {}).get('text')
        or (msg.get('ephemeralMessage', {}).get('message', {}) or {}).get('conversation')
    )
    return telefone, texto


def _carregar_aguardando():
    if os.path.exists(AGUARDANDO_JSON) and os.path.getsize(AGUARDANDO_JSON) > 0:
        with open(AGUARDANDO_JSON, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def _salvar_aguardando(aguardando):
    with open(AGUARDANDO_JSON, "w", encoding="utf-8") as f:
        json.dump(aguardando, f, ensure_ascii=False, indent=2)


def _registrar_decisao(registro):
    log = []
    if os.path.exists(LOG_JSON) and os.path.getsize(LOG_JSON) > 0:
        with open(LOG_JSON, "r", encoding="utf-8") as f:
            log = json.load(f)
    log.append(registro)
    with open(LOG_JSON, "w", encoding="utf-8") as f:
        json.dump(log, f, ensure_ascii=False, indent=2)


def _julgar_com_ia(pedidos_pendentes, resposta_vendedor):
    """Manda o(s) motivo(s) de bloqueio + a resposta do vendedor pra IA
    julgar se a justificativa permite liberar. Julgamento livre (sem regra
    fixa) — pedido explícito do usuário em 2026-08-06. Retorna dict
    {'liberar': bool, 'justificativa': str}."""
    from openai import OpenAI
    client = OpenAI()

    pedidos_txt = "\n".join(
        f"- Pedido {p['numped']} ({p['cliente']}): {p['motivo']}"
        for p in pedidos_pendentes
    )

    system = (
        "Você avalia se a resposta de um vendedor a um bloqueio de pedido "
        "(desconto acima do permitido, cliente bloqueado, limite de crédito "
        "excedido etc.) é uma justificativa razoável pra liberar o pedido. "
        "Seja criterioso: só recomende liberar quando a resposta realmente "
        "explicar/justificar a situação (ex: autorização de um superior, "
        "erro de digitação explicado e corrigido, condição comercial "
        "conhecida). Respostas vagas, evasivas ou que não abordam o motivo "
        "não devem ser liberadas. Responda em JSON: "
        '{"liberar": true|false, "justificativa": "<resumo curto em '
        'português do motivo da decisão>"}'
    )
    user = (
        f"Pedido(s) bloqueado(s):\n{pedidos_txt}\n\n"
        f"Resposta do vendedor:\n{resposta_vendedor}"
    )

    resp = client.chat.completions.create(
        model=os.getenv("OPENAI_TEXT_MODEL", "gpt-4o-mini"),
        messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
        response_format={"type": "json_object"},
        temperature=0,
    )
    return json.loads(resp.choices[0].message.content)


def _montar_confirmacao(vendedor, pedidos_pendentes, resposta_vendedor, justificativa_ia):
    linhas = [
        "*PEDIDO LIBERADO PARA DESBLOQUEIO* ✅",
        f"Vendedor: *{vendedor}*\n",
    ]
    for p in pedidos_pendentes:
        linhas.append(f"• *Pedido {p['numped']}* — {p['cliente']}\n  Motivo original: {p['motivo']}")
    linhas.append(f"\nResposta do vendedor: \"{resposta_vendedor}\"")
    linhas.append(f"\nAvaliação da IA: {justificativa_ia}")
    linhas.append("\n_Confirmação automática — a liberação em si precisa ser feita manualmente no Winthor._")
    return "\n".join(linhas)


@bp.route("/inbound", methods=["POST"])
def inbound():
    payload = request.get_json(silent=True) or {}

    # Só processa evento de mensagem recebida
    evento = (payload.get('event') or '').lower()
    if 'messages' not in evento and 'message' not in evento:
        return {"ok": True, "ignorado": "evento não é mensagem"}, 200

    telefone, texto = _extrair_remetente_e_texto(payload)
    if not telefone or not texto:
        return {"ok": True, "ignorado": "sem remetente ou texto"}, 200

    print(f"[INBOUND] {telefone}: {texto!r}", flush=True)

    aguardando = _carregar_aguardando()
    pedidos_pendentes = aguardando.get(telefone)
    if not pedidos_pendentes:
        print(f"[IGNORADO] {telefone} sem pedido bloqueado pendente", flush=True)
        return {"ok": True, "ignorado": "telefone sem pedido bloqueado pendente"}, 200

    vendedor = pedidos_pendentes[0].get('vendedor', telefone)
    print(f"[PROCESSANDO] {vendedor} ({telefone}) — {len(pedidos_pendentes)} pedido(s) pendente(s)", flush=True)

    try:
        decisao = _julgar_com_ia(pedidos_pendentes, texto)
    except Exception as e:
        print(f"[ERRO IA] {vendedor}: {str(e)[:200]}", flush=True)
        _registrar_decisao({
            'telefone': telefone, 'vendedor': vendedor, 'resposta': texto,
            'erro': str(e)[:300], 'quando': datetime.now().isoformat(),
        })
        return {"ok": False, "erro": "falha ao consultar IA"}, 200

    print(f"[DECISAO] {vendedor}: liberar={decisao.get('liberar')} — {decisao.get('justificativa', '')[:150]}", flush=True)

    registro = {
        'telefone': telefone, 'vendedor': vendedor, 'resposta': texto,
        'pedidos': [p['numped'] for p in pedidos_pendentes],
        'liberar': decisao.get('liberar', False),
        'justificativa': decisao.get('justificativa', ''),
        'quando': datetime.now().isoformat(),
    }
    _registrar_decisao(registro)

    if decisao.get('liberar'):
        mensagem = _montar_confirmacao(vendedor, pedidos_pendentes, texto, decisao.get('justificativa', ''))
        print(f"[CONFIRMACAO ENVIADA] pro numero de aprovacao sobre {vendedor}", flush=True)
        enviar_whatsapp(NUMERO_APROVACAO, mensagem)
        # Resolvido — tira esse telefone da fila de espera.
        aguardando.pop(telefone, None)
        _salvar_aguardando(aguardando)

    return {"ok": True, "liberar": decisao.get('liberar', False)}, 200


app.register_blueprint(bp)

if __name__ == "__main__":
    # threaded=True — mesmo motivo do login_api.py: sem isso uma chamada
    # lenta (OpenAI/Oracle) trava a fila inteira de webhooks.
    app.run(host="0.0.0.0", port=5052, threaded=True)
