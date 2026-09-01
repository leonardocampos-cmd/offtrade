"""
Exportação do relatório de execução (pesquisas e tarefas) do Max Promotor,
filtrado por supervisor e período — gera promotoria_data.js pra
promotoria.html (gestor-only). Adaptado do script de referência do usuário
(2026-08-20), que gerava dois CSVs; aqui os mesmos dados viram um único
JSON servido pela página, e roda como passo do pipeline (main.py) em vez de
execução manual.

Como a API do Max Promotor não suporta filtro por intervalo de datas nem
por usuário diretamente nas consultas em lote, a estratégia usada é:
  1. Descobrir a equipe do supervisor (usuários subordinados, recursivamente)
  2. Para cada usuário da equipe, buscar as visitas dele (aninhado, uma
     consulta GraphQL por usuário), ordenar da data mais atual para a mais
     antiga e filtrar por período em Python
  3. Para o detalhe de pesquisa (assunto/pergunta/item/avaliado/resposta/foto),
     que não tem relação aninhada direta no schema, cruzar com as tabelas de
     referência (buscadas uma única vez, no início) em Python
  4. Montar o payload de forma incremental, usuário por usuário, salvando o
     arquivo a cada usuário — se a execução for interrompida no meio, o
     próximo run continua com o que já tem em vez de perder tudo.
"""
import json
import os
import re
import time
from datetime import datetime, timedelta
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

DOMAIN = "brasilcomercio.maxpromotor.com.br"
LOGIN_URL = f"https://{DOMAIN}/web/integracao/api/auth/login"
GRAPHQL_URL = f"https://{DOMAIN}/graphql"

OUTPUT_PATH = Path(__file__).parent / "promotoria_data.js"

# ------- parâmetros do relatório -------
NOME_SUPERVISOR = "DANIEL DINIZ"
CODIGO_SUPERVISOR = "255"
DATA_INICIO = datetime(2026, 1, 1)
DATA_FIM = None  # None = sem limite superior (até agora)


def login():
    usuario_api = os.getenv("MAXPROMOTOR_USER")
    senha_api = os.getenv("MAXPROMOTOR_PASS")
    if not usuario_api or not senha_api:
        raise RuntimeError("MAXPROMOTOR_USER/MAXPROMOTOR_PASS não configurados no .env")
    res = requests.post(LOGIN_URL, json={"username": usuario_api, "password": senha_api}, timeout=30)
    res.raise_for_status()
    return res.json()["accessToken"]


def consultar(token, query):
    """3 tentativas com espera — blip de rede/VPN transitório já derrubou
    execuções inteiras de outros scripts do pipeline (mesmo padrão de
    meta.py::carregar_dados e email_pedidos.py); aqui cada usuário tem uma
    consulta pesada (visitas aninhadas), então uma falha isolada não pode
    custar o usuário inteiro."""
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {token}"}
    ultimo_erro = None
    for tentativa in range(1, 4):
        try:
            res = requests.post(GRAPHQL_URL, json={"query": query}, headers=headers, timeout=300)
            data = res.json()
        except (requests.exceptions.RequestException, ValueError) as e:
            ultimo_erro = e
            if tentativa < 3:
                time.sleep(5 * tentativa)
            continue
        if "errors" in data:
            raise RuntimeError(data["errors"])
        return data["data"]
    raise ultimo_erro


import zoneinfo
_TZ_BRT = zoneinfo.ZoneInfo("America/Sao_Paulo")
_TZ_UTC = zoneinfo.ZoneInfo("UTC")


def parse_ts(valor):
    """A API do Max Promotor devolve tsEntrada/tsSaida em UTC mas SEM sufixo
    'Z'/offset (ex: '2026-08-31T17:42:21') — parece hora local, mas não é.
    Confirmado pelo usuário em 2026-08-31 comparando com o horário mostrado
    no próprio app na foto de check-in (14:42, ~3h antes do que a gente
    exibia): sem essa conversão, TODO check-in/check-out/data ficava 3h
    "adiantado" — e isso inclusive gerava falso-positivo na análise de fotos
    fora da janela do check-in/checkout (o gap de ~3h sempre foi esse bug,
    não relógio de aparelho desconfigurado)."""
    if not valor:
        return None
    dt = datetime.fromisoformat(valor.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=_TZ_UTC)
    return dt.astimezone(_TZ_BRT).replace(tzinfo=None)


def _distancia_m(loc, endereco):
    """Distância (Haversine, em metros) entre uma localização de GPS
    (dict com latitude/longitude, de ProwLocalizacao) e o endereço
    cadastrado do PDV (dict com latitude/longitude, de ProwEndereco).
    None se faltar coordenada de qualquer um dos dois lados."""
    try:
        lat1, lon1 = float(loc.get("latitude")), float(loc.get("longitude"))
        lat2, lon2 = float(endereco.get("latitude")), float(endereco.get("longitude"))
    except (TypeError, ValueError):
        return None
    from math import radians, sin, cos, asin, sqrt
    r = 6371000  # raio da Terra em metros
    dlat, dlon = radians(lat2 - lat1), radians(lon2 - lon1)
    a = sin(dlat / 2) ** 2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlon / 2) ** 2
    return round(2 * r * asin(sqrt(a)))


def dentro_do_periodo(ts_entrada):
    dt = parse_ts(ts_entrada)
    if dt is None:
        return False
    if dt < DATA_INICIO:
        return False
    if DATA_FIM and dt > DATA_FIM:
        return False
    return True


def resolver_campo_nome_cidade(token):
    query = '{ __type(name: "ProwCidade") { fields { name } } }'
    data = consultar(token, query)
    campos = [f["name"] for f in data["__type"]["fields"]]
    for candidato in ("nome", "descricao"):
        if candidato in campos:
            return candidato
    return next(c for c in campos if c not in ("id", "nodeId"))


def buscar_todas_cidades(token, campo_cidade):
    d = consultar(token, f"{{ allProwCidades {{ nodes {{ id {campo_cidade} }} }} }}")
    return {n["id"]: n[campo_cidade] for n in d["allProwCidades"]["nodes"]}


def montar_mapa_equipe(token, codigo_supervisor, nome_supervisor):
    dados = consultar(token, "{ allProwUsuarios { nodes { id nome codigo login } } }")
    usuarios = dados["allProwUsuarios"]["nodes"]

    supervisor = next(
        (u for u in usuarios if u["codigo"] == codigo_supervisor and nome_supervisor in u["nome"]),
        None,
    )
    if supervisor is None:
        raise RuntimeError(f"Supervisor não encontrado: codigo={codigo_supervisor} nome={nome_supervisor}")

    dados_sub = consultar(token, "{ allProwUsuarioSubordinados { nodes { subordinadoId superiorId } } }")
    subordinados = dados_sub["allProwUsuarioSubordinados"]["nodes"]

    mapa_superior_para_subs = {}
    for s in subordinados:
        mapa_superior_para_subs.setdefault(s["superiorId"], []).append(s["subordinadoId"])

    equipe_ids = set()
    fila = [str(supervisor["id"])]
    while fila:
        atual = fila.pop()
        for sub_id in mapa_superior_para_subs.get(atual, []):
            if sub_id not in equipe_ids:
                equipe_ids.add(sub_id)
                fila.append(sub_id)

    usuarios_por_id = {str(u["id"]): u for u in usuarios}
    equipe = [usuarios_por_id[i] for i in equipe_ids if i in usuarios_por_id]
    return supervisor, equipe


def buscar_dados_usuario(token, usuario_id):
    query = f"""
    {{
      prowUsuarioById(id: {usuario_id}) {{
        id
        nome
        codigo
        prowUsuarioPerfilByPerfilId {{ perfil }}
        prowEmpresaUsuariosByUsuarioId {{
          nodes {{ prowEmpresaByEmpresaId {{ nome }} }}
        }}
        prowVisitasByUsuarioId {{
          nodes {{
            id
            tsEntrada
            tsSaida
            observacao
            tipoJustificativa
            prowMotivoVisitaByMotivoVisitaId {{ descricao }}
            prowLocalizacaoByLocalizacaoCheckinId {{ latitude longitude }}
            prowLocalizacaoByLocalizacaoCheckoutId {{ latitude longitude }}
            prowPontoVendaByPontoVendaId {{
              nome
              fantasia
              codigo
              cpfcnpj
              prowEnderecoPontoVendasByPontoVendaId {{
                nodes {{ prowEnderecoByEnderecoId {{ cidadeId latitude longitude }} }}
              }}
            }}
            prowVisitaRespostaPesquisasByVisitaId {{
              nodes {{
                prowRespostaPesquisaByRespostaPesquisaId {{
                  id
                  prowPesquisaByPesquisaId {{ descricao }}
                }}
              }}
            }}
            prowVisitaRespostaTarefasByVisitaId {{
              nodes {{
                prowRespostaTarefaByRespostaTarefaId {{
                  id
                  prowChecklistByTarefaId {{ descricao tipo finalidade }}
                  prowRespostaAtividadeTarefasByRespostaTarefaId {{
                    nodes {{
                      prowItemChecklistByAtividadeTarefaId {{
                        prowPerguntaByPerguntaId {{ descricao }}
                      }}
                      prowRespostaAtvdTarefaValorsByRespostaAtividadeTarefaId {{
                        nodes {{ valor numero }}
                      }}
                      prowFotoRespAtvTarefasByRespostaAtividadeTarefaId {{
                        nodes {{
                          prowFotoByFotoMarcaId {{ caminho }}
                          prowFotoByFotoOriginalId {{ caminho }}
                        }}
                      }}
                    }}
                  }}
                }}
              }}
            }}
          }}
        }}
      }}
    }}
    """
    return consultar(token, query)["prowUsuarioById"]


def buscar_referencias_pesquisa(token):
    """Tabelas de referência globais pro detalhe de pesquisa (assunto/
    pergunta/item avaliado) — buscadas uma única vez, no início."""
    refs = {}

    # Todo id aqui vira string na hora de virar chave/valor de referência —
    # a API mistura tipos entre 'id' consultado direto (int) e os campos de
    # chave estrangeira tipo 'xxxId' (string), então comparar sem normalizar
    # nunca dava match (bug real: 100% dos campos assunto/pergunta/resposta
    # ficavam vazios até o cruzamento inteiro ser passado a string —
    # confirmado em 2026-08-20).
    d = consultar(token, "{ allProwAssuntos { nodes { id descricao } } }")
    refs["assunto"] = {str(n["id"]): n["descricao"] for n in d["allProwAssuntos"]["nodes"]}

    d = consultar(token, "{ allProwPerguntas { nodes { id descricao } } }")
    refs["pergunta"] = {str(n["id"]): n["descricao"] for n in d["allProwPerguntas"]["nodes"]}

    d = consultar(token, "{ allProwItemAvaliados { nodes { id nome } } }")
    refs["itemAvaliado"] = {str(n["id"]): n["nome"] for n in d["allProwItemAvaliados"]["nodes"]}

    d = consultar(token, "{ allProwAssuntoPesquisas { nodes { id idAssunto } } }")
    refs["assuntoPesquisa"] = {str(n["id"]): str(n["idAssunto"]) for n in d["allProwAssuntoPesquisas"]["nodes"]}

    d = consultar(token, "{ allProwPergAssunPesqs { nodes { id assuntoPesquisaId perguntaId } } }")
    refs["pergAssunPesq"] = {
        str(n["id"]): {"assuntoPesquisaId": str(n["assuntoPesquisaId"]), "perguntaId": str(n["perguntaId"])}
        for n in d["allProwPergAssunPesqs"]["nodes"]
    }

    d = consultar(token, "{ allProwItemAvaliPergPesqs { nodes { id itemAvaliadoId perguntaAssunPesqId } } }")
    refs["itemAvaliPergPesq"] = {
        str(n["id"]): {"itemAvaliadoId": str(n["itemAvaliadoId"]), "perguntaAssunPesqId": str(n["perguntaAssunPesqId"])}
        for n in d["allProwItemAvaliPergPesqs"]["nodes"]
    }

    return refs


# Detalhe item a item (pergunta + resposta de cada item avaliado) só é
# buscado pra pesquisas dos últimos N dias — histórico completo (8 meses x
# ~50 pessoas) explodiria o arquivo de novo (por isso foi resumido em
# 2026-08-20). Pedido do usuário em 2026-08-31 ("não tem resposta pra
# assunto?"): reabrir o detalhe, mas só recente.
PESQUISA_DETALHE_DIAS = 30


def buscar_itens_resposta_pesquisa(token, resposta_pesquisa_ids, ids_detalhe=None):
    """Itens/fotos de resposta de pesquisa, filtrados pelos ids de resposta
    informados (tipicamente, os de um único usuário). O resumo (contagem de
    itens/fotos + assuntos distintos) é buscado pra todo mundo; os VALORES de
    resposta item a item (allProwRespostaPesqItemValors) só são buscados pros
    ids em `ids_detalhe` (subconjunto recente, ver PESQUISA_DETALHE_DIAS) —
    None busca pra todo mundo."""
    ids_set = {str(i) for i in resposta_pesquisa_ids}

    d = consultar(token, "{ allProwRespostaPesquisaItems { nodes { id respostaPesquisaId itemAvaliPergPesqId } } }")
    itens = [n for n in d["allProwRespostaPesquisaItems"]["nodes"] if str(n["respostaPesquisaId"]) in ids_set]
    ids_item = {str(n["id"]) for n in itens}

    d = consultar(
        token,
        """
        {
          allProwFotoRespPesqItems {
            nodes {
              respostaPesquisaItemId
              tipo
              prowFotoByFotoMarcaId { caminho }
              prowFotoByFotoOriginalId { caminho }
            }
          }
        }
        """,
    )
    fotos_por_item = {}
    for n in d["allProwFotoRespPesqItems"]["nodes"]:
        if str(n["respostaPesquisaItemId"]) in ids_item:
            caminho = (n["prowFotoByFotoMarcaId"] or {}).get("caminho") or (n["prowFotoByFotoOriginalId"] or {}).get("caminho")
            fotos_por_item.setdefault(str(n["respostaPesquisaItemId"]), []).append(caminho)

    ids_item_detalhe = ids_item if ids_detalhe is None else {
        str(n["id"]) for n in itens if str(n["respostaPesquisaId"]) in {str(i) for i in ids_detalhe}
    }
    valores_por_item = {}
    if ids_item_detalhe:
        d = consultar(token, "{ allProwRespostaPesqItemValors { nodes { respostaPesquisaItemId valor numero } } }")
        for n in d["allProwRespostaPesqItemValors"]["nodes"]:
            if str(n["respostaPesquisaItemId"]) in ids_item_detalhe:
                v = n["valor"] or (str(n["numero"]) if n["numero"] is not None else "")
                if v:
                    valores_por_item.setdefault(str(n["respostaPesquisaItemId"]), []).append(v)

    return itens, fotos_por_item, valores_por_item


# ── Aba "Visitas" (timeline + análise de IA) ──────────────────────────────────
# Pedido do usuário em 2026-08-31: uma linha por VISITA (não por pesquisa ou
# tarefa individual), agrupando pelo visita_id já presente em cada linha de
# pesquisa/tarefa, pra alimentar um timeline (check-in -> pesquisas -> tarefas
# -> check-out) com análise de IA de verdade (chamada à OpenAI, não só regras).

def _visita_base(r):
    return {
        "visita_id":          r["visita_id"],
        "data":                r.get("data", ""),
        "usuario":             r.get("usuario", ""),
        "razao_social":        r.get("razao_social", ""),
        "fantasia":            r.get("fantasia", ""),
        "cnpj":                r.get("cpf_cnpj_pdv", ""),
        "cidade":              "",
        "bairro":              "",
        "check_in":            r.get("check_in", ""),
        "check_out":           r.get("check_out", ""),
        "observacao_visita":   r.get("observacao_visita", ""),
        "tipo_justificativa":  r.get("tipo_justificativa", ""),
        "motivo_visita":       r.get("motivo_visita", ""),
        "dist_checkin_m":      r.get("dist_checkin_m"),
        "dist_checkout_m":     r.get("dist_checkout_m"),
        "pesquisas":           [],
        "tarefas":             [],
        "fotos":               [],
        "analise_ia":          "",
    }


def _montar_visitas(pesquisas, tarefas):
    por_visita = {}
    for r in pesquisas:
        vid = r.get("visita_id")
        if not vid:
            continue
        v = por_visita.setdefault(vid, _visita_base(r))
        v["pesquisas"].append({
            "pesquisa": r.get("pesquisa", ""),
            "assuntos": r.get("assuntos", ""),
            "qtd_itens": r.get("qtd_itens", 0),
        })
        v["fotos"].extend(f for f in (r.get("fotos") or []) if f)
    for r in tarefas:
        vid = r.get("visita_id")
        if not vid:
            continue
        v = por_visita.setdefault(vid, _visita_base(r))
        v["tarefas"].append({
            "tarefa": r.get("tarefa", ""),
            "tipo_tarefa": r.get("tipo_tarefa", ""),
            "pergunta": r.get("pergunta", ""),
            "resposta": r.get("resposta", ""),
        })
        v["fotos"].extend(f for f in (r.get("fotos") or []) if f)
    visitas = list(por_visita.values())
    visitas.sort(key=lambda v: (v["data"], v["check_in"]), reverse=True)
    return visitas


def _resolver_cidade_bairro_crc(visitas):
    """Cidade/bairro sempre resolvidos no CRC (PCCLIENT), via CNPJ do PDV —
    pedido do usuário em 2026-08-31 (a cidade própria do Max Promotor fica em
    branco quando o endereço não está cadastrado lá; o CRC é a fonte de
    verdade)."""
    cnpjs = {re.sub(r"\D", "", v.get("cnpj") or "") for v in visitas}
    cnpjs.discard("")
    if not cnpjs:
        return
    from meta import engine, carregar_dados
    # Oracle rejeita IN(...) com mais de 1000 elementos (ORA-01795) — com 8
    # meses de histórico x ~52 usuários dá pra passar fácil disso, então
    # busca em lotes.
    cnpjs_lista = sorted(cnpjs)
    mapa = {}
    for i in range(0, len(cnpjs_lista), 900):
        lote = cnpjs_lista[i:i + 900]
        cnpjs_sql = ",".join(f"'{c}'" for c in lote)
        try:
            df = carregar_dados(f"""
                SELECT REPLACE(REPLACE(REPLACE(C.CGCENT,'.',''),'/',''),'-','') AS CNPJ,
                       C.MUNICENT AS CIDADE, C.BAIRROENT AS BAIRRO
                FROM crc.PCCLIENT C
                WHERE REPLACE(REPLACE(REPLACE(C.CGCENT,'.',''),'/',''),'-','') IN ({cnpjs_sql})
            """, engine, "promotoria_cidade_bairro")
            df.columns = df.columns.str.upper()
            mapa.update({str(row["CNPJ"]): (row["CIDADE"] or "", row["BAIRRO"] or "") for _, row in df.iterrows()})
        except Exception as e:
            print(f"  [AVISO] busca de cidade/bairro (CRC.PCCLIENT), lote {i // 900 + 1} falhou ({str(e)[:100]}) — fica em branco pra esse lote.")
    for v in visitas:
        cidade, bairro = mapa.get(re.sub(r"\D", "", v.get("cnpj") or ""), ("", ""))
        v["cidade"], v["bairro"] = cidade, bairro


ANALISE_IA_CACHE_PATH   = Path(__file__).parent / "promotoria_analises_ia.json"
ANALISE_IA_MODEL        = os.getenv("OPENAI_TEXT_MODEL", "gpt-4o-mini")
ANALISE_IA_DIAS         = 30   # só vale a pena gastar com IA em visita recente
ANALISE_IA_MAX_POR_RUN  = 80   # cron roda de hora em hora — dá pra ir alcançando aos poucos

_PROMPT_SISTEMA_ANALISE_VISITA = """Você analisa visitas de promotores de trade marketing em pontos de venda (PDV).
Recebe um resumo estruturado de uma visita: check-in/check-out, distância (em
metros) entre onde o GPS do celular marcou o check-in/check-out e o endereço
cadastrado do PDV, motivo/justificativa da visita, observação do promotor,
pesquisas respondidas (com assuntos) e tarefas respondidas (pergunta/resposta),
e quantidade de fotos anexadas.

Escreva um parecer curto (2 a 4 frases, português, direto, sem saudação) sobre
se a visita parece consistente e completa. Aponte só problemas REAIS e
objetivos que dá pra inferir dos dados fornecidos — por exemplo: visita muito
curta pra quantidade de pesquisas/tarefas respondidas; distância grande entre
o GPS do check-in/checkout e o endereço do PDV (pode ser check-in feito de
outro lugar, ou endereço do PDV desatualizado); tarefa sem nenhuma resposta
registrada; check-out ausente; zero fotos numa visita que deveria ter. NÃO
invente problema que não dá pra inferir do texto. Se estiver tudo normal,
diga isso numa frase só, sem forçar crítica."""


def _prompt_visita(v):
    linhas = [
        f"Visita de {v['usuario']} em {v['data']} — cliente: "
        f"{v['fantasia'] or v['razao_social']} ({v['cidade']}/{v['bairro']})."
    ]
    if v["check_in"]:
        linhas.append(f"Check-in: {v['check_in']}" + (f" (a {v['dist_checkin_m']}m do endereço cadastrado do PDV)" if v.get("dist_checkin_m") is not None else ""))
    if v["check_out"]:
        linhas.append(f"Check-out: {v['check_out']}" + (f" (a {v['dist_checkout_m']}m do endereço cadastrado do PDV)" if v.get("dist_checkout_m") is not None else ""))
    else:
        linhas.append("Sem check-out registrado.")
    if v.get("motivo_visita"):
        linhas.append(f"Motivo da visita: {v['motivo_visita']}")
    if v.get("tipo_justificativa"):
        linhas.append(f"Tipo: {v['tipo_justificativa']}")
    if v.get("observacao_visita"):
        linhas.append(f"Observação do promotor: {v['observacao_visita']}")
    if v["pesquisas"]:
        linhas.append("Pesquisas respondidas:")
        for p in v["pesquisas"]:
            linhas.append(f"- {p['pesquisa']} (assuntos: {p['assuntos']}, {p['qtd_itens']} itens respondidos)")
    else:
        linhas.append("Nenhuma pesquisa respondida.")
    if v["tarefas"]:
        linhas.append("Tarefas respondidas:")
        for t in v["tarefas"]:
            linhas.append(f"- {t['tarefa']} ({t['tipo_tarefa']}): pergunta \"{t['pergunta']}\" -> resposta \"{t['resposta']}\"")
    else:
        linhas.append("Nenhuma tarefa respondida.")
    linhas.append(f"{len(v['fotos'])} foto(s) anexada(s) no total.")
    return "\n".join(linhas)


def _carregar_cache_analises():
    if ANALISE_IA_CACHE_PATH.exists():
        try:
            return json.loads(ANALISE_IA_CACHE_PATH.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def _salvar_cache_analises(cache):
    tmp = ANALISE_IA_CACHE_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, ANALISE_IA_CACHE_PATH)


def _gerar_analises_ia(visitas):
    """Análise de IA de verdade (chamada à OpenAI) por visita, com cache
    permanente em disco — cada visita só é analisada UMA VEZ (visita_id como
    chave), nunca reprocessada em execuções seguintes. Só gera análise nova
    pra visitas dos últimos ANALISE_IA_DIAS dias (histórico desde jan/2026 não
    vale o custo), e limita ANALISE_IA_MAX_POR_RUN chamadas novas por execução
    — como o cron roda de hora em hora, um catch-up grande vai se espalhando
    ao longo de várias execuções em vez de estourar tempo/custo numa hora só."""
    cache = _carregar_cache_analises()
    from datetime import timedelta
    corte = datetime.now() - timedelta(days=ANALISE_IA_DIAS)
    pendentes = []
    for v in visitas:
        cache_hit = cache.get(v["visita_id"])
        if cache_hit:
            v["analise_ia"] = cache_hit["texto"]
            continue
        try:
            data_dt = datetime.strptime(v["data"], "%d/%m/%Y") if v["data"] else None
        except ValueError:
            data_dt = None
        if data_dt and data_dt < corte:
            continue
        pendentes.append(v)
    if not pendentes:
        return
    pendentes = pendentes[:ANALISE_IA_MAX_POR_RUN]
    print(f"Promotoria: gerando análise de IA pra {len(pendentes)} visita(s) nova(s)...")
    from openai import OpenAI
    client = OpenAI()
    for v in pendentes:
        try:
            resp = client.chat.completions.create(
                model=ANALISE_IA_MODEL,
                messages=[
                    {"role": "system", "content": _PROMPT_SISTEMA_ANALISE_VISITA},
                    {"role": "user", "content": _prompt_visita(v)},
                ],
                temperature=0.3,
            )
            texto = (resp.choices[0].message.content or "").strip()
        except Exception as e:
            print(f"  [AVISO] análise de IA falhou pra visita {v['visita_id']}: {str(e)[:150]}")
            continue
        if not texto:
            continue
        v["analise_ia"] = texto
        cache[v["visita_id"]] = {"texto": texto, "gerado_em": datetime.now().strftime("%d/%m/%Y %H:%M")}
        # Grava a cada chamada (não só no final) — um timeout/interrupção no
        # meio do lote não pode jogar fora chamadas à OpenAI já pagas.
        _salvar_cache_analises(cache)


def _gravar_payload(payload):
    tmp_path = OUTPUT_PATH.with_suffix(".js.tmp")
    with open(tmp_path, "w", encoding="utf-8") as f:
        f.write(f"const PROMOTORIA_DATA = {json.dumps(payload, ensure_ascii=False, indent=2)};\n")
    os.replace(tmp_path, OUTPUT_PATH)


def main():
    print("Promotoria: autenticando...")
    token = login()

    print("Promotoria: resolvendo campo de nome da cidade...")
    campo_cidade = resolver_campo_nome_cidade(token)

    print("Promotoria: buscando nomes das cidades...")
    cidade_por_id = buscar_todas_cidades(token, campo_cidade)

    print("Promotoria: buscando referências de pesquisa (assunto/pergunta/item avaliado)...")
    refs = buscar_referencias_pesquisa(token)

    print(f"Promotoria: buscando equipe do supervisor {NOME_SUPERVISOR} (código {CODIGO_SUPERVISOR})...")
    supervisor, equipe = montar_mapa_equipe(token, CODIGO_SUPERVISOR, NOME_SUPERVISOR)
    print(f"Promotoria: equipe encontrada — {len(equipe)} usuário(s): {[u['nome'] for u in equipe]}")

    hoje = datetime.now().strftime("%Y-%m-%d")
    periodo_atual = {
        "inicio": DATA_INICIO.strftime("%Y-%m-%d"),
        "fim": DATA_FIM.strftime("%Y-%m-%d") if DATA_FIM else None,
    }
    # Retoma de uma execução anterior do MESMO DIA (usuários já processados
    # ficam em 'usuarios_processados') em vez de refazer tudo do zero —
    # cada usuário tem uma consulta pesada (visitas + detalhe de pesquisa
    # aninhados), e uma execução para no meio (interrompida/matada) não pode
    # custar o trabalho já feito. Se mudar de dia ou de escopo (supervisor/
    # período), começa do zero mesmo.
    payload = None
    if OUTPUT_PATH.exists():
        try:
            texto_existente = OUTPUT_PATH.read_text(encoding="utf-8")
            m = re.search(r"const PROMOTORIA_DATA\s*=\s*(\{.*\});?\s*\Z", texto_existente.strip(), re.DOTALL)
            candidato = json.loads(m.group(1)) if m else None
            if (
                candidato
                and candidato.get("atualizado_em", "").startswith(datetime.now().strftime("%d/%m/%Y"))
                and candidato.get("supervisor", {}).get("codigo") == supervisor["codigo"]
                and candidato.get("periodo") == periodo_atual
            ):
                payload = candidato
                payload.setdefault("usuarios_processados", [])
                # Se já processou TODO MUNDO numa hora anterior, reseta pra
                # buscar de novo em vez de só retomar — sem isso,
                # atualizado_em ficava congelado no horário da 1ª execução
                # completa do dia, mesmo rodando de hora em hora depois via
                # main.py (pedido do usuário em 2026-08-31: "coloca
                # atualização a cada 1h"). O resume-por-interrupção continua
                # valendo dentro da MESMA hora (útil se essa execução for
                # interrompida no meio).
                _total_equipe = len([supervisor] + equipe)
                _hora_payload = candidato.get("atualizado_em", "")[-5:-3]
                _hora_agora = datetime.now().strftime("%H")
                if len(payload["usuarios_processados"]) >= _total_equipe and _hora_payload != _hora_agora:
                    print(f"Promotoria: já completo desde {candidato.get('atualizado_em')} — buscando de novo pra essa hora.")
                    payload = None
        except Exception:
            payload = None

    if payload is None:
        payload = {
            "atualizado_em": datetime.now().strftime("%d/%m/%Y %H:%M"),
            "supervisor": {"codigo": supervisor["codigo"], "nome": supervisor["nome"]},
            "periodo": periodo_atual,
            "pesquisas": [],
            "tarefas": [],
            "usuarios_processados": [],
        }
        _gravar_payload(payload)  # arquivo existe desde já, mesmo que algo falhe no meio
    else:
        print(f"Promotoria: retomando execução de hoje — {len(payload['usuarios_processados'])} usuário(s) já processado(s).")

    todos_usuarios = [supervisor] + equipe
    for i, membro in enumerate(todos_usuarios, 1):
        if membro["id"] in payload["usuarios_processados"]:
            continue
        print(f"Promotoria: [{i}/{len(todos_usuarios)}] buscando visitas de {membro['nome']} (id {membro['id']})...")
        try:
            dados_usuario = buscar_dados_usuario(token, membro["id"])
        except Exception as e:
            print(f"  [AVISO] falha ao buscar {membro['nome']}: {str(e)[:200]}")
            continue

        if dados_usuario is None:
            continue

        perfil = (dados_usuario.get("prowUsuarioPerfilByPerfilId") or {}).get("perfil", "")
        empresas = dados_usuario.get("prowEmpresaUsuariosByUsuarioId", {}).get("nodes", [])
        filial = "; ".join(e["prowEmpresaByEmpresaId"]["nome"] for e in empresas if e.get("prowEmpresaByEmpresaId"))

        visitas = dados_usuario.get("prowVisitasByUsuarioId", {}).get("nodes", [])
        visitas_periodo = [v for v in visitas if dentro_do_periodo(v["tsEntrada"])]
        visitas_periodo.sort(key=lambda v: parse_ts(v["tsEntrada"]) or datetime.min, reverse=True)

        linhas_pesquisa_base_usuario = []
        linhas_tarefa_usuario = []

        for visita in visitas_periodo:
            pdv = visita.get("prowPontoVendaByPontoVendaId") or {}
            enderecos = pdv.get("prowEnderecoPontoVendasByPontoVendaId", {}).get("nodes", [])
            cidade_id = None
            endereco = {}
            if enderecos:
                endereco = enderecos[0].get("prowEnderecoByEnderecoId") or {}
                cidade_id = endereco.get("cidadeId")

            motivo = visita.get("prowMotivoVisitaByMotivoVisitaId") or {}
            loc_checkin  = visita.get("prowLocalizacaoByLocalizacaoCheckinId") or {}
            loc_checkout = visita.get("prowLocalizacaoByLocalizacaoCheckoutId") or {}
            # Distância entre onde o check-in/checkout foi REGISTRADO (GPS do
            # aparelho) e o endereço cadastrado do PDV — dá pra IA um número
            # objetivo em vez de achismo pra sinalizar "check-in longe da
            # loja" (sugestão explorada via introspecção da API em 2026-08-31).
            dist_checkin_m  = _distancia_m(loc_checkin, endereco)
            dist_checkout_m = _distancia_m(loc_checkout, endereco)

            base = {
                "visita_id": str(visita["id"]),
                "cod_supervisor": supervisor["codigo"],
                "supervisor": supervisor["nome"],
                "cod_usuario": dados_usuario["codigo"],
                "usuario": dados_usuario["nome"],
                "perfil": perfil,
                "filial": filial,
                "cpf_cnpj_pdv": pdv.get("cpfcnpj", ""),
                "codigo_pdv": pdv.get("codigo", ""),
                "razao_social": pdv.get("nome", ""),
                "fantasia": pdv.get("fantasia", ""),
                "cidade": cidade_por_id.get(cidade_id, ""),
                "data": (parse_ts(visita["tsEntrada"]) or "").strftime("%d/%m/%Y") if visita["tsEntrada"] else "",
                # ISO já convertido pra horário local (BRT) — não a string
                # crua da API (essa vem em UTC sem sufixo, ver parse_ts()).
                "check_in": (parse_ts(visita["tsEntrada"]) or "").isoformat() if visita["tsEntrada"] else "",
                "check_out": (parse_ts(visita["tsSaida"]) or "").isoformat() if visita["tsSaida"] else "",
                "observacao_visita": visita.get("observacao") or "",
                "tipo_justificativa": visita.get("tipoJustificativa") or "",
                "motivo_visita": motivo.get("descricao") or "",
                "dist_checkin_m": dist_checkin_m,
                "dist_checkout_m": dist_checkout_m,
            }

            for vrp in visita.get("prowVisitaRespostaPesquisasByVisitaId", {}).get("nodes", []):
                resp = vrp.get("prowRespostaPesquisaByRespostaPesquisaId")
                if not resp:
                    continue
                linhas_pesquisa_base_usuario.append({
                    **base,
                    "resposta_pesquisa_id": resp["id"],
                    "pesquisa": (resp.get("prowPesquisaByPesquisaId") or {}).get("descricao", ""),
                })

            for vrt in visita.get("prowVisitaRespostaTarefasByVisitaId", {}).get("nodes", []):
                resp_tarefa = vrt.get("prowRespostaTarefaByRespostaTarefaId")
                if not resp_tarefa:
                    continue
                checklist = resp_tarefa.get("prowChecklistByTarefaId") or {}
                atividades = resp_tarefa.get("prowRespostaAtividadeTarefasByRespostaTarefaId", {}).get("nodes", [])
                if not atividades:
                    linhas_tarefa_usuario.append({
                        **base, "tarefa": checklist.get("descricao", ""), "tipo_tarefa": checklist.get("finalidade", ""),
                        "tipo_checklist": checklist.get("tipo", ""),
                        "pergunta": "", "resposta": "", "foto": "",
                    })
                    continue
                for atv in atividades:
                    item_checklist = atv.get("prowItemChecklistByAtividadeTarefaId") or {}
                    pergunta = (item_checklist.get("prowPerguntaByPerguntaId") or {}).get("descricao", "")
                    valores = atv.get("prowRespostaAtvdTarefaValorsByRespostaAtividadeTarefaId", {}).get("nodes", [])
                    resposta = "; ".join(v["valor"] or str(v["numero"] or "") for v in valores)
                    # Uma linha por ATIVIDADE, não por foto — cada atividade de
                    # check-in/check-out costuma ter 2 fotos, e uma linha por
                    # foto duplicava tarefa/pergunta/resposta idênticos na
                    # tabela (achado pelo usuário em 2026-08-31: "está
                    # duplicando as linhas"). Mesmo padrão que pesquisa já usa
                    # (qtd_fotos/fotos como array numa linha só, ver
                    # buscar_itens_resposta_pesquisa acima).
                    fotos_nodes = atv.get("prowFotoRespAtvTarefasByRespostaAtividadeTarefaId", {}).get("nodes", [])
                    fotos = []
                    for foto in fotos_nodes:
                        caminho = (foto.get("prowFotoByFotoMarcaId") or {}).get("caminho") or (
                            foto.get("prowFotoByFotoOriginalId") or {}
                        ).get("caminho", "")
                        if caminho:
                            fotos.append(caminho)
                    linhas_tarefa_usuario.append({
                        **base, "tarefa": checklist.get("descricao", ""), "tipo_tarefa": checklist.get("finalidade", ""),
                        # tipo do checklist (CHECK_IN/CHECK_OUT/etc) — quando é
                        # um desses dois, pergunta/resposta não são conteúdo
                        # de pesquisa de verdade, só um contador interno de
                        # foto anexada ("Checkin | Checkout" → "2") — achado
                        # pelo usuário em 2026-08-31 ("dá pra saber quais são
                        # as respostas?"), usado no front pra não mostrar esse
                        # número sem sentido como se fosse resposta real.
                        "tipo_checklist": checklist.get("tipo", ""),
                        "pergunta": pergunta, "resposta": resposta, "qtd_fotos": len(fotos), "fotos": fotos,
                    })

        linhas_pesquisa_final_usuario = []
        if linhas_pesquisa_base_usuario:
            resposta_ids = {l["resposta_pesquisa_id"] for l in linhas_pesquisa_base_usuario}
            _corte_detalhe = datetime.now() - timedelta(days=PESQUISA_DETALHE_DIAS)
            ids_recentes = {
                l["resposta_pesquisa_id"] for l in linhas_pesquisa_base_usuario
                if l["data"] and datetime.strptime(l["data"], "%d/%m/%Y") >= _corte_detalhe
            }
            try:
                itens, fotos_por_item, valores_por_item = buscar_itens_resposta_pesquisa(token, resposta_ids, ids_recentes)
            except Exception as e:
                print(f"  [AVISO] falha ao detalhar pesquisas de {membro['nome']}: {str(e)[:200]}")
                itens, fotos_por_item, valores_por_item = [], {}, {}

            itens_por_resposta = {}
            for item in itens:
                itens_por_resposta.setdefault(str(item["respostaPesquisaId"]), []).append(item)

            # Uma linha por resposta de pesquisa (não por item avaliado) —
            # expandir item a item (ex: "Execução no PDV" chega a ter ~30-40
            # itens por resposta) multiplicado pelos ~8 meses de histórico e
            # ~50 pessoas da equipe gerou um promotoria_data.js de mais de
            # 150MB, inviável de carregar direto na página (pedido do
            # usuário em 2026-08-20 pra resumir). "itens_detalhe" (pergunta +
            # resposta de cada item) reabre isso só pra pesquisa RECENTE
            # (pedido do usuário em 2026-08-31), usando valores_por_item —
            # vazio pra resposta fora do corte de PESQUISA_DETALHE_DIAS.
            for base_pesq in linhas_pesquisa_base_usuario:
                itens_da_resposta = itens_por_resposta.get(str(base_pesq["resposta_pesquisa_id"]), [])
                base_pesq.pop("resposta_pesquisa_id", None)
                assuntos = []
                fotos = []
                itens_detalhe = []
                for item in itens_da_resposta:
                    iapp = refs["itemAvaliPergPesq"].get(str(item["itemAvaliPergPesqId"]), {})
                    papq = refs["pergAssunPesq"].get(iapp.get("perguntaAssunPesqId"), {})
                    assunto_pesq = refs["assuntoPesquisa"].get(papq.get("assuntoPesquisaId"))
                    assunto = refs["assunto"].get(assunto_pesq, "")
                    if assunto and assunto not in assuntos:
                        assuntos.append(assunto)
                    fotos.extend(f for f in fotos_por_item.get(str(item["id"]), []) if f)
                    valores = valores_por_item.get(str(item["id"]))
                    if valores:
                        itens_detalhe.append({
                            "assunto": assunto,
                            "item_avaliado": refs["itemAvaliado"].get(iapp.get("itemAvaliadoId"), ""),
                            "pergunta": refs["pergunta"].get(papq.get("perguntaId"), ""),
                            "resposta": "; ".join(valores),
                        })
                linhas_pesquisa_final_usuario.append({
                    **base_pesq,
                    "assuntos": "; ".join(assuntos),
                    "qtd_itens": len(itens_da_resposta),
                    "qtd_fotos": len(fotos),
                    "fotos": fotos,
                    "itens_detalhe": itens_detalhe,
                })

        payload["pesquisas"].extend(linhas_pesquisa_final_usuario)
        payload["tarefas"].extend(linhas_tarefa_usuario)
        payload["usuarios_processados"].append(membro["id"])
        payload["atualizado_em"] = datetime.now().strftime("%d/%m/%Y %H:%M")
        _gravar_payload(payload)

        print(
            f"  {len(visitas_periodo)} visita(s) no período | "
            f"{len(linhas_pesquisa_final_usuario)} linha(s) de pesquisa | "
            f"{len(linhas_tarefa_usuario)} linha(s) de tarefa salvas"
        )

    print(f"OK promotoria_data.js — {len(payload['pesquisas'])} linhas de pesquisa, {len(payload['tarefas'])} linhas de tarefa")

    print("Promotoria: agregando visitas (timeline + IA)...")
    visitas = _montar_visitas(payload["pesquisas"], payload["tarefas"])
    _resolver_cidade_bairro_crc(visitas)
    _gerar_analises_ia(visitas)
    payload["visitas"] = visitas
    payload["atualizado_em"] = datetime.now().strftime("%d/%m/%Y %H:%M")
    _gravar_payload(payload)
    com_ia = sum(1 for v in visitas if v["analise_ia"])
    print(f"OK visitas agregadas — {len(visitas)} visita(s), {com_ia} com análise de IA")


if __name__ == "__main__":
    main()
