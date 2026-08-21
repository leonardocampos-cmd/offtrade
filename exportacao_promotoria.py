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
from datetime import datetime
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


def parse_ts(valor):
    if not valor:
        return None
    return datetime.fromisoformat(valor.replace("Z", "+00:00")).replace(tzinfo=None)


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
            prowPontoVendaByPontoVendaId {{
              nome
              fantasia
              codigo
              cpfcnpj
              prowEnderecoPontoVendasByPontoVendaId {{
                nodes {{ prowEnderecoByEnderecoId {{ cidadeId }} }}
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


def buscar_itens_resposta_pesquisa(token, resposta_pesquisa_ids):
    """Itens/fotos de resposta de pesquisa, filtrados pelos ids de resposta
    informados (tipicamente, os de um único usuário). Só busca o necessário
    pro resumo (contagem de itens/fotos + assuntos distintos) — não busca
    mais os valores de resposta item a item (allProwRespostaPesqItemValors),
    que só faziam sentido quando a página explodia uma linha por item (ver
    comentário em main() — resumido pra 1 linha por resposta em 2026-08-20)."""
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

    return itens, fotos_por_item


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
            if enderecos:
                endereco = enderecos[0].get("prowEnderecoByEnderecoId") or {}
                cidade_id = endereco.get("cidadeId")

            base = {
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
                "check_in": visita["tsEntrada"] or "",
                "check_out": visita["tsSaida"] or "",
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
                        "pergunta": "", "resposta": "", "foto": "",
                    })
                    continue
                for atv in atividades:
                    item_checklist = atv.get("prowItemChecklistByAtividadeTarefaId") or {}
                    pergunta = (item_checklist.get("prowPerguntaByPerguntaId") or {}).get("descricao", "")
                    valores = atv.get("prowRespostaAtvdTarefaValorsByRespostaAtividadeTarefaId", {}).get("nodes", [])
                    resposta = "; ".join(v["valor"] or str(v["numero"] or "") for v in valores)
                    fotos = atv.get("prowFotoRespAtvTarefasByRespostaAtividadeTarefaId", {}).get("nodes", [])
                    if fotos:
                        for foto in fotos:
                            caminho = (foto.get("prowFotoByFotoMarcaId") or {}).get("caminho") or (
                                foto.get("prowFotoByFotoOriginalId") or {}
                            ).get("caminho", "")
                            linhas_tarefa_usuario.append({
                                **base, "tarefa": checklist.get("descricao", ""), "tipo_tarefa": checklist.get("finalidade", ""),
                                "pergunta": pergunta, "resposta": resposta, "foto": caminho,
                            })
                    else:
                        linhas_tarefa_usuario.append({
                            **base, "tarefa": checklist.get("descricao", ""), "tipo_tarefa": checklist.get("finalidade", ""),
                            "pergunta": pergunta, "resposta": resposta, "foto": "",
                        })

        linhas_pesquisa_final_usuario = []
        if linhas_pesquisa_base_usuario:
            resposta_ids = {l["resposta_pesquisa_id"] for l in linhas_pesquisa_base_usuario}
            try:
                itens, fotos_por_item = buscar_itens_resposta_pesquisa(token, resposta_ids)
            except Exception as e:
                print(f"  [AVISO] falha ao detalhar pesquisas de {membro['nome']}: {str(e)[:200]}")
                itens, fotos_por_item = [], {}

            itens_por_resposta = {}
            for item in itens:
                itens_por_resposta.setdefault(str(item["respostaPesquisaId"]), []).append(item)

            # Uma linha por resposta de pesquisa (não por item avaliado) —
            # expandir item a item (ex: "Execução no PDV" chega a ter ~30-40
            # itens por resposta) multiplicado pelos ~8 meses de histórico e
            # ~50 pessoas da equipe gerou um promotoria_data.js de mais de
            # 150MB, inviável de carregar direto na página (pedido do
            # usuário em 2026-08-20 pra resumir). O detalhe item a item seria
            # necessário pra reabrir isso no futuro (ex: consulta pontual por
            # resposta_pesquisa_id), mas não é gerado aqui.
            for base_pesq in linhas_pesquisa_base_usuario:
                itens_da_resposta = itens_por_resposta.get(str(base_pesq["resposta_pesquisa_id"]), [])
                base_pesq.pop("resposta_pesquisa_id", None)
                assuntos = []
                fotos = []
                for item in itens_da_resposta:
                    iapp = refs["itemAvaliPergPesq"].get(str(item["itemAvaliPergPesqId"]), {})
                    papq = refs["pergAssunPesq"].get(iapp.get("perguntaAssunPesqId"), {})
                    assunto_pesq = refs["assuntoPesquisa"].get(papq.get("assuntoPesquisaId"))
                    assunto = refs["assunto"].get(assunto_pesq, "")
                    if assunto and assunto not in assuntos:
                        assuntos.append(assunto)
                    fotos.extend(f for f in fotos_por_item.get(str(item["id"]), []) if f)
                linhas_pesquisa_final_usuario.append({
                    **base_pesq,
                    "assuntos": "; ".join(assuntos),
                    "qtd_itens": len(itens_da_resposta),
                    "qtd_fotos": len(fotos),
                    "fotos": fotos,
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


if __name__ == "__main__":
    main()
