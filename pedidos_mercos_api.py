"""
Pedidos Mercos — backend (Flask), só o endpoint de "Enviar WhatsApp" que
pedidos_mercos.html consome. A página em si é HTML estático, publicada pelo
site (deploy_static_vps.py) — isso aqui só existe porque o telefone do
vendedor não pode ser exposto no pedidos_mercos_data.js público, e porque
o envio via Z-API precisa de um relay server-side (evita depender de CORS
liberado no lado da Z-API pra chamada direta do navegador).

Pedido do usuário em 2026-09-01: "Enviar WhatsApp" mandava um resumo do
pedido pelo wa.me do PRÓPRIO celular de quem clicou (sem número fixo, quem
clicava escolhia o contato) — o pedido agora é sair pela API, do WhatsApp
de QUEM ESTÁ LOGADO na página pro número do VENDEDOR do pedido. Cada
usuário logado tem sua PRÓPRIA conta Z-API (instance/token/client-token) —
o usuário foi explícito: esse token fica só no navegador dele (localStorage,
nunca salvo aqui) e é mandado em cada requisição pra esse backend só
retransmitir pra Z-API, nunca persistido.

O vendedor desses pedidos (ex: "008 - Marcos") não tem cadastro no Winthor
— são vendedores da própria SPON, não do time OFF TRADE — então o telefone
vem do cadastro de colaborador da própria Mercos (ver
mercos_api.py::buscar_colaboradores_telefones), cacheado em memória por
_CACHE_TTL_SEG pra não logar na Mercos a cada clique.

Formato da Z-API (https://api.z-api.io/instances/{instance}/token/{token}/
send-text, header Client-Token) é o padrão documentado publicamente pela
Z-API — nunca testado contra uma conta real neste projeto (ninguém tinha
credencial pra testar até agora); se o formato mudou, o erro retornado pela
própria Z-API aparece direto na página pra ajustar.

Uso local: python pedidos_mercos_api.py  (abre em http://localhost:5056)
Na VPS roda atrás do nginx em /api/pedidos-mercos/ (ver
deploy_pedidos_mercos_api_vps.py). Porta 5056: 5050-5055 já ocupadas por
outros serviços da VPS (vencimento, login-api, pedido_reply_bot,
whatsapp-resumo, kanban-api, credito-cadastro — ver credito_cadastro_api.py).
"""
import os
import time

import requests
from dotenv import load_dotenv
from flask import Flask, Blueprint, request

import mercos_api

load_dotenv()

RUNTIME = os.getenv("OFFTRADE_RUNTIME", "local")

app = Flask(__name__)
bp = Blueprint("pedidos_mercos", __name__, url_prefix="/api/pedidos-mercos")

_CACHE_TTL_SEG = 600
_cache_telefones: dict = {}
_cache_ts = 0.0


def _telefones_colaboradores() -> dict:
    global _cache_telefones, _cache_ts
    if _cache_telefones and (time.time() - _cache_ts) < _CACHE_TTL_SEG:
        return _cache_telefones
    sessao = mercos_api.login()
    _cache_telefones = mercos_api.buscar_colaboradores_telefones(sessao)
    _cache_ts = time.time()
    return _cache_telefones


def _enviar_via_zapi(instance, token, client_token, numero, mensagem):
    url = f"https://api.z-api.io/instances/{instance}/token/{token}/send-text"
    headers = {"Client-Token": client_token, "Content-Type": "application/json"}
    payload = {"phone": numero, "message": mensagem}
    return requests.post(url, json=payload, headers=headers, timeout=20)


@bp.route("/enviar-whatsapp", methods=["POST"])
def enviar_whatsapp_pedido():
    dados = request.get_json(silent=True) or {}
    cod_vendedor  = str(dados.get("cod_vendedor", "")).strip()
    mensagem      = str(dados.get("mensagem", "")).strip()
    zapi_instance = str(dados.get("zapi_instance", "")).strip()
    zapi_token    = str(dados.get("zapi_token", "")).strip()
    zapi_client_token = str(dados.get("zapi_client_token", "")).strip()

    if not cod_vendedor or not mensagem:
        return {"ok": False, "motivo": "Dados incompletos."}, 400
    if not zapi_instance or not zapi_token or not zapi_client_token:
        return {"ok": False, "motivo": "Configure sua conta Z-API (Instance ID, Token e Client-Token) antes de enviar."}, 400

    try:
        telefones = _telefones_colaboradores()
    except Exception as e:
        return {"ok": False, "motivo": f"Não consegui consultar o telefone na Mercos agora ({str(e)[:150]})."}

    numero = telefones.get(cod_vendedor)
    if not numero:
        return {"ok": False, "motivo": f"Vendedor \"{cod_vendedor}\" não tem telefone cadastrado na Mercos."}

    try:
        resp = _enviar_via_zapi(zapi_instance, zapi_token, zapi_client_token, numero, mensagem)
    except Exception as e:
        return {"ok": False, "motivo": f"Erro ao enviar pela Z-API ({str(e)[:150]})."}

    if resp.status_code >= 300:
        detalhe = resp.text[:200] if resp.text else ""
        return {"ok": False, "motivo": f"Z-API recusou o envio (HTTP {resp.status_code}): {detalhe}"}
    return {"ok": True}


app.register_blueprint(bp)

if __name__ == "__main__":
    debug = RUNTIME != "vps"
    host = "127.0.0.1" if RUNTIME == "vps" else "0.0.0.0"
    app.run(host=host, port=5056, debug=debug, threaded=True)
