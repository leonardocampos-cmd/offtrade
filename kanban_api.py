"""
API de tarefas do Kanban Mercos (Flask) — CRUD compartilhado entre gestores
e o vendedor W.S, separado dos outros serviços (login_api.py,
controle_vencimento.py) por ter storage e escopo próprios.

Não usa Oracle/banco nenhum — persiste em um JSON simples em disco
(kanban_data.json, escrita atômica: .tmp + os.replace, mesmo padrão dos
exportacao_*.py do pipeline).

Autenticação: reaproveita o cookie offtrade_token (JWT sem assinatura,
"alg: none") que auth.js/pedidos_mercos.html/estoque_mercos.html já setam
depois do login — aqui só decodifica o e-mail do payload e confere contra
a allowlist (gestores de utils.py::EMAILS_ADMIN + o e-mail do RCA 588/W.S).
Igual ao aviso em auth.js, esse token não é criptograficamente seguro
(qualquer um podia forjar um e-mail da allowlist) — aceitável pro nível de
exposição desta ferramenta interna, mesmo padrão já usado pro resto do site.

Uso local: python kanban_api.py  (abre em http://localhost:5054)
Na VPS roda atrás do nginx em /api/kanban/ (ver deploy_kanban_api_vps.py).
"""
import base64
import json
import os
import uuid
from datetime import datetime
from pathlib import Path
from urllib.parse import unquote

from flask import Flask, Blueprint, request, jsonify

DATA_PATH = Path(os.getenv("KANBAN_DATA_PATH", str(Path(__file__).parent / "kanban_data.json")))

# Mesma lista de utils.py::EMAILS_ADMIN — copiada aqui pra não depender do
# utils.py inteiro (que puxa Streamlit/Oracle, desnecessário pra esse
# serviço). Se a lista de gestores mudar, atualizar os dois lugares.
EMAILS_ADMIN = {
    "danielle.soares@rigarr.com.br",
    "allan.correa@rigarr.com.br",
    "leonardo.campos@rigarr.com.br",
    "alexsandro.nunes@rigarr.com.br",
    "giovani.cabral@rigarr.com.br",
    "kaliel.caro@rigarr.com.br",
    "artur.furlan@rigarr.com.br",
    "daniel.diniz@rigarr.com.br",
    "marcus.tanamachi@rigarr.com.br",
    "geovanna.lescano@rigarr.com.br",
    "fernando.risson@rigarr.com.br",
    "erocles.oliveira@rigarr.com.br",
    "andre.massensini@rigarr.com.br",
    "priscilla.zambrano@rigarr.com.br",
    "anderson.canaveis@rigarr.com.br",
    "willianseto@unityatacado.com.br",  # RCA 588 / "W.S" no SPON
}

COLUNAS_VALIDAS = {"a_fazer", "fazendo", "concluido"}

app = Flask(__name__)
bp = Blueprint("kanban", __name__, url_prefix="/api/kanban")

_lock_path = DATA_PATH.with_suffix(".lock")


def _email_autorizado() -> str | None:
    raw = request.cookies.get("offtrade_token")
    if not raw:
        return None
    try:
        token_obj = json.loads(unquote(raw))
        id_token = token_obj.get("id_token", "")
        payload_b64 = id_token.split(".")[1]
        padded = payload_b64 + "=" * (-len(payload_b64) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded))
        email = (payload.get("email") or "").strip().lower()
    except Exception:
        return None
    return email if email in EMAILS_ADMIN else None


def _carregar() -> list[dict]:
    if not DATA_PATH.exists():
        return []
    try:
        return json.loads(DATA_PATH.read_text(encoding="utf-8"))
    except Exception:
        return []


def _salvar(cards: list[dict]) -> None:
    tmp = DATA_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(cards, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, DATA_PATH)


@bp.before_request
def _checar_auth():
    if not _email_autorizado():
        return jsonify({"ok": False, "motivo": "Não autorizado."}), 401


@bp.route("/cards", methods=["GET"])
def listar_cards():
    return jsonify({"ok": True, "cards": _carregar()})


@bp.route("/cards", methods=["POST"])
def criar_card():
    dados = request.get_json(silent=True) or {}
    titulo = str(dados.get("titulo", "")).strip()[:200]
    if not titulo:
        return jsonify({"ok": False, "motivo": "Título é obrigatório."}), 400
    coluna = str(dados.get("coluna", "a_fazer")).strip()
    if coluna not in COLUNAS_VALIDAS:
        coluna = "a_fazer"
    agora = datetime.now().isoformat(timespec="seconds")
    card = {
        "id": uuid.uuid4().hex,
        "titulo": titulo,
        "descricao": str(dados.get("descricao", "")).strip()[:2000],
        "vendedor": str(dados.get("vendedor", "")).strip()[:100],
        "coluna": coluna,
        "criado_por": _email_autorizado(),
        "criado_em": agora,
        "atualizado_em": agora,
    }
    cards = _carregar()
    cards.append(card)
    _salvar(cards)
    return jsonify({"ok": True, "card": card}), 201


@bp.route("/cards/<card_id>", methods=["PUT"])
def atualizar_card(card_id):
    dados = request.get_json(silent=True) or {}
    cards = _carregar()
    card = next((c for c in cards if c["id"] == card_id), None)
    if not card:
        return jsonify({"ok": False, "motivo": "Card não encontrado."}), 404

    if "titulo" in dados:
        titulo = str(dados["titulo"]).strip()[:200]
        if not titulo:
            return jsonify({"ok": False, "motivo": "Título é obrigatório."}), 400
        card["titulo"] = titulo
    if "descricao" in dados:
        card["descricao"] = str(dados["descricao"]).strip()[:2000]
    if "vendedor" in dados:
        card["vendedor"] = str(dados["vendedor"]).strip()[:100]
    if "coluna" in dados:
        coluna = str(dados["coluna"]).strip()
        if coluna in COLUNAS_VALIDAS:
            card["coluna"] = coluna
    card["atualizado_em"] = datetime.now().isoformat(timespec="seconds")

    _salvar(cards)
    return jsonify({"ok": True, "card": card})


@bp.route("/cards/<card_id>", methods=["DELETE"])
def excluir_card(card_id):
    cards = _carregar()
    novos = [c for c in cards if c["id"] != card_id]
    if len(novos) == len(cards):
        return jsonify({"ok": False, "motivo": "Card não encontrado."}), 404
    _salvar(novos)
    return jsonify({"ok": True})


app.register_blueprint(bp)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5054, threaded=True)
