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

Formato da Z-API (send-text e send-document/pdf, header Client-Token) é o
padrão documentado publicamente pela Z-API — nunca testado contra uma conta
real neste projeto (ninguém tinha credencial pra testar até agora); se o
formato mudou, o erro retornado pela própria Z-API aparece direto na
página pra ajustar. O PDF do pedido (mesmo layout de "Gerar PDF") é gerado
no navegador (jsPDF) e mandado em base64 nesse endpoint — pedido do
usuário em 2026-09-01: mensagem de faturamento + cortes, e o PDF junto.

Uso local: python pedidos_mercos_api.py  (abre em http://localhost:5056)
Na VPS roda atrás do nginx em /api/pedidos-mercos/ (ver
deploy_pedidos_mercos_api_vps.py). Porta 5056: 5050-5055 já ocupadas por
outros serviços da VPS (vencimento, login-api, pedido_reply_bot,
whatsapp-resumo, kanban-api, credito-cadastro — ver credito_cadastro_api.py).
"""
import json
import os
import re
import subprocess
import threading
import time
import unicodedata
from pathlib import Path

import requests
from dotenv import load_dotenv
from flask import Flask, Blueprint, request

import mercos_api

load_dotenv()

RUNTIME = os.getenv("OFFTRADE_RUNTIME", "local")

# gerar_estoque_mercos_spon_data.py precisa de Oracle (oracledb/sqlalchemy/
# pandas via meta.py) — deps pesadas de propósito fora do venv leve deste
# serviço (/opt/pedidos-mercos-api, só flask/requests/dotenv). Roda como
# subprocesso usando o Python + diretório do pipeline principal, que já tem
# tudo isso configurado (Oracle Instant Client incluso).
_PIPELINE_DIR = os.getenv(
    "PIPELINE_DIR",
    "/opt/offtrade-pipeline" if RUNTIME == "vps" else r"G:\Meu Drive\offtrade",
)
_PIPELINE_PYTHON = os.path.join(_PIPELINE_DIR, ".venv", "bin", "python") if RUNTIME == "vps" else "python"

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


def _enviar_texto_zapi(instance, token, client_token, numero, mensagem):
    url = f"https://api.z-api.io/instances/{instance}/token/{token}/send-text"
    headers = {"Client-Token": client_token, "Content-Type": "application/json"}
    payload = {"phone": numero, "message": mensagem}
    return requests.post(url, json=payload, headers=headers, timeout=20)


# send-document/pdf: "document" precisa do data URI COMPLETO, com o
# prefixo "data:application/pdf;base64,..." — confirmado 2026-09-02 contra
# conta real (mandar só o base64 cru, sem prefixo, dava "Base64/Url could
# not be read" na Z-API). O front-end (pedidos_mercos.html::enviarWhatsapp)
# já manda com o prefixo; esse parâmetro só repassa pra frente.
def _enviar_documento_zapi(instance, token, client_token, numero, base64_pdf, nome_arquivo):
    url = f"https://api.z-api.io/instances/{instance}/token/{token}/send-document/pdf"
    headers = {"Client-Token": client_token, "Content-Type": "application/json"}
    payload = {"phone": numero, "document": base64_pdf, "fileName": nome_arquivo}
    return requests.post(url, json=payload, headers=headers, timeout=30)


@bp.route("/enviar-whatsapp", methods=["POST"])
def enviar_whatsapp_pedido():
    dados = request.get_json(silent=True) or {}
    cod_vendedor  = str(dados.get("cod_vendedor", "")).strip()
    mensagem      = str(dados.get("mensagem", "")).strip()
    zapi_instance = str(dados.get("zapi_instance", "")).strip()
    zapi_token    = str(dados.get("zapi_token", "")).strip()
    zapi_client_token = str(dados.get("zapi_client_token", "")).strip()
    # PDF opcional (data URI completo, COM o prefixo "data:application/pdf;base64,"
    # — ver comentário de _enviar_documento_zapi) — pedido do usuário em
    # 2026-09-01: manda a mensagem E o PDF do pedido.
    pdf_base64   = str(dados.get("pdf_base64", "")).strip()
    pdf_filename = str(dados.get("pdf_filename", "")).strip() or "pedido.pdf"

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
        resp = _enviar_texto_zapi(zapi_instance, zapi_token, zapi_client_token, numero, mensagem)
    except Exception as e:
        return {"ok": False, "motivo": f"Erro ao enviar mensagem pela Z-API ({str(e)[:150]})."}

    if resp.status_code >= 300:
        detalhe = resp.text[:200] if resp.text else ""
        return {"ok": False, "motivo": f"Z-API recusou o envio da mensagem (HTTP {resp.status_code}): {detalhe}"}

    if pdf_base64:
        try:
            resp_pdf = _enviar_documento_zapi(zapi_instance, zapi_token, zapi_client_token, numero, pdf_base64, pdf_filename)
        except Exception as e:
            return {"ok": False, "motivo": f"Mensagem enviada, mas o PDF falhou ({str(e)[:150]})."}
        if resp_pdf.status_code >= 300:
            detalhe = resp_pdf.text[:200] if resp_pdf.text else ""
            return {"ok": False, "motivo": f"Mensagem enviada, mas a Z-API recusou o PDF (HTTP {resp_pdf.status_code}): {detalhe}"}


# ── Config Z-API pro aviso automático rodar no servidor (checar_status_pedidos_mercos.py) ──
# Pedido do usuário em 2026-09-11: o aviso automático de mudança de status
# era 100% client-side (setInterval na página) e só funcionava com a aba
# aberta em primeiro plano — em celular o navegador suspende o timer assim
# que a aba vai pra segundo plano, então nunca disparava de verdade. Migrado
# pro cron da VPS (checar_status_pedidos_mercos.py), que precisa da mesma
# conta Z-API pra mandar sozinho. Muda a decisão de 2026-09-01 de nunca
# persistir o token aqui — mas o token continua nunca passando por chat/
# humano: a vendedora salva no PRÓPRIO navegador dela (mesmo modal de
# sempre), e pedidos_mercos.html::salvarConfigZapi() sincroniza esse mesmo
# valor pra cá automaticamente, sem passo manual extra. Gravado em arquivo
# local (não versionado/clonado por git, mesmo motivo de
# /opt/pedidos-mercos-api não ser clone git) — mesmo nível de confiança do
# enviar-whatsapp acima (ferramenta interna, sem camada de auth extra).
ZAPI_CONFIG_PATH = Path(__file__).parent / "zapi_config_servidor.json"


@bp.route("/config-zapi", methods=["POST"])
def config_zapi():
    dados = request.get_json(silent=True) or {}
    config = {
        "instance": str(dados.get("instance", "")).strip(),
        "token": str(dados.get("token", "")).strip(),
        "client_token": str(dados.get("client_token", "")).strip(),
        "auto_avisar": bool(dados.get("auto_avisar")),
    }
    try:
        tmp = ZAPI_CONFIG_PATH.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(config, ensure_ascii=False), encoding="utf-8")
        tmp.replace(ZAPI_CONFIG_PATH)
    except Exception as e:
        return {"ok": False, "motivo": f"Não consegui salvar a config ({str(e)[:150]})."}, 500
    return {"ok": True}


# ── Sincronizar estoque agora (botão em estoque_mercos.html) ───────────────
# Mesmo push que o cron de 30min já faz (gerar_estoque_mercos_spon_data.py),
# só que sob demanda — pedido do usuário em 2026-09-04 pra não precisar
# esperar o cron quando o saldo mudou e alguém precisa que reflita na Mercos
# na hora. Lock evita duas sincronizações ao mesmo tempo: a Mercos só permite
# UMA sessão logada por usuário (confirmado 2026-09-04 — um segundo login
# derruba o primeiro), então rodar em paralelo com o cron ou outro clique
# corrompe o resultado de ambos.
_sync_lock = threading.Lock()


_RE_RESUMO = re.compile(
    r"estoque empurrado pra Mercos: (\d+) produto\(s\), (\d+) falha\(s\) \(de (\d+) mapeados\)"
)


@bp.route("/sincronizar-estoque", methods=["POST"])
def sincronizar_estoque():
    if not _sync_lock.acquire(blocking=False):
        return {"ok": False, "motivo": "Já tem uma sincronização em andamento (cron ou outro clique) — aguarde terminar e tente de novo."}, 409
    # ORACLE_LIB fica errado se herdado: este processo já rodou load_dotenv()
    # a partir do PRÓPRIO .env (/opt/pedidos-mercos-api/.env, cópia do .env
    # local que nunca precisou de Oracle — build_remote_env() aqui não
    # reescreve esse campo, só OFFTRADE_RUNTIME). subprocess.run() herda
    # os.environ por padrão, e load_dotenv() do lado do gerar_estoque_*
    # (via meta.py) NÃO sobrescreve uma env var já setada — então o
    # caminho do Instant Client do Windows vazava pro processo Linux
    # (achado real em 2026-09-04: "C:\instantclient/libclntsh.so"). Remove
    # daqui pra o subprocesso cair no default certo de utils.py::ORACLE_LIB
    # ou no valor do próprio .env do pipeline.
    _env = {k: v for k, v in os.environ.items() if k != "ORACLE_LIB"}
    try:
        resp = subprocess.run(
            [_PIPELINE_PYTHON, "gerar_estoque_mercos_spon_data.py"],
            cwd=_PIPELINE_DIR, capture_output=True, text=True, timeout=110, env=_env,
        )
    except subprocess.TimeoutExpired:
        return {"ok": False, "motivo": "Sincronização demorou demais (>110s) e foi interrompida."}, 504
    except Exception as e:
        return {"ok": False, "motivo": f"Falha ao sincronizar: {str(e)[:200]}"}, 500
    finally:
        _sync_lock.release()

    saida = (resp.stdout or "") + (resp.stderr or "")
    m = _RE_RESUMO.search(saida)
    if resp.returncode != 0 and not m:
        return {"ok": False, "motivo": f"Script falhou (código {resp.returncode}): {saida[-300:]}"}, 500
    if not m:
        return {"ok": False, "motivo": f"Não consegui confirmar o resultado — saída inesperada: {saida[-300:]}"}
    ok, falhas, total = (int(x) for x in m.groups())
    return {"ok": True, "atualizados": ok, "falhas": falhas, "total": total}


# ── DANFE (estoque_mercos.html não, pedidos_mercos.html::baixarDanfe) ──────
# Pedido do usuário em 2026-09-11: baixar o DANFE oficial (não só o resumo
# comercial que já existia) de um pedido faturado. buscar_dados_nfe.py imprime
# um JSON puro no stdout como ÚLTIMA linha — mas importar meta.py (que importa
# utils.py só pra pegar ORACLE_LIB) dispara avisos do Streamlit ("missing
# ScriptRunContext") e os prints de progresso de carregar_dados também vão pro
# stdout, então pega só a última linha não-vazia (mais simples que o regex de
# resumo usado em sincronizar-estoque, já que aqui controlamos o formato).
@bp.route("/dados-nfe", methods=["POST"])
def dados_nfe():
    dados = request.get_json(silent=True) or {}
    numpeds = [str(n).strip() for n in (dados.get("numpeds") or []) if str(n).strip()]
    if not numpeds:
        return {"ok": False, "motivo": "Informe ao menos um pedido do SPON (numped_spon)."}, 400

    _env = {k: v for k, v in os.environ.items() if k != "ORACLE_LIB"}
    try:
        resp = subprocess.run(
            [_PIPELINE_PYTHON, "buscar_dados_nfe.py", ",".join(numpeds)],
            cwd=_PIPELINE_DIR, capture_output=True, text=True, timeout=60, env=_env,
        )
    except subprocess.TimeoutExpired:
        return {"ok": False, "motivo": "Consulta ao Oracle demorou demais (>60s)."}, 504
    except Exception as e:
        return {"ok": False, "motivo": f"Falha ao consultar: {str(e)[:200]}"}, 500

    linhas = [l for l in (resp.stdout or "").splitlines() if l.strip()]
    try:
        resultado = json.loads(linhas[-1]) if linhas else {}
    except (json.JSONDecodeError, ValueError):
        resultado = {}
    if not isinstance(resultado, dict) or "ok" not in resultado:
        saida = ((resp.stdout or "") + (resp.stderr or ""))[-300:]
        return {"ok": False, "motivo": f"Resposta inesperada do Oracle: {saida}"}, 500
    return resultado


# ── Casamento manual (estoque_whatsapp.html) ────────────────────────────────
# Pedido do usuário em 2026-09-10: "Sem casar" no estoque_whatsapp.html
# (produto que a IA não achou no catálogo, ou casou errado) precisa de um
# jeito de escolher manualmente — busca em catalogo_data.js (já publicado,
# mesmo catálogo usado por exportacao_estoque_whatsapp.py pra IA) e grava
# aqui, no MESMO arquivo de cache que o cron usa (chave = texto normalizado
# igual exportacao_estoque_whatsapp.py::_normalizar — duplicado aqui de
# propósito: essa API roda num venv leve, sem os imports pesados do
# pipeline/Oracle que o módulo original carrega). Uma vez gravado, o cron
# nunca mais manda esse texto pra IA de novo (mesma checagem "already in
# matches" de _atualizar_matches).
bp_estoque = Blueprint("estoque_whatsapp", __name__, url_prefix="/api/estoque-whatsapp")

_ESTOQUE_MATCHES_PATH = os.path.join(
    "/opt/offtrade-pipeline" if RUNTIME == "vps" else r"G:\Meu Drive\offtrade",
    "estoque_whatsapp_matches.json",
)


def _normalizar_estoque(texto):
    s = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode("ascii")
    s = re.sub(r"[^a-z0-9]+", " ", s.lower()).strip()
    return s


@bp_estoque.route("/casar-manual", methods=["POST"])
def casar_manual_estoque():
    dados = request.get_json(silent=True) or {}
    raw_texto = str(dados.get("raw_texto", "")).strip()
    codprod = str(dados.get("codprod", "")).strip()
    descricao = str(dados.get("descricao", "")).strip()

    if not raw_texto or not codprod or not descricao:
        return {"ok": False, "motivo": "Dados incompletos."}, 400

    chave = _normalizar_estoque(raw_texto)
    if not chave:
        return {"ok": False, "motivo": "Texto do produto inválido."}, 400

    try:
        matches = {}
        if os.path.exists(_ESTOQUE_MATCHES_PATH):
            with open(_ESTOQUE_MATCHES_PATH, "r", encoding="utf-8") as f:
                matches = json.load(f)
        matches[chave] = {"codprod": codprod, "descricao": descricao}
        tmp = _ESTOQUE_MATCHES_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(matches, f, ensure_ascii=False, indent=2)
        os.replace(tmp, _ESTOQUE_MATCHES_PATH)
    except Exception as e:
        return {"ok": False, "motivo": f"Não consegui salvar ({str(e)[:150]})."}, 500

    return {"ok": True}


app.register_blueprint(bp)
app.register_blueprint(bp_estoque)

if __name__ == "__main__":
    debug = RUNTIME != "vps"
    host = "127.0.0.1" if RUNTIME == "vps" else "0.0.0.0"
    app.run(host=host, port=5056, debug=debug, threaded=True)
