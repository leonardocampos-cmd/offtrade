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
import base64
import mimetypes
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


def enviar_whatsapp_imagem(numero, caminho_imagem, legenda=""):
    """Envia imagem via sendMedia. Schema flat (local v2.3.7) confirmado nos
    docs oficiais; schema aninhado da VPS (v1.8.7, 'mediaMessage': {...}) é
    inferido por analogia ao mesmo padrão de enviar_whatsapp() acima
    ('textMessage': {...}) — nunca testado na VPS, testar antes de usar em
    produção lá."""
    url = f"{os.getenv('EVOLUTION_BASE_URL', 'http://localhost:8083')}/message/sendMedia/{os.getenv('EVOLUTION_INSTANCE', 'bees')}"
    headers = {"apikey": os.getenv("EVOLUTION_KEY", ""), "Content-Type": "application/json"}
    is_vps = os.getenv("OFFTRADE_RUNTIME", "local") == "vps"
    with open(caminho_imagem, "rb") as f:
        media_b64 = base64.b64encode(f.read()).decode()
    mimetype = mimetypes.guess_type(caminho_imagem)[0] or "image/jpeg"
    campo_media = {
        "mediatype": "image",
        "mimetype": mimetype,
        "media": media_b64,
        "fileName": os.path.basename(caminho_imagem),
        "caption": legenda,
    }
    payload = {"number": numero, "mediaMessage": campo_media} if is_vps else {"number": numero, **campo_media}
    return requests.post(url, json=payload, headers=headers, timeout=30)
