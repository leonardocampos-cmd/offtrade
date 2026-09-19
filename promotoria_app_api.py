"""
App de Promotoria (PWA) — backend (Flask + SQLite).

Dois perfis: PROMOTOR (código PCUSUARI.CODUSUR + PIN definido pelo gestor) e
GESTOR (senha PROMO_GESTOR_SENHA no .env). Promotor faz check-in/check-out com
foto (legenda com hora/loja/tipo já gravada na imagem pelo navegador), responde
tarefas (texto/número/foto/sim-não/escolha, todas com geolocalização), segue a
rota do dia e manda a posição (ping). Gestor cria rota, tarefas, vê mapa,
visitas e respostas.

Lojas = PCCLIENT (CODUSUR1 = vendedor da loja); pessoas = PCUSUARI (não existe
PCUSUARIO no Oracle; o gestor marca na tela quais RCAs são promotores). O
Oracle só é LIDO (cache em disco, o app segue funcionando se a base cair) —
tudo que o app grava fica em SQLite local (promo_app_data/).

Uso local: python promotoria_app_api.py  (http://localhost:5059/api/promo-app/)
Na VPS roda atrás do nginx em /api/promo-app/ (ver deploy_promotoria_app_vps.py).
"""
import base64
import hashlib
import hmac
import json
import os
import secrets
import sqlite3
import struct
import threading
import time
import uuid
import zlib
from datetime import datetime, timedelta, timezone
from math import asin, cos, radians, sin, sqrt
from pathlib import Path

from dotenv import load_dotenv
from flask import Blueprint, Flask, Response, abort, jsonify, request, send_file

HERE = Path(__file__).parent
load_dotenv(HERE / ".env")

DATA_DIR = Path(os.getenv("PROMO_DATA_DIR") or (HERE / "promo_app_data"))
FOTOS_DIR = DATA_DIR / "fotos"
DB_PATH = DATA_DIR / "promo_app.db"
LOJAS_CACHE = DATA_DIR / "lojas_cache.json"
RCAS_CACHE = DATA_DIR / "rcas_cache.json"
DATA_DIR.mkdir(parents=True, exist_ok=True)
FOTOS_DIR.mkdir(parents=True, exist_ok=True)

RAIO_M = int(os.getenv("PROMO_RAIO_M", "300"))   # acima disso o check-in vira "fora do raio" (só sinaliza, não bloqueia)
CACHE_TTL_S = 6 * 3600
TZ = timezone(timedelta(hours=-3))               # Brasil sem horário de verão; VPS roda em UTC

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 25 * 1024 * 1024
bp = Blueprint("promo_app", __name__, url_prefix="/api/promo-app")


# ── util ────────────────────────────────────────────────────────────────────
def agora():
    return datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S")


def hoje():
    return datetime.now(TZ).strftime("%Y-%m-%d")


def _flt(v):
    try:
        f = float(str(v).replace(",", "."))
        return f
    except (TypeError, ValueError):
        return None


def haversine_m(lat1, lon1, lat2, lon2):
    r = 6371000
    p1, p2 = radians(lat1), radians(lat2)
    a = sin((p2 - p1) / 2) ** 2 + cos(p1) * cos(p2) * sin(radians(lon2 - lon1) / 2) ** 2
    return 2 * r * asin(sqrt(a))


# ── banco ───────────────────────────────────────────────────────────────────
SCHEMA = """
CREATE TABLE IF NOT EXISTS promotores(
  codusur INTEGER PRIMARY KEY, nome TEXT, ativo INTEGER DEFAULT 1,
  pin_hash TEXT, pin_salt TEXT, criado TEXT);
CREATE TABLE IF NOT EXISTS rotas(
  id INTEGER PRIMARY KEY AUTOINCREMENT, codusur INTEGER, data TEXT,
  codcli INTEGER, ordem INTEGER, UNIQUE(codusur, data, codcli));
CREATE TABLE IF NOT EXISTS visitas(
  id INTEGER PRIMARY KEY AUTOINCREMENT, codusur INTEGER, codcli INTEGER,
  loja_nome TEXT, data TEXT,
  in_ts TEXT, in_lat REAL, in_lng REAL, in_acc REAL, in_foto TEXT, in_dist REAL,
  out_ts TEXT, out_lat REAL, out_lng REAL, out_acc REAL, out_foto TEXT, out_dist REAL);
CREATE INDEX IF NOT EXISTS ix_vis ON visitas(codusur, data);
CREATE TABLE IF NOT EXISTS tarefas(
  id INTEGER PRIMARY KEY AUTOINCREMENT, titulo TEXT, descricao TEXT,
  perguntas TEXT, codusur INTEGER, codcli INTEGER,
  data_ini TEXT, data_fim TEXT, ativo INTEGER DEFAULT 1, criado TEXT);
CREATE TABLE IF NOT EXISTS respostas(
  id INTEGER PRIMARY KEY AUTOINCREMENT, visita_id INTEGER, tarefa_id INTEGER,
  pergunta_id TEXT, codusur INTEGER, codcli INTEGER,
  texto TEXT, numero REAL, foto TEXT, lat REAL, lng REAL, ts TEXT,
  UNIQUE(visita_id, tarefa_id, pergunta_id));
CREATE TABLE IF NOT EXISTS pings(
  id INTEGER PRIMARY KEY AUTOINCREMENT, codusur INTEGER, ts TEXT, data TEXT,
  lat REAL, lng REAL, acc REAL);
CREATE INDEX IF NOT EXISTS ix_ping ON pings(codusur, data);
"""
_local = threading.local()


def db():
    c = getattr(_local, "c", None)
    if c is None:
        c = sqlite3.connect(DB_PATH, timeout=20)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA journal_mode=WAL")
        _local.c = c
    return c


def _init_db():
    c = sqlite3.connect(DB_PATH)
    c.executescript(SCHEMA)
    c.commit()
    c.close()


_init_db()


def rows(sql, args=()):
    return [dict(r) for r in db().execute(sql, args).fetchall()]


def one(sql, args=()):
    r = db().execute(sql, args).fetchone()
    return dict(r) if r else None


# ── auth (token HMAC assinado; sem sessão no servidor) ─────────────────────
def _secret():
    s = os.getenv("PROMO_SECRET")
    if s:
        return s.encode()
    f = DATA_DIR / "secret.key"
    if not f.exists():
        f.write_text(secrets.token_hex(32))
    return f.read_text().strip().encode()


SECRET = _secret()


def make_token(role, uid, dias=30):
    body = f"{role}:{uid}:{int(time.time()) + dias * 86400}"
    sig = hmac.new(SECRET, body.encode(), hashlib.sha256).hexdigest()[:32]
    return base64.urlsafe_b64encode(f"{body}:{sig}".encode()).decode()


def ler_token(tok):
    try:
        role, uid, exp, sig = base64.urlsafe_b64decode(tok.encode()).decode().split(":")
        body = f"{role}:{uid}:{exp}"
        ok = hmac.compare_digest(sig, hmac.new(SECRET, body.encode(), hashlib.sha256).hexdigest()[:32])
        if ok and int(exp) > time.time():
            return role, int(uid) if uid.isdigit() else uid
    except Exception:
        pass
    return None, None


def exige(*roles):
    h = request.headers.get("Authorization", "")
    tok = h[7:] if h.startswith("Bearer ") else request.args.get("t", "")
    role, uid = ler_token(tok)
    if role not in roles:
        abort(401)
    if role == "promotor":
        p = one("SELECT ativo FROM promotores WHERE codusur=?", (uid,))
        if not p or not p["ativo"]:
            abort(401)
    return role, uid


_tentativas = {}


def _limite_login(chave):
    agora_t = time.time()
    lst = [t for t in _tentativas.get(chave, []) if agora_t - t < 600]
    if len(lst) >= 8:
        _tentativas[chave] = lst
        return False
    lst.append(agora_t)
    _tentativas[chave] = lst
    return True


def _hash_pin(pin, salt):
    return hashlib.pbkdf2_hmac("sha256", pin.encode(), salt.encode(), 120000).hex()


@app.errorhandler(401)
def _401(_):
    return jsonify(erro="sessão inválida ou expirada"), 401


# ── Oracle (somente leitura, cache em disco) ───────────────────────────────
_mem = {}
_lock = threading.RLock()   # reentrante: _carregar_lojas() chama rcas() sob o mesmo lock


def _cache(nome, arquivo, carregar):
    with _lock:
        c = _mem.get(nome)
        if c and time.time() - c[0] < CACHE_TTL_S:
            return c[1]
        try:
            dados = carregar()
            arquivo.write_text(json.dumps(dados, ensure_ascii=False), encoding="utf-8")
            _mem[nome] = (time.time(), dados)
            return dados
        except Exception as e:
            print(f"[AVISO] {nome}: Oracle indisponível ({e}); usando cache em disco")
            if c:
                return c[1]
            if arquivo.exists():
                dados = json.loads(arquivo.read_text(encoding="utf-8"))
                _mem[nome] = (time.time() - CACHE_TTL_S + 300, dados)   # tenta de novo em 5 min
                return dados
            return []


# PCSUPERV.CODCOORDENADOR aponta pra PCCOORDENADORVENDA, que o usuário Oracle deste projeto não
# consegue ler (ORA-00942). Enquanto o DBA não liberar o SELECT, o mapa código -> pessoa fica aqui
# (confirmado pelo usuário em 2026-09-19: coordenador 1 = João Pedro, RCA 172).
COORDENADORES = {1: {"nome": "João Pedro", "codusur": 172}}


# Regra de quem é promotor (pedido do usuário em 2026-09-19): PCUSUARI.TIPOVEND = 'P' e supervisor 238.
# Quem bate com a regra vira promotor sozinho (ver sincronizar_promotores); o gestor ainda pode
# desativar alguém ou adicionar manualmente um RCA fora da regra.
PROMOTOR_TIPOVEND = "P"
PROMOTOR_SUPERVISORES = {238}


def eh_promotor_auto(r):
    return r.get("tipovend") == PROMOTOR_TIPOVEND and r.get("supervisor") in PROMOTOR_SUPERVISORES


def _carregar_rcas():
    """Todos os RCAs não excluídos — inclusive BLOQUEIO='S': promotor não vende, então costuma
    estar bloqueado no ERP (ex: RCA 371). O nome do supervisor vem de consulta separada."""
    import meta
    df = meta.carregar_dados(
        "SELECT CODUSUR, NOME, ESTADO, CODSUPERVISOR, BLOQUEIO, TIPOVEND FROM PCUSUARI "
        "WHERE DTEXCLUSAO IS NULL ORDER BY NOME", meta.engine, "PCUSUARI")
    try:
        sups = {int(r.CODSUPERVISOR): ((r.NOME or "").strip(), None if r.CODCOORDENADOR != r.CODCOORDENADOR or r.CODCOORDENADOR is None else int(r.CODCOORDENADOR))
                for r in meta.carregar_dados("SELECT CODSUPERVISOR, NOME, CODCOORDENADOR FROM PCSUPERV", meta.engine, "PCSUPERV").itertuples()}
    except Exception:
        sups = {}
    out = []
    for r in df.itertuples():
        sup = None if r.CODSUPERVISOR is None or r.CODSUPERVISOR != r.CODSUPERVISOR else int(r.CODSUPERVISOR)
        out.append({"codusur": int(r.CODUSUR), "nome": (r.NOME or "").strip(), "estado": r.ESTADO,
                    "supervisor": sup, "supervisor_nome": sups.get(sup, ("", None))[0],
                    "coordenador": sups.get(sup, ("", None))[1], "bloqueado": r.BLOQUEIO == "S", "tipovend": (r.TIPOVEND or "").strip()})
    return out


def _carregar_lojas():
    import meta
    df = meta.carregar_dados(
        "SELECT CODCLI, CLIENTE, FANTASIA, ENDERENT, BAIRROENT, MUNICENT, ESTENT, "
        "LATITUDE, LONGITUDE, CODUSUR1 FROM PCCLIENT "
        "WHERE DTEXCLUSAO IS NULL AND BLOQUEIO='N'", meta.engine, "PCCLIENT")
    # nome do vendedor em consulta separada (nunca JOIN em query com várias fontes — ver incidente map_rca)
    try:
        vend = {r["codusur"]: r["nome"] for r in rcas()}
    except Exception:
        vend = {}
    out = []
    for r in df.itertuples():
        lat, lng = _flt(r.LATITUDE), _flt(r.LONGITUDE)
        if not lat or not lng:
            lat = lng = None
        cu = None if r.CODUSUR1 is None or r.CODUSUR1 != r.CODUSUR1 else int(r.CODUSUR1)
        out.append({"codcli": int(r.CODCLI), "nome": (r.FANTASIA or r.CLIENTE or "").strip(),
                    "razao": (r.CLIENTE or "").strip(),
                    "endereco": " · ".join(x.strip() for x in (r.ENDERENT, r.BAIRROENT) if x),
                    "cidade": ((r.MUNICENT or "") + ("/" + r.ESTENT if r.ESTENT else "")).strip(),
                    "lat": lat, "lng": lng, "codusur": cu, "vendedor": vend.get(cu, "")})
    return out


def rcas():
    return _cache("rcas", RCAS_CACHE, _carregar_rcas)


def lojas():
    return _cache("lojas", LOJAS_CACHE, _carregar_lojas)


def sincronizar_promotores():
    """Cadastra (ativo, sem PIN) quem bate com a regra e ainda não está na tabela. Quem o gestor
    desativou continua desativado — a linha existe, então não é recriada."""
    for r in rcas():
        if eh_promotor_auto(r) and not one("SELECT 1 x FROM promotores WHERE codusur=?", (r["codusur"],)):
            db().execute("INSERT INTO promotores(codusur,nome,ativo,criado) VALUES(?,?,1,?)", (r["codusur"], r["nome"], agora()))
    db().commit()


def loja_por_cod(codcli):
    for l in lojas():
        if l["codcli"] == codcli:
            return l
    return None


def _norm(s):
    import unicodedata
    return unicodedata.normalize("NFD", (s or "").lower()).encode("ascii", "ignore").decode()


def buscar_lojas(q, codusur=None, limite=40):
    qn = _norm(q).strip()
    res = []
    for l in lojas():
        if codusur and l["codusur"] != codusur:
            continue
        if qn:
            alvo = _norm(f'{l["codcli"]} {l["nome"]} {l["razao"]} {l["cidade"]} {l["endereco"]}')
            if not all(p in alvo for p in qn.split()):
                continue
        res.append(l)
        if len(res) >= limite:
            break
    return res


# ── fotos ───────────────────────────────────────────────────────────────────
def salvar_foto(arquivo, prefixo):
    if not arquivo:
        return None
    dados = arquivo.read()
    if not dados[:3] == b"\xff\xd8\xff" and dados[:8] != b"\x89PNG\r\n\x1a\n":
        abort(400, "arquivo de foto inválido")
    rel = f"{datetime.now(TZ):%Y/%m}/{prefixo}_{uuid.uuid4().hex[:12]}.jpg"
    dest = FOTOS_DIR / rel
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(dados)
    return rel


@bp.get("/foto/<path:rel>")
def foto(rel):
    exige("gestor", "promotor")
    p = (FOTOS_DIR / rel).resolve()
    if FOTOS_DIR.resolve() not in p.parents or not p.exists():
        abort(404)
    return send_file(p, mimetype="image/jpeg", max_age=86400)


# ── PWA (app, manifest, service worker, ícones) ─────────────────────────────
@bp.get("/")
@bp.get("")
def pagina():
    r = Response((HERE / "promotoria_app.html").read_text(encoding="utf-8"), mimetype="text/html")
    r.headers["Cache-Control"] = "no-cache"
    return r


@bp.get("/manifest.webmanifest")
def manifest():
    return jsonify({
        "name": "Promotoria Off Trade", "short_name": "Promotoria",
        "start_url": "/api/promo-app/", "scope": "/api/promo-app/",
        "display": "standalone", "orientation": "portrait",
        "background_color": "#0f172a", "theme_color": "#0f172a",
        "icons": [{"src": "icon-192.png", "sizes": "192x192", "type": "image/png", "purpose": "any maskable"},
                  {"src": "icon-512.png", "sizes": "512x512", "type": "image/png", "purpose": "any maskable"}],
    })


# Shell em cache p/ abrir instantâneo; /api de dados nunca é cacheado (sempre rede).
SW = """
const C='promo-shell-v1';
self.addEventListener('install',e=>{e.waitUntil(caches.open(C).then(c=>c.addAll(['./','manifest.webmanifest'])));self.skipWaiting();});
self.addEventListener('activate',e=>{e.waitUntil(caches.keys().then(k=>Promise.all(k.filter(x=>x!==C).map(x=>caches.delete(x)))));self.clients.claim();});
self.addEventListener('fetch',e=>{
  const u=new URL(e.request.url);
  if(e.request.method!=='GET'||u.pathname.split('/api/promo-app/')[1]?.match(/^(api|foto)\\//))return;
  e.respondWith(fetch(e.request).then(r=>{const cp=r.clone();caches.open(C).then(c=>c.put(e.request,cp));return r;}).catch(()=>caches.match(e.request)));
});
"""


@bp.get("/sw.js")
def sw():
    r = Response(SW, mimetype="application/javascript")
    r.headers["Cache-Control"] = "no-cache"
    return r


def _icone_png(tam):
    """PNG sólido (azul) com pino branco — gerado em Python puro, sem Pillow."""
    cx, cy, rad = tam / 2, tam * 0.42, tam * 0.24
    linhas = []
    for y in range(tam):
        linha = bytearray([0])
        for x in range(tam):
            d = sqrt((x - cx) ** 2 + (y - cy) ** 2)
            ponta = y > cy and abs(x - cx) < (tam * 0.62 - y) * 0.55 and y < tam * 0.72
            miolo = d < rad * 0.42
            if miolo:
                linha += b"\x0f\x17\x2a"
            elif d < rad or ponta:
                linha += b"\xff\xff\xff"
            else:
                linha += b"\x25\x63\xeb"
        linhas.append(bytes(linha))
    raw = b"".join(linhas)

    def chunk(t, d):
        c = struct.pack(">I", len(d)) + t + d
        return c + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", tam, tam, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))


_icones = {}


@bp.get("/icon-<int:tam>.png")
def icone(tam):
    if tam not in (192, 512):
        abort(404)
    if tam not in _icones:
        _icones[tam] = _icone_png(tam)
    r = Response(_icones[tam], mimetype="image/png")
    r.headers["Cache-Control"] = "public, max-age=604800"
    return r


# ── login ───────────────────────────────────────────────────────────────────
def _senha_erp(cod):
    """PCUSUARI.SENHA do RCA, lida na hora (nunca vai pra cache nem pra disco). None = Oracle fora do ar
    ou sem senha cadastrada. Mesma coluna/comparação de login_api.py (login de vendedor)."""
    from concurrent.futures import ThreadPoolExecutor
    def _q():
        import meta
        from sqlalchemy import text
        with meta.engine.connect() as c:
            r = c.execute(text("SELECT SENHA FROM PCUSUARI WHERE CODUSUR = :c AND DTEXCLUSAO IS NULL"), {"c": cod}).fetchone()
        return (str(r[0]).strip() or None) if r and r[0] is not None else None
    try:
        return ThreadPoolExecutor(1).submit(_q).result(timeout=12)
    except Exception as e:
        print(f"[AVISO] login promotor {cod}: não consegui ler PCUSUARI.SENHA ({e}); só o PIN reserva vale")
        return None


@bp.post("/api/login")
def login():
    d = request.get_json(force=True, silent=True) or {}
    try:
        cod = int(d.get("codusur"))
    except (TypeError, ValueError):
        return jsonify(erro="código inválido"), 400
    if not _limite_login(f"{request.remote_addr}:{cod}"):
        return jsonify(erro="muitas tentativas — aguarde 10 minutos"), 429
    sincronizar_promotores()
    p = one("SELECT * FROM promotores WHERE codusur=?", (cod,))
    senha = str(d.get("pin") or d.get("senha") or "").strip()
    ok = False
    if p and p["ativo"] and senha:
        erp = _senha_erp(cod)
        ok = bool(erp) and hmac.compare_digest(erp, senha)
        if not ok and p["pin_hash"]:                    # PIN reserva definido pelo gestor
            ok = hmac.compare_digest(p["pin_hash"], _hash_pin(senha, p["pin_salt"]))
    if not ok:
        return jsonify(erro="código ou senha incorretos (ou você ainda não foi cadastrado como promotor)"), 401
    return jsonify(token=make_token("promotor", cod), nome=p["nome"], codusur=cod, perfil="promotor")


# Mesma lista de gestores de auth.js/login.html/utils.py (EMAILS_ADMIN) — mantida em paralelo, como lá.
GESTORES_EMAIL = {
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
}
GESTORES_EMAIL |= {e.strip().lower() for e in os.getenv("PROMO_GESTORES_EXTRA", "").split(",") if e.strip()}


@bp.post("/api/login-google")
def login_google():
    """Depois do round-trip em /api/auth/oauth/login (login_api.py), o navegador volta com o cookie
    offtrade_gmail_token. Esse cookie NÃO é assinado, então não confio no e-mail dele: uso o access_token
    dele pra perguntar ao próprio Google quem é a pessoa (userinfo) e só então confiro a lista de gestores."""
    import urllib.request
    try:
        access = json.loads(request.cookies.get("offtrade_gmail_token") or "{}").get("access_token")
        if not access:
            raise ValueError("sem access_token")
        req = urllib.request.Request("https://openidconnect.googleapis.com/v1/userinfo",
                                     headers={"Authorization": "Bearer " + access})
        info = json.loads(urllib.request.urlopen(req, timeout=10).read().decode())
    except Exception:
        return jsonify(erro="não consegui confirmar o login do Google — tente de novo"), 401
    email = (info.get("email") or "").lower()
    if not info.get("email_verified") or email not in GESTORES_EMAIL:
        return jsonify(erro=f"e-mail não autorizado como gestor: {email or '?'}"), 403
    return jsonify(token=make_token("gestor", email, dias=7), nome=info.get("name") or email, perfil="gestor")


@bp.post("/api/login-gestor")
def login_gestor():
    d = request.get_json(force=True, silent=True) or {}
    senha = os.getenv("PROMO_GESTOR_SENHA", "")
    if not senha:
        return jsonify(erro="PROMO_GESTOR_SENHA não configurada no servidor"), 503
    if not _limite_login(f"{request.remote_addr}:gestor"):
        return jsonify(erro="muitas tentativas — aguarde 10 minutos"), 429
    if not hmac.compare_digest(str(d.get("senha") or ""), senha):
        return jsonify(erro="senha incorreta"), 401
    return jsonify(token=make_token("gestor", "gestor", dias=7), nome="Gestor", perfil="gestor")


# ── promotor ────────────────────────────────────────────────────────────────
def _visita_json(v):
    if not v:
        return None
    return {k: v[k] for k in ("id", "codcli", "loja_nome", "in_ts", "out_ts", "in_dist", "out_dist")}


@bp.get("/api/eu/hoje")
def eu_hoje():
    _, cod = exige("promotor")
    rota = rows("SELECT codcli, ordem FROM rotas WHERE codusur=? AND data=? ORDER BY ordem", (cod, hoje()))
    vis = {v["codcli"]: v for v in rows("SELECT * FROM visitas WHERE codusur=? AND data=? ORDER BY id", (cod, hoje()))}
    itens = []
    for r in rota:
        l = loja_por_cod(r["codcli"]) or {"codcli": r["codcli"], "nome": f'Cliente {r["codcli"]}', "endereco": "", "cidade": "", "lat": None, "lng": None}
        itens.append({**l, "ordem": r["ordem"], "visita": _visita_json(vis.pop(r["codcli"], None))})
    extras = [_visita_json(v) for v in vis.values()]                       # visitas fora da rota
    aberta = one("SELECT * FROM visitas WHERE codusur=? AND out_ts IS NULL ORDER BY id DESC LIMIT 1", (cod,))
    return jsonify(data=hoje(), rota=itens, extras=extras, aberta=_visita_json(aberta))


@bp.get("/api/lojas")
def api_lojas():
    role, uid = exige("promotor", "gestor")
    return jsonify(buscar_lojas(request.args.get("q", ""), int(request.args["codusur"]) if request.args.get("codusur") else None))


def _pos():
    lat, lng = _flt(request.form.get("lat")), _flt(request.form.get("lng"))
    if lat is None or lng is None:
        abort(400, "localização obrigatória — permita o GPS")
    return lat, lng, _flt(request.form.get("acc"))


@bp.post("/api/checkin")
def checkin():
    _, cod = exige("promotor")
    codcli = int(request.form.get("codcli", 0))
    lat, lng, acc = _pos()
    aberta = one("SELECT * FROM visitas WHERE codusur=? AND out_ts IS NULL", (cod,))
    if aberta:
        return jsonify(erro=f'Você já está em check-in em "{aberta["loja_nome"]}". Faça o check-out antes.', visita=_visita_json(aberta)), 409
    l = loja_por_cod(codcli)
    nome = l["nome"] if l else (request.form.get("loja_nome") or f"Cliente {codcli}")
    dist = haversine_m(lat, lng, l["lat"], l["lng"]) if l and l["lat"] else None
    cur = db().execute(
        "INSERT INTO visitas(codusur,codcli,loja_nome,data,in_ts,in_lat,in_lng,in_acc,in_foto,in_dist) VALUES(?,?,?,?,?,?,?,?,?,?)",
        (cod, codcli, nome, hoje(), agora(), lat, lng, acc, salvar_foto(request.files.get("foto"), f"in{cod}"), dist))
    db().commit()
    return jsonify(ok=True, visita_id=cur.lastrowid, dist=dist, fora_do_raio=bool(dist and dist > RAIO_M))


@bp.post("/api/checkout")
def checkout():
    _, cod = exige("promotor")
    v = one("SELECT * FROM visitas WHERE id=? AND codusur=?", (request.form.get("visita_id"), cod))
    if not v or v["out_ts"]:
        return jsonify(erro="visita não encontrada ou já finalizada"), 404
    lat, lng, acc = _pos()
    l = loja_por_cod(v["codcli"])
    dist = haversine_m(lat, lng, l["lat"], l["lng"]) if l and l["lat"] else None
    db().execute("UPDATE visitas SET out_ts=?,out_lat=?,out_lng=?,out_acc=?,out_foto=?,out_dist=? WHERE id=?",
                 (agora(), lat, lng, acc, salvar_foto(request.files.get("foto"), f"out{cod}"), dist, v["id"]))
    db().commit()
    return jsonify(ok=True)


def _tarefas_da_visita(v):
    out = []
    for t in rows("SELECT * FROM tarefas WHERE ativo=1 AND (codusur IS NULL OR codusur=?) AND (codcli IS NULL OR codcli=?) "
                  "AND (data_ini IS NULL OR data_ini<=?) AND (data_fim IS NULL OR data_fim>=?) ORDER BY id",
                  (v["codusur"], v["codcli"], v["data"], v["data"])):
        t["perguntas"] = json.loads(t["perguntas"] or "[]")
        resp = {r["pergunta_id"]: r for r in rows("SELECT * FROM respostas WHERE visita_id=? AND tarefa_id=?", (v["id"], t["id"]))}
        for p in t["perguntas"]:
            p["resposta"] = resp.get(p["id"])
        t["pendentes"] = sum(1 for p in t["perguntas"] if p.get("obrigatoria") and p["id"] not in resp)
        out.append(t)
    return out


@bp.get("/api/visitas/<int:vid>/tarefas")
def visita_tarefas(vid):
    _, cod = exige("promotor")
    v = one("SELECT * FROM visitas WHERE id=? AND codusur=?", (vid, cod))
    if not v:
        abort(404)
    return jsonify(visita=_visita_json(v), tarefas=_tarefas_da_visita(v))


@bp.post("/api/respostas")
def responder():
    _, cod = exige("promotor")
    v = one("SELECT * FROM visitas WHERE id=? AND codusur=?", (request.form.get("visita_id"), cod))
    if not v:
        abort(404)
    tid, pid = int(request.form["tarefa_id"]), request.form["pergunta_id"]
    tarefa = one("SELECT perguntas FROM tarefas WHERE id=?", (tid,))
    if not tarefa or pid not in {p["id"] for p in json.loads(tarefa["perguntas"] or "[]")}:
        abort(404)
    ant = one("SELECT foto FROM respostas WHERE visita_id=? AND tarefa_id=? AND pergunta_id=?", (v["id"], tid, pid))
    foto_rel = salvar_foto(request.files.get("foto"), f"r{cod}") or (ant and ant["foto"])
    db().execute(
        "INSERT INTO respostas(visita_id,tarefa_id,pergunta_id,codusur,codcli,texto,numero,foto,lat,lng,ts) VALUES(?,?,?,?,?,?,?,?,?,?,?) "
        "ON CONFLICT(visita_id,tarefa_id,pergunta_id) DO UPDATE SET texto=excluded.texto,numero=excluded.numero,"
        "foto=excluded.foto,lat=excluded.lat,lng=excluded.lng,ts=excluded.ts",
        (v["id"], tid, pid, cod, v["codcli"], request.form.get("texto") or None, _flt(request.form.get("numero")),
         foto_rel, _flt(request.form.get("lat")), _flt(request.form.get("lng")), agora()))
    db().commit()
    return jsonify(ok=True)


@bp.post("/api/ping")
def ping():
    _, cod = exige("promotor")
    d = request.get_json(force=True, silent=True) or {}
    lat, lng = _flt(d.get("lat")), _flt(d.get("lng"))
    if lat is None or lng is None:
        abort(400)
    db().execute("INSERT INTO pings(codusur,ts,data,lat,lng,acc) VALUES(?,?,?,?,?,?)",
                 (cod, agora(), hoje(), lat, lng, _flt(d.get("acc"))))
    db().commit()
    return jsonify(ok=True)


# ── gestor ──────────────────────────────────────────────────────────────────
@bp.get("/api/g/rcas")
def g_rcas():
    exige("gestor")
    q = _norm(request.args.get("q", ""))
    sup, coord = request.args.get("supervisor"), request.args.get("coordenador")
    marcados = {p["codusur"] for p in rows("SELECT codusur FROM promotores")}
    res = [{**r, "promotor": r["codusur"] in marcados} for r in rcas()
           if (not q or q in _norm(f'{r["codusur"]} {r["nome"]}')) and (not sup or str(r["supervisor"]) == sup)
           and (not coord or str(r["coordenador"]) == coord)]
    for r in res:
        r["coordenador_nome"] = COORDENADORES.get(r["coordenador"], {}).get("nome", "")
    return jsonify(res[:80])


@bp.get("/api/g/supervisores")
def g_supervisores():
    exige("gestor")
    cont = {}
    for r in rcas():
        if r["supervisor"] is not None:
            c = cont.setdefault(r["supervisor"], {"codigo": r["supervisor"], "nome": r["supervisor_nome"], "coordenador": r["coordenador"], "rcas": 0})
            c["rcas"] += 1
    return jsonify(sorted(cont.values(), key=lambda x: -x["rcas"]))


@bp.get("/api/g/coordenadores")
def g_coordenadores():
    exige("gestor")
    cont = {}
    for r in rcas():
        if r["coordenador"] is not None:
            cont[r["coordenador"]] = cont.get(r["coordenador"], 0) + 1
    return jsonify([{"codigo": k, "nome": COORDENADORES.get(k, {}).get("nome", f"Coordenador {k}"), "rcas": n} for k, n in cont.items()])


@bp.get("/api/g/promotores")
def g_promotores():
    exige("gestor")
    sincronizar_promotores()
    auto = {r["codusur"] for r in rcas() if eh_promotor_auto(r)}
    return jsonify([{**p, "auto": p["codusur"] in auto} for p in
                    rows("SELECT codusur, nome, ativo, (pin_hash IS NOT NULL) AS tem_pin FROM promotores ORDER BY nome")])


@bp.post("/api/g/promotores")
def g_promotor_salvar():
    exige("gestor")
    d = request.get_json(force=True, silent=True) or {}
    cod = int(d["codusur"])
    if not one("SELECT 1 x FROM promotores WHERE codusur=?", (cod,)):
        nome = next((r["nome"] for r in rcas() if r["codusur"] == cod), d.get("nome") or f"RCA {cod}")
        db().execute("INSERT INTO promotores(codusur,nome,ativo,criado) VALUES(?,?,1,?)", (cod, nome, agora()))
    if "ativo" in d:
        db().execute("UPDATE promotores SET ativo=? WHERE codusur=?", (1 if d["ativo"] else 0, cod))
    pin = str(d.get("pin") or "")
    if pin:
        if not (pin.isdigit() and 4 <= len(pin) <= 8):
            return jsonify(erro="PIN deve ter de 4 a 8 dígitos"), 400
        salt = secrets.token_hex(8)
        db().execute("UPDATE promotores SET pin_hash=?, pin_salt=? WHERE codusur=?", (_hash_pin(pin, salt), salt, cod))
    if d.get("remover"):
        if any(r["codusur"] == cod and eh_promotor_auto(r) for r in rcas()):   # entraria de novo na sincronização
            db().execute("UPDATE promotores SET ativo=0 WHERE codusur=?", (cod,))
        else:
            db().execute("DELETE FROM promotores WHERE codusur=?", (cod,))
    db().commit()
    return jsonify(ok=True)


@bp.get("/api/g/mapa")
def g_mapa():
    exige("gestor")
    sincronizar_promotores()
    out = []
    for p in rows("SELECT codusur, nome FROM promotores WHERE ativo=1 ORDER BY nome"):
        ult = one("SELECT ts, lat, lng, acc FROM pings WHERE codusur=? AND data=? ORDER BY id DESC LIMIT 1", (p["codusur"], hoje()))
        aberta = one("SELECT loja_nome, in_ts FROM visitas WHERE codusur=? AND out_ts IS NULL ORDER BY id DESC LIMIT 1", (p["codusur"],))
        res = one("SELECT COUNT(*) n, SUM(out_ts IS NOT NULL) fim FROM visitas WHERE codusur=? AND data=?", (p["codusur"], hoje()))
        rota = one("SELECT COUNT(*) n FROM rotas WHERE codusur=? AND data=?", (p["codusur"], hoje()))["n"]
        out.append({**p, "ultimo": ult, "aberta": aberta, "visitas": res["n"], "finalizadas": res["fim"] or 0, "rota": rota})
    return jsonify(out)


@bp.get("/api/g/trilha")
def g_trilha():
    exige("gestor")
    return jsonify(rows("SELECT ts, lat, lng FROM pings WHERE codusur=? AND data=? ORDER BY id",
                        (request.args["codusur"], request.args.get("data") or hoje())))


@bp.get("/api/g/visitas")
def g_visitas():
    exige("gestor")
    data = request.args.get("data") or hoje()
    sql, args = "SELECT * FROM visitas WHERE data=?", [data]
    if request.args.get("codusur"):
        sql += " AND codusur=?"
        args.append(request.args["codusur"])
    nomes = {p["codusur"]: p["nome"] for p in rows("SELECT codusur, nome FROM promotores")}
    out = []
    for v in rows(sql + " ORDER BY in_ts", args):
        v["promotor"] = nomes.get(v["codusur"], str(v["codusur"]))
        v["respostas"] = []
        for r in rows("SELECT r.*, t.titulo, t.perguntas FROM respostas r JOIN tarefas t ON t.id=r.tarefa_id WHERE r.visita_id=? ORDER BY r.id", (v["id"],)):
            perg = next((p for p in json.loads(r.pop("perguntas") or "[]") if p["id"] == r["pergunta_id"]), {})
            r["pergunta"] = perg.get("texto", r["pergunta_id"])
            v["respostas"].append(r)
        out.append(v)
    return jsonify(out)


@bp.get("/api/g/rota")
def g_rota_ler():
    exige("gestor")
    itens = []
    for r in rows("SELECT codcli, ordem FROM rotas WHERE codusur=? AND data=? ORDER BY ordem", (request.args["codusur"], request.args["data"])):
        itens.append({**(loja_por_cod(r["codcli"]) or {"codcli": r["codcli"], "nome": f'Cliente {r["codcli"]}', "endereco": "", "cidade": ""}), "ordem": r["ordem"]})
    return jsonify(itens)


@bp.post("/api/g/rota")
def g_rota_salvar():
    """Substitui a rota de um promotor em uma ou várias datas (copiar rota = várias datas)."""
    exige("gestor")
    d = request.get_json(force=True, silent=True) or {}
    cod, datas, clis = int(d["codusur"]), d.get("datas") or [d["data"]], [int(c) for c in d.get("codclis", [])]
    for dt in datas:
        db().execute("DELETE FROM rotas WHERE codusur=? AND data=?", (cod, dt))
        for i, c in enumerate(dict.fromkeys(clis)):
            db().execute("INSERT INTO rotas(codusur,data,codcli,ordem) VALUES(?,?,?,?)", (cod, dt, c, i + 1))
    db().commit()
    return jsonify(ok=True, datas=len(datas), lojas=len(set(clis)))


@bp.get("/api/g/tarefas")
def g_tarefas():
    exige("gestor")
    ts = rows("SELECT * FROM tarefas ORDER BY id DESC")
    for t in ts:
        t["perguntas"] = json.loads(t["perguntas"] or "[]")
        t["respostas"] = one("SELECT COUNT(*) n FROM respostas WHERE tarefa_id=?", (t["id"],))["n"]
    return jsonify(ts)


TIPOS = {"texto", "numero", "foto", "sim_nao", "escolha"}


@bp.post("/api/g/tarefas")
def g_tarefa_salvar():
    exige("gestor")
    d = request.get_json(force=True, silent=True) or {}
    if d.get("id") and ("ativo" in d) and len(d) == 2:                      # só ativar/desativar
        db().execute("UPDATE tarefas SET ativo=? WHERE id=?", (1 if d["ativo"] else 0, d["id"]))
        db().commit()
        return jsonify(ok=True)
    perguntas = []
    for i, p in enumerate(d.get("perguntas") or []):
        if p.get("tipo") not in TIPOS or not (p.get("texto") or "").strip():
            return jsonify(erro=f"pergunta {i + 1} inválida"), 400
        perguntas.append({"id": p.get("id") or f"p{i + 1}", "texto": p["texto"].strip(), "tipo": p["tipo"],
                          "obrigatoria": bool(p.get("obrigatoria")), "permite_foto": bool(p.get("permite_foto")),
                          "opcoes": [o.strip() for o in (p.get("opcoes") or []) if o.strip()]})
    if not (d.get("titulo") or "").strip() or not perguntas:
        return jsonify(erro="informe título e ao menos uma pergunta"), 400
    vals = (d["titulo"].strip(), d.get("descricao") or "", json.dumps(perguntas, ensure_ascii=False),
            d.get("codusur") or None, d.get("codcli") or None, d.get("data_ini") or None, d.get("data_fim") or None)
    if d.get("id"):
        db().execute("UPDATE tarefas SET titulo=?,descricao=?,perguntas=?,codusur=?,codcli=?,data_ini=?,data_fim=? WHERE id=?", vals + (d["id"],))
    else:
        db().execute("INSERT INTO tarefas(titulo,descricao,perguntas,codusur,codcli,data_ini,data_fim,criado) VALUES(?,?,?,?,?,?,?,?)", vals + (agora(),))
    db().commit()
    return jsonify(ok=True)


app.register_blueprint(bp)

if __name__ == "__main__":
    if not os.getenv("PROMO_GESTOR_SENHA"):
        print("[AVISO] defina PROMO_GESTOR_SENHA no .env para o gestor conseguir entrar")
    app.run(host="127.0.0.1" if os.getenv("OFFTRADE_RUNTIME") == "vps" else "0.0.0.0", port=5059)
