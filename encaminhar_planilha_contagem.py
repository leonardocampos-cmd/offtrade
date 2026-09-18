"""
Encaminha pro número do BEES (21992085320) qualquer planilha (.xlsx) que
alguém suba no grupo "Estoque RJ - Rigarr" em resposta ao pedido de
contagem (ver alerta_contagem_planilhas.py) — pedido do usuário em
2026-09-18.

Mesmo padrão de leitura de grupo de exportacao_estoque_whatsapp.py (mesma
instância dedicada "estoque", mesmos GROUP_JIDS, mesmo endpoint
chat/findMessages) — só que aqui filtra mensagem de DOCUMENTO em vez de
texto. Dedup por id da mensagem em encaminhar_planilha_contagem.json pra
não reencaminhar a cada rodada do cron.

Ignora mensagens fromMe=True (documentos que o próprio bot manda, ex: a
planilha em branco anexada pelo alerta_contagem_planilhas.py) — só
encaminha o que o TIME sobe de volta.

Roda com cron próprio na VPS (instância "estoque" só existe lá, mesma
razão de exportacao_estoque_whatsapp.py e alerta_contagem_planilhas.py).
"""
import base64
import json
import os
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv()

from whatsapp_evolution import enviar_whatsapp_documento

EVOLUTION_BASE_URL = os.getenv("EVOLUTION_BASE_URL", "http://localhost:8083")
EVOLUTION_KEY = os.getenv("EVOLUTION_KEY", "")
INSTANCIA = "estoque"
GROUP_JIDS = {"120363021573739336@g.us", "135777321263246@lid"}
MENSAGENS_POR_BUSCA = 300

NUMERO_DESTINO = "5521992085320"  # número do BEES

HERE = Path(__file__).parent
ENCAMINHADOS_JSON = HERE / "encaminhar_planilha_contagem.json"
RECEBIDOS_DIR = HERE / "planilhas_contagem_recebidas"


def _headers():
    return {"apikey": EVOLUTION_KEY, "Content-Type": "application/json"}


def _carregar_encaminhados():
    if ENCAMINHADOS_JSON.exists():
        try:
            return set(json.loads(ENCAMINHADOS_JSON.read_text(encoding="utf-8")))
        except Exception:
            pass
    return set()


def _salvar_encaminhados(ids):
    # Mantém só os últimos 500 — a lista só existe pra dedupe de curto prazo
    # (mesma janela de MENSAGENS_POR_BUSCA), não precisa crescer sem limite.
    ENCAMINHADOS_JSON.write_text(json.dumps(sorted(ids)[-500:], ensure_ascii=False), encoding="utf-8")


def _extrair_documento(msg):
    """Devolve (fileName, mimetype) se a mensagem for um documento, senão None.
    documentWithCaptionMessage aninha o documentMessage real um nível mais
    fundo (confirmado no formato do Baileys quando o documento é enviado com
    legenda)."""
    doc = msg.get("documentMessage")
    if not doc:
        doc = ((msg.get("documentWithCaptionMessage") or {}).get("message") or {}).get("documentMessage")
    if not doc:
        return None
    return doc.get("fileName") or doc.get("title") or "planilha.xlsx", doc.get("mimetype") or ""


def _buscar_documentos_grupo():
    url = f"{EVOLUTION_BASE_URL}/chat/findMessages/{INSTANCIA}"
    resp = requests.post(url, json={"where": {}, "limit": MENSAGENS_POR_BUSCA}, headers=_headers(), timeout=30)
    resp.raise_for_status()
    todas = resp.json()

    documentos = []
    for m in todas:
        key = m.get("key") or {}
        if key.get("remoteJid") not in GROUP_JIDS or key.get("fromMe"):
            continue
        info = _extrair_documento(m.get("message") or {})
        if not info:
            continue
        file_name, mimetype = info
        if not file_name.lower().endswith((".xlsx", ".xls", ".xlsm")):
            continue
        documentos.append({
            "id": key.get("id") or "",
            "key": key,
            "fileName": file_name,
            "mimetype": mimetype,
            "ts": m.get("messageTimestamp") or 0,
        })
    documentos.sort(key=lambda x: x["ts"])
    return documentos


def _baixar_media_base64(key):
    url = f"{EVOLUTION_BASE_URL}/chat/getBase64FromMediaMessage/{INSTANCIA}"
    payload = {"message": {"key": key}, "convertToMp4": False}
    resp = requests.post(url, json=payload, headers=_headers(), timeout=60)
    resp.raise_for_status()
    data = resp.json()
    return data.get("base64")


def main():
    encaminhados = _carregar_encaminhados()
    documentos = _buscar_documentos_grupo()
    novos = [d for d in documentos if d["id"] and d["id"] not in encaminhados]

    if not novos:
        print("OK - nenhuma planilha nova pra encaminhar.")
        return

    RECEBIDOS_DIR.mkdir(exist_ok=True)

    for doc in novos:
        try:
            b64 = _baixar_media_base64(doc["key"])
            if not b64:
                print(f"[AVISO] '{doc['fileName']}' sem conteúdo (base64 vazio) — ignorado.")
                continue
            destino = RECEBIDOS_DIR / doc["fileName"]
            destino.write_bytes(base64.b64decode(b64))

            resp = enviar_whatsapp_documento(
                NUMERO_DESTINO, str(destino),
                legenda=f"Contagem recebida: {doc['fileName']}",
            )
            if resp.status_code < 300:
                print(f"OK - encaminhado: {doc['fileName']}")
            else:
                print(f"[AVISO] falha ao encaminhar '{doc['fileName']}': {resp.status_code} - {resp.text[:200]}")
        except Exception as e:
            print(f"[AVISO] erro processando '{doc['fileName']}' ({str(e)[:150]}) — pulando.")
            continue
        finally:
            encaminhados.add(doc["id"])

    _salvar_encaminhados(encaminhados)


if __name__ == "__main__":
    main()
