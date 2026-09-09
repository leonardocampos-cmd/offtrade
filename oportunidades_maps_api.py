"""
Oportunidades de Vendas (Maps) — busca sob demanda (Flask) de estabelecimentos
(bares, restaurantes, adegas etc.) numa cidade via OpenStreetMap, e confere se
cada um já é cliente cadastrado (casamento por nome, não por CNPJ).

Contexto: pedido do usuário em 09/09/2026 era originalmente "buscar no Google
Meu Negócio/Maps, achar o CNPJ, e ver se já é cadastrado" — mas o Maps não
expõe CNPJ nenhum, e não existe API gratuita de "nome → CNPJ" (as gratuitas,
tipo BrasilAPI/ReceitaWS, fazem o caminho inverso: CNPJ → dados da empresa).
Sem chave/faturamento nenhum disponível (Google Places exigiria Google Cloud
com cartão), a fonte de PDVs aqui é o OpenStreetMap (Overpass API, gratuito,
sem cadastro) — e a conferência de "já é cliente?" é por nome+cidade
(fuzzy match via difflib), não por CNPJ exato. Cobertura de PDV no
OpenStreetMap é mais fraca que a do Google Maps, principalmente fora de
bairros centrais — aceito como ponto de partida (decisão do usuário).

Uso local: python oportunidades_maps_api.py (abre em http://localhost:5060)
Na VPS roda atrás do nginx em /api/oportunidades-maps/, mesmo padrão de
raiox_cliente_api.py/metas_builder_api.py.
"""
import os
import re
import time
import unicodedata
from difflib import SequenceMatcher

import requests
from dotenv import load_dotenv
from flask import Flask, Blueprint, request

load_dotenv()

RUNTIME = os.getenv("OFFTRADE_RUNTIME", "local")

# meta.py já chama oracledb.init_oracle_client() — importar antes de qualquer
# outra coisa evita "Oracle Client library has already been initialized"
# (mesmo padrão de raiox_cliente_api.py).
import meta  # noqa: E402
from meta import (  # noqa: E402
    engine, engine_theking, engine_castas, engine_garrido,
    engine_spon, engine_mgon, engine_blended, carregar_paralelo,
)

app = Flask(__name__)
bp = Blueprint("oportunidades_maps", __name__, url_prefix="/api/oportunidades-maps")


@app.after_request
def _cors(resp):
    # Em produção a página consome via caminho relativo (mesma origem, atrás
    # do nginx) — isso só importa em teste local, onde a página estática (ex:
    # http.server numa porta) e essa API (porta 5060) são origens diferentes
    # e o navegador bloqueia o fetch sem esse header.
    resp.headers["Access-Control-Allow-Origin"] = "*"
    return resp

_SCHEMAS = [
    ("CRC", engine), ("thekings", engine_theking), ("CASTAS", engine_castas),
    ("GARRIDO", engine_garrido), ("SPON", engine_spon), ("MGON", engine_mgon),
    ("BLENDED", engine_blended),
]

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
OVERPASS_URL = "https://overpass-api.de/api/interpreter"
# Nominatim exige User-Agent descritivo identificando a aplicação (política de
# uso do projeto OSM) — sem isso as requisições podem ser bloqueadas.
_HEADERS = {"User-Agent": "OfftradeHub-OportunidadesVendas/1.0 (uso interno Rigarr)"}

RAIO_METROS = 4000
LIMIAR_MATCH = 0.72  # score mínimo (0-1) do fuzzy match pra marcar "possível cliente"

# Palavras genéricas demais (tipo de estabelecimento, forma societária) —
# sem removê-las, "Restaurante Alecrim" batia com "Restaurante Beijing" só
# por causa da palavra RESTAURANTE em comum, gerando falso positivo em
# praticamente todo PDV do mesmo ramo (achado testando com Niterói/RJ:
# 9 dos 10 "possível cliente" eram coincidência de nome genérico).
_PALAVRAS_GENERICAS = {
    "BAR", "RESTAURANTE", "LANCHONETE", "BOTECO", "BOTEQUIM", "PUB", "CASA",
    "NOTURNA", "HOTEL", "POUSADA", "MERCADO", "SUPERMERCADO", "MERCEARIA",
    "ADEGA", "LOJA", "DEPOSITO", "DISTRIBUIDORA", "COMERCIO", "COMERCIAL",
    "BEBIDAS", "ALIMENTOS", "GASTRONOMIA", "CAFE", "LANCHES", "EMPORIO",
    "LTDA", "ME", "EPP", "EIRELI", "SA", "DE", "DA", "DO", "DOS", "DAS", "E",
}

# Categoria exibida no front -> tags OSM (amenity/shop) correspondentes.
CATEGORIAS = {
    "bar":         [("amenity", "bar"), ("amenity", "pub")],
    "restaurante": [("amenity", "restaurant")],
    "casa_noturna": [("amenity", "nightclub")],
    "hotel":       [("tourism", "hotel")],
    "adega":       [("shop", "alcohol")],
    "mercado":     [("shop", "supermarket"), ("shop", "convenience")],
}


def _geocode_cidade(cidade: str):
    """Resolve nome de cidade -> (lat, lon) via Nominatim. None se não achar."""
    params = {"q": f"{cidade}, Brasil", "format": "json", "limit": 1}
    r = requests.get(NOMINATIM_URL, params=params, headers=_HEADERS, timeout=15)
    r.raise_for_status()
    resultados = r.json()
    if not resultados:
        return None
    return float(resultados[0]["lat"]), float(resultados[0]["lon"])


def _montar_query_overpass(lat: float, lon: float, categorias: list[str]) -> str:
    tags = []
    for cat in categorias:
        tags.extend(CATEGORIAS.get(cat, []))
    if not tags:
        tags = [t for lst in CATEGORIAS.values() for t in lst]
    filtros = "".join(f'node["{k}"="{v}"](around:{RAIO_METROS},{lat},{lon});' for k, v in tags)
    return f"[out:json][timeout:25];({filtros});out body;"


def _buscar_overpass(lat: float, lon: float, categorias: list[str]) -> list[dict]:
    query = _montar_query_overpass(lat, lon, categorias)
    r = requests.post(OVERPASS_URL, data={"data": query}, headers=_HEADERS, timeout=30)
    r.raise_for_status()
    elementos = r.json().get("elements", [])

    pois = []
    for el in elementos:
        tags = el.get("tags", {})
        nome = tags.get("name")
        if not nome:
            continue
        categoria = tags.get("amenity") or tags.get("shop") or ""
        rua = tags.get("addr:street", "")
        numero = tags.get("addr:housenumber", "")
        endereco = f"{rua}, {numero}".strip(", ") if rua else ""
        pois.append({
            "nome": nome.strip(),
            "categoria": categoria,
            "endereco": endereco,
            "bairro": tags.get("addr:suburb", ""),
            "cidade": tags.get("addr:city", ""),
            "telefone": tags.get("phone") or tags.get("contact:phone") or "",
            "lat": el.get("lat"), "lon": el.get("lon"),
        })
    return pois


def _buscar_candidatos_cadastro(cidade: str) -> list[dict]:
    """Clientes cadastrados na cidade buscada, em todas as bases — candidatos
    pro fuzzy match. Consulta as 7 bases em paralelo (carregar_paralelo) em
    vez de uma atrás da outra — uma fonte lenta/travada (comum numa VPS que
    depende de VPN pra algumas distribuidoras) não pode bloquear a resposta
    da API inteira por minutos (achado testando na VPS em 09/09/2026: a
    versão sequencial nunca respondia dentro do timeout do nginx)."""
    chamadas = [
        (f"""
            SELECT CODCLI, CLIENTE, COALESCE(FANTASIA, CLIENTE) AS FANTASIA,
                   MUNICENT, ESTENT, CGCENT
            FROM {nome_schema}.PCCLIENT
            WHERE UPPER(MUNICENT) = UPPER('{cidade}')
        """, eng, f"oport_maps_cadastro_{nome_schema}")
        for nome_schema, eng in _SCHEMAS
    ]
    resultados = carregar_paralelo(chamadas)

    candidatos = []
    for (nome_schema, _eng), resultado in zip(_SCHEMAS, resultados):
        if isinstance(resultado, Exception):
            print(f"[AVISO] oport_maps_cadastro_{nome_schema} falhou — ignorado ({resultado})")
            continue
        df = resultado
        df.columns = df.columns.str.upper()
        for _, r in df.iterrows():
            candidatos.append({
                "schema": nome_schema,
                "codcli": int(r["CODCLI"]) if r["CODCLI"] is not None else None,
                "cliente": (r["CLIENTE"] or "").strip(),
                "fantasia": (r["FANTASIA"] or "").strip(),
                "cnpj": (r["CGCENT"] or "").strip(),
            })
    return candidatos


def _normalizar_nome(s: str) -> str:
    s = unicodedata.normalize("NFD", s or "").encode("ascii", "ignore").decode()
    return re.sub(r"[^A-Za-z0-9 ]", " ", s).upper()


def _nucleo_nome(s: str) -> str:
    """Nome sem palavra genérica de ramo/forma societária — só a parte que
    de fato identifica o estabelecimento."""
    tokens = [t for t in _normalizar_nome(s).split() if t and t not in _PALAVRAS_GENERICAS]
    return " ".join(tokens)


def _similaridade(a: str, b: str) -> float:
    nucleo_a, nucleo_b = _nucleo_nome(a), _nucleo_nome(b)
    # Sem núcleo de um dos dois lados (nome 100% genérico, ex: "Bar e Lanchonete"),
    # não dá pra confiar em nenhum match — melhor não sugerir do que sugerir errado.
    if not nucleo_a or not nucleo_b:
        return 0.0
    return SequenceMatcher(None, nucleo_a, nucleo_b).ratio()


def _melhor_match(nome_poi: str, candidatos: list[dict]) -> dict | None:
    melhor, melhor_score = None, 0.0
    for c in candidatos:
        for campo in ("fantasia", "cliente"):
            score = _similaridade(nome_poi, c[campo]) if c[campo] else 0.0
            if score > melhor_score:
                melhor, melhor_score = c, score
    if melhor and melhor_score >= LIMIAR_MATCH:
        return {**melhor, "score": round(melhor_score, 2)}
    return None


@bp.route("/buscar", methods=["GET"])
def buscar():
    cidade = request.args.get("cidade", "").strip()
    if not cidade:
        return {"ok": False, "motivo": "Parâmetro 'cidade' é obrigatório."}, 400
    categorias = [c for c in request.args.get("categorias", "").split(",") if c]

    try:
        coord = _geocode_cidade(cidade)
    except Exception as e:
        return {"ok": False, "motivo": f"Falha ao geolocalizar cidade: {str(e)[:200]}"}, 502
    if coord is None:
        return {"ok": False, "motivo": f"Cidade '{cidade}' não encontrada."}, 404
    lat, lon = coord

    try:
        pois = _buscar_overpass(lat, lon, categorias)
    except Exception as e:
        return {"ok": False, "motivo": f"Falha ao consultar OpenStreetMap: {str(e)[:200]}"}, 502

    candidatos = _buscar_candidatos_cadastro(cidade)

    resultados = []
    for poi in pois:
        match = _melhor_match(poi["nome"], candidatos)
        resultados.append({
            **poi,
            "status": "possivel_cliente" if match else "oportunidade",
            "match": match,
        })
    resultados.sort(key=lambda r: (r["status"] == "possivel_cliente", r["nome"]))

    return {
        "ok": True, "cidade": cidade, "total": len(resultados),
        "possiveis_clientes": sum(1 for r in resultados if r["status"] == "possivel_cliente"),
        "oportunidades": sum(1 for r in resultados if r["status"] == "oportunidade"),
        "resultados": resultados,
    }


app.register_blueprint(bp)

if __name__ == "__main__":
    debug = RUNTIME != "vps"
    host = "127.0.0.1" if RUNTIME == "vps" else "0.0.0.0"
    app.run(host=host, port=5060, debug=debug, threaded=True)
