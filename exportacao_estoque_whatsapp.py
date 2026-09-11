"""
Gera estoque_whatsapp_data.js a partir da contagem de estoque que o time
posta manualmente no grupo de WhatsApp "Estoque RJ - Rigarr" — pedido do
usuário em 2026-09-10.

Evolution API, instância "estoque" (número dedicado, criado em 2026-09-10 só
pra entrar nesse grupo e ler as mensagens — NÃO é a instância "bees" usada
pros outros avisos do projeto, ver whatsapp_evolution.py). Só existe na
Evolution da VPS — esse script não roda de propósito fora dela (mesmo padrão
de exportacao_meta.py: cron próprio, fora do main.py).

Grupo: "Estoque RJ - Rigarr", id 120363021573739336@g.us (confirmado em
2026-09-10 — group/fetchAllGroups devolve corpo vazio em silêncio nessa
versão da API, 1.8.7; achado batendo chat/findChats + group/findGroupInfos
um a um).

Segundo id, 135777321263246@lid (confirmado em 2026-09-11): mensagens
NOVAS do mesmo grupo passaram a chegar com esse remoteJid em vez do
120363021573739336@g.us de sempre — grupo continua o mesmo (confirmado
pelo usuário, vendo a mensagem no próprio WhatsApp dentro do grupo "Estoque
RJ - Rigarr"), mas o @lid não bate com o JID do grupo nem está na lista de
participantes — sinal de migração de endereçamento do WhatsApp (LID) que
esse Baileys/Evolution 1.8.6 não resolveu corretamente pro grupo. Sem
histórico suficiente ainda pra saber se o @g.us antigo para de aparecer de
vez ou os dois convivem — aceita as duas chaves em GROUP_JIDS pra não
perder nenhuma das duas.

Não existe UMA mensagem "contagem completa": o time posta produto+quantidade
aos poucos, cobrindo marcas diferentes em mensagens diferentes ao longo do
dia (confirmado pelo usuário em 2026-09-10 — "atualizações parciais"),
misturado com conversa solta ("Bom dia", "podem confirmar?"). Por isso esse
script faz MERGE com o que já sabia (estoque_whatsapp_raw.json): cada
produto reconhecido guarda só a ÚLTIMA quantidade vista, pelo timestamp da
própria mensagem do WhatsApp — não o horário da execução do cron.

chat/findMessages com filtro "where.key.remoteJid" é aceito pela API (log do
container confirma que o corpo chega certo) mas o resultado IGNORA o filtro
e devolve mensagens de qualquer conversa — bug da v1.8.7 confirmado em
2026-09-10. O filtro por grupo é feito aqui do lado de fora: busca as
últimas MENSAGENS_POR_BUSCA mensagens de TODAS as conversas e filtra por
remoteJid no Python.

Casamento produto -> CODPROD por IA: nome digitado no grupo (às vezes com
erro de digitação, tipo "Woodforf") não bate exato com PCPRODUT.DESCRICAO —
pedido do usuário em 2026-09-10. Cacheia cada casamento em
estoque_whatsapp_matches.json (por texto normalizado) pra não gastar OpenAI
de novo no mesmo produto a cada rodada do cron; só manda pra IA o que for
texto novo. Catálogo usado como base é o mesmo de catalogo.py (produtos
vendidos pelo canal OFF TRADE/W.S nos últimos 18 meses).
"""
import json
import os
import re
import unicodedata
from datetime import datetime
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv()

import meta

RUNTIME = os.getenv("OFFTRADE_RUNTIME", "local")

EVOLUTION_BASE_URL = os.getenv("EVOLUTION_BASE_URL", "http://localhost:8083")
EVOLUTION_KEY = os.getenv("EVOLUTION_KEY", "")
INSTANCE = "estoque"
GROUP_JIDS = {"120363021573739336@g.us", "135777321263246@lid"}
MENSAGENS_POR_BUSCA = 300

HERE = Path(__file__).parent
OUT_JS = HERE / "estoque_whatsapp_data.js"
RAW_JSON = HERE / "estoque_whatsapp_raw.json"
MATCHES_JSON = HERE / "estoque_whatsapp_matches.json"

CATALOGO_QUERY = """
    SELECT DISTINCT P.CODPROD, P.DESCRICAO
    FROM CRC.PCPRODUT P
    WHERE P.CODPROD IN (
        SELECT DISTINCT M.CODPROD FROM CRC.PCMOV M
        JOIN CRC.PCUSUARI U ON M.CODUSUR = U.CODUSUR
        WHERE (U.NOME LIKE '%OFF TRADE%' OR U.NOME LIKE '%W.S%')
          AND M.DTMOV >= ADD_MONTHS(SYSDATE, -18)
    )
    ORDER BY P.DESCRICAO
"""


# ── Evolution API ────────────────────────────────────────────────────────

def _buscar_mensagens_grupo():
    url = f"{EVOLUTION_BASE_URL}/chat/findMessages/{INSTANCE}"
    headers = {"apikey": EVOLUTION_KEY, "Content-Type": "application/json"}
    resp = requests.post(url, json={"where": {}, "limit": MENSAGENS_POR_BUSCA}, headers=headers, timeout=30)
    resp.raise_for_status()
    todas = resp.json()
    do_grupo = [
        m for m in todas
        if (m.get("key") or {}).get("remoteJid") in GROUP_JIDS
    ]
    saida = []
    for m in do_grupo:
        msg = m.get("message") or {}
        texto = msg.get("conversation") or (msg.get("extendedTextMessage") or {}).get("text")
        if not texto:
            continue
        saida.append({"texto": texto, "ts": m.get("messageTimestamp") or 0})
    saida.sort(key=lambda x: x["ts"])
    return saida


# ── Parsing das linhas ("Produto - 123", "Produto= 123", "• Produto: 123") ──
# Formatos confirmados em mensagens reais do grupo em 2026-09-10: separador
# "-", "=" ou ":" (às vezes sem espaço nenhum), número com "." como milhar
# ("23.787"), sufixo opcional "und"/"unid", bullet líder "•" ou "-", e "❌"
# no fim da linha pra indicar zerado/indisponível (sem separador+número).
# "malas" confirmado em 2026-09-11 ("Moving hidro protein tangerina : 323
# malas") — mensagem real chegou (graças ao fix do @lid) mas ficou sem
# parsear porque o sufixo só aceitava und/unid; o produto zerava
# silenciosamente (nenhum erro, só não virava item).
_RE_BULLET = re.compile(r"^[•\-\*]\s*")
_RE_ITEM = re.compile(r"^(.*?)\s*[-=:]\s*(\d{1,3}(?:\.\d{3})*)\s*(?:und?\.?|unid\.?|malas?)?\s*$", re.IGNORECASE)
_RE_INDISPONIVEL = re.compile(r"^(.*?)\s*[❌❎✖]\s*$")


def _normalizar(texto):
    s = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode("ascii")
    s = re.sub(r"[^a-z0-9]+", " ", s.lower()).strip()
    return s


def _parse_linhas(texto):
    itens = []
    for linha_bruta in texto.split("\n"):
        linha = _RE_BULLET.sub("", linha_bruta.strip())
        if not linha:
            continue
        m = _RE_ITEM.match(linha)
        if m:
            produto = m.group(1).strip(" -=:")
            if not produto:
                continue
            qtd = int(m.group(2).replace(".", ""))
            itens.append((produto, qtd))
            continue
        m2 = _RE_INDISPONIVEL.match(linha)
        if m2:
            produto = m2.group(1).strip(" -=:")
            if produto:
                itens.append((produto, 0))
    return itens


# ── Estado acumulado (merge de mensagens parciais ao longo do tempo) ───────

def _carregar_json(caminho, default):
    if caminho.exists():
        try:
            with open(caminho, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return default


def _salvar_json(caminho, dados):
    tmp = caminho.with_suffix(caminho.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(dados, f, ensure_ascii=False, indent=2)
    os.replace(tmp, caminho)


def _atualizar_estado_raw(mensagens):
    estado = _carregar_json(RAW_JSON, {})
    for msg in mensagens:
        for produto, qtd in _parse_linhas(msg["texto"]):
            chave = _normalizar(produto)
            if not chave:
                continue
            atual = estado.get(chave)
            if atual is None or msg["ts"] >= atual.get("ts", 0):
                estado[chave] = {"raw_texto": produto, "quantidade": qtd, "ts": msg["ts"]}
    _salvar_json(RAW_JSON, estado)
    return estado


# ── Casamento com o catálogo (CODPROD) via IA ───────────────────────────────

def _buscar_catalogo():
    try:
        df = meta.carregar_dados(CATALOGO_QUERY, meta.engine, "PCPRODUT (off trade)")
    except Exception as e:
        print(f"[AVISO] catálogo indisponível ({str(e)[:150]}) — casamento por IA pulado nesta rodada")
        return None
    return [{"codprod": str(int(r["CODPROD"])), "descricao": r["DESCRICAO"]} for r in df.to_dict("records")]


def _casar_com_ia(pendentes, catalogo):
    from openai import OpenAI
    client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])

    catalogo_txt = "\n".join(f"{c['codprod']} | {c['descricao']}" for c in catalogo)
    produtos_txt = "\n".join(f"{i}. {p}" for i, p in enumerate(pendentes))

    # Prompt reforçado em 2026-09-10: a 1ª versão (mais permissiva) inventava
    # correspondência errada em vez de null quando o produto não existia de
    # verdade no catálogo — achado testando contra dados reais do grupo:
    # "Smirnoff 1,750ml" casou com "VODKA STOLI 1L" (marca diferente!),
    # "VINHO HORIZONTE TINTO" casou com "VINHO HORIZONTE BRANCO" (cor
    # diferente), "SAUV BLANC" casou com "CHARDONNAY" (varietal diferente).
    # Casar errado é PIOR que não casar (mostra estoque do produto errado em
    # silêncio) — por isso a lista explícita do que NUNCA pode divergir.
    prompt = f"""Você recebe uma lista de produtos digitados à mão num grupo de WhatsApp de
contagem de estoque (podem ter erro de digitação, abreviação, nome informal)
e o catálogo oficial de produtos (CODPROD | DESCRICAO).

Pra cada produto da lista, ache o CODPROD do catálogo que é EXATAMENTE o
mesmo produto físico. Isto NUNCA pode divergir entre o texto e a descrição
escolhida — se qualquer um desses for diferente ou não estiver claro, é
null, mesmo que seja o item mais parecido disponível no catálogo:
- Marca/fabricante (ex: Smirnoff não é o mesmo produto que Stoli, mesmo
  sendo as duas vodka)
- Cor/estilo/varietal (ex: vinho Tinto não é Branco; Sauvignon Blanc não é
  Chardonnay; Colheita não é Tawny nem Ruby)
- Volume/tamanho/embalagem (ex: 750ml não é 1L; caixa com 12 não é caixa
  com 24; 275ml não é 270ml)

Não tente "salvar" um produto que não existe no catálogo escolhendo o mais
parecido — devolva null. É preferível ficar sem casar um produto real do
que casar com um produto diferente do que a pessoa contou.

CATÁLOGO:
{catalogo_txt}

PRODUTOS DO WHATSAPP (índice: texto):
{produtos_txt}

Responda em JSON: {{"matches": [{{"indice": 0, "codprod": "12345"}}, {{"indice": 1, "codprod": null}}, ...]}}
Um item pra cada índice da lista, na mesma ordem."""

    # Retry simples pra 429 — visto na prática (2026-09-10) com lotes
    # seguidos batendo limite de tokens/min da organização; uma pausa curta
    # já resolve, sem precisar esperar a próxima rodada do cron inteira.
    import time
    for tentativa in range(3):
        try:
            resp = client.chat.completions.create(
                model=os.getenv("OPENAI_TEXT_MODEL_ESTOQUE", "gpt-4o"),
                messages=[{"role": "user", "content": prompt}],
                response_format={"type": "json_object"},
            )
            break
        except Exception as e:
            if "429" in str(e) and tentativa < 2:
                time.sleep(20)
                continue
            raise
    resultado = json.loads(resp.choices[0].message.content)
    por_codprod = {c["codprod"]: c["descricao"] for c in catalogo}

    saida = {}
    for item in resultado.get("matches", []):
        idx = item.get("indice")
        if idx is None or not (0 <= idx < len(pendentes)):
            continue
        codprod = item.get("codprod")
        if codprod and str(codprod) in por_codprod:
            saida[pendentes[idx]] = {"codprod": str(codprod), "descricao": por_codprod[str(codprod)]}
        else:
            saida[pendentes[idx]] = {"codprod": None, "descricao": None}
    return saida


# Lote pequeno de propósito: testado em 2026-09-10 com os 179 produtos reais
# do grupo de uma vez só (catálogo de 1863 linhas) e a IA embaralhava índice
# — "4 PACK RED BULL NECTARINA" casou com o produto de MAÇÃ, "RED BULL
# MELÃO" casou com uma CACHAÇA — bug de atenção/tracking do modelo com
# prompt grande, não achou-se com uma reformulação de prompt. Em lotes de
# ~25 pendentes por vez (mesmo catálogo completo em cada lote — só o que
# muda é a lista de produtos pendentes), os mesmos casos casaram certo.
_TAMANHO_LOTE_IA = 15


def _atualizar_matches(estado_raw):
    matches = _carregar_json(MATCHES_JSON, {})
    pendentes_chaves = [chave for chave in estado_raw if chave not in matches]
    if not pendentes_chaves:
        return matches

    catalogo = _buscar_catalogo()
    if catalogo is None:
        return matches

    for inicio in range(0, len(pendentes_chaves), _TAMANHO_LOTE_IA):
        lote_chaves = pendentes_chaves[inicio:inicio + _TAMANHO_LOTE_IA]
        lote_textos = [estado_raw[chave]["raw_texto"] for chave in lote_chaves]
        try:
            novos = _casar_com_ia(lote_textos, catalogo)
        except Exception as e:
            print(f"[AVISO] casamento por IA falhou nesse lote ({str(e)[:150]}) — pula, tenta de novo na próxima rodada")
            continue
        for chave, texto in zip(lote_chaves, lote_textos):
            if texto in novos:
                matches[chave] = novos[texto]
        _salvar_json(MATCHES_JSON, matches)
    return matches


# ── Escrita do _data.js ─────────────────────────────────────────────────────

def _montar_itens(estado_raw, matches):
    itens = []
    for chave, info in estado_raw.items():
        match = matches.get(chave) or {}
        itens.append({
            "codprod": match.get("codprod"),
            "descricao_oficial": match.get("descricao"),
            "raw_texto": info["raw_texto"],
            "quantidade": info["quantidade"],
            "atualizado_em": datetime.fromtimestamp(info["ts"]).strftime("%d/%m/%Y %H:%M") if info["ts"] else "",
        })
    itens.sort(key=lambda p: (p["descricao_oficial"] or p["raw_texto"]).lower())
    return itens


def main():
    if not EVOLUTION_KEY:
        print("[AVISO] EVOLUTION_KEY não configurado — pulando.")
        return

    try:
        mensagens = _buscar_mensagens_grupo()
    except Exception as e:
        print(f"[AVISO] Evolution API indisponível ({str(e)[:150]}) — pulando.")
        return

    estado_raw = _atualizar_estado_raw(mensagens)
    matches = _atualizar_matches(estado_raw)
    itens = _montar_itens(estado_raw, matches)

    payload = {
        "atualizado_em": datetime.now().strftime("%d/%m/%Y %H:%M"),
        "fontes_indisponiveis": [],
        "itens": itens,
    }

    tmp = OUT_JS.with_suffix(".js.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        f.write("const ESTOQUE_WHATSAPP_DATA = ")
        json.dump(payload, f, ensure_ascii=False)
        f.write(";\n")
    os.replace(tmp, OUT_JS)
    print(f"OK - {len(itens)} produto(s) ({sum(1 for i in itens if i['codprod'])} casados com o catálogo) -> {OUT_JS}")

    import subprocess
    repo_dir = str(HERE)
    try:
        subprocess.run(["git", "-C", repo_dir, "add", "estoque_whatsapp_data.js"], check=True)
        subprocess.run(["git", "-C", repo_dir, "commit", "-m",
                        f"Atualiza estoque_whatsapp_data.js - {datetime.now().strftime('%d/%m/%Y %H:%M')}"])
        subprocess.run(["git", "-C", repo_dir, "push", "origin", "master"], check=True)
        print("OK estoque_whatsapp_data.js enviado ao GitHub Pages.")
    except subprocess.CalledProcessError:
        print("[AVISO] git push falhou — ignorado, script continua.")

    _publicar_static()


# ── Publica direto em /opt/offtrade-static (site) ─────────────────────────
# Roda só na VPS (cron próprio) — mesmo padrão de
# exportacao_pedidos_bloqueados.py::_publicar_static.
def _publicar_static():
    if RUNTIME != "vps":
        return
    import shutil
    destino = "/opt/offtrade-static"
    if not OUT_JS.exists():
        return
    tmp = os.path.join(destino, ".estoque_whatsapp_data.js.tmp_publish")
    shutil.copy(OUT_JS, tmp)
    os.replace(tmp, os.path.join(destino, "estoque_whatsapp_data.js"))
    print(f"OK - estoque_whatsapp_data.js copiado para {destino}")


if __name__ == "__main__":
    main()
