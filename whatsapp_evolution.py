"""
Envio de texto via Evolution API, compartilhado pelos scripts do pipeline
que rodam tanto local (Windows) quanto na VPS.

"EVOLUTION_BASE_URL=http://localhost:8083" aponta para servidores
DIFERENTES dependendo de onde o script roda: local é uma Evolution API
v2.3.7 (schema flat, {"text": ...}); a VPS roda v1.8.7 (schema aninhado,
{"textMessage": {"text": ...}}) — confirmado em 2026-07-24 testando os
dois direto via curl, depois que o endpoint /api/login-erro (que só roda
na VPS) começou a retornar 400 "instance requires property textMessage"
usando o formato flat que os outros scripts já usavam local com sucesso.
"""
import os

import requests
from dotenv import load_dotenv

# Chama load_dotenv() aqui mesmo — scripts que importam este módulo antes de
# carregar o próprio .env (ordem de import comum no projeto) fariam os
# os.getenv() abaixo lerem valores vazios se dependessem só do load_dotenv()
# do chamador.
load_dotenv()


def enviar_whatsapp(numero, mensagem):
    url = f"{os.getenv('EVOLUTION_BASE_URL', 'http://localhost:8083')}/message/sendText/{os.getenv('EVOLUTION_INSTANCE', 'bees')}"
    headers = {"apikey": os.getenv("EVOLUTION_KEY", ""), "Content-Type": "application/json"}
    is_vps = os.getenv("OFFTRADE_RUNTIME", "local") == "vps"
    campo_texto = {"textMessage": {"text": mensagem}} if is_vps else {"text": mensagem}
    payload = {"number": numero, **campo_texto}
    return requests.post(url, json=payload, headers=headers, timeout=15)
