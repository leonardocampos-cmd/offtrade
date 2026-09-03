"""
Bot: lê e-mails de PEDIDO/BONIFICAÇÃO da CRC filial 4 no Gmail (assunto
contém "PEDIDO" ou "BONIFICAÇÃO"), extrai cliente + itens pedidos (tabela
HTML colada de Excel, ou imagem colada no corpo — via visão da OpenAI) e
cruza com o que já foi faturado no Oracle (crc.PBI_PCPEDI, por CODCLI +
CODPROD + RCA + NF emitida na data do e-mail ou depois — ver
_faturado_do_item) pra gerar o comparativo pendente/parcial/faturado.

Esses pedidos por e-mail ainda não entram no Winthor no momento em que são
feitos — só depois que alguém fatura manualmente. O comparativo existe pra
saber o que ainda falta faturar da filial 4 (CRC).

Fluxo:
  1. Autentica Gmail (token_gmail.json gerado por gmail_setup.py)
  2. Busca emails novos (não vistos em pedidos_email_cache.json)
  3. Extrai blocos de pedido/bonificação (tabela HTML ou imagem via OpenAI)
  4. Se achou pelo menos 1 bloco CRC-4, extrai TAMBÉM do texto livre do corpo
     (fora de tabela/imagem) uma eventual data de agendamento de entrega e
     observações importantes pra logística (embalagem, horário de
     recebimento, taxas etc — pedido do usuário em 2026-09-02, ver
     _extrair_agendamento_email) — isso é diferente do campo OBS de cada
     bloco (que vem do FORMULÁRIO/imagem, não do corpo do e-mail em volta)
  5. Acumula em pedidos_email_cache.json (fonte de verdade, cresce com o tempo)
  6. Cruza cod_cliente+cod_prod de todos os blocos acumulados com o Oracle
  7. Faz merge no agendamento_data.js (aba "comparativo"), sem sobrescrever
     a aba "agendamentos" gerada por exportacao_agendamento.py
"""
import base64
import json
import os
import re
import socket
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from dotenv import load_dotenv
from bs4 import BeautifulSoup
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build

# Sem isso, uma conexão https com o Google (Gmail) ou OpenAI pode ficar
# pendurada num read() que nunca retorna — mesmo bug já visto e corrigido em
# baixar_planilhas_drive.py (travou o pipeline da VPS por horas em 2026-07-20).
socket.setdefaulttimeout(60)

BASE = Path(__file__).parent
load_dotenv(BASE / ".env")
TOKEN_GMAIL = BASE / "token_gmail.json"
CACHE_PATH  = BASE / "pedidos_email_cache.json"
DATA_PATH   = BASE / "agendamento_data.js"
SCOPES      = ["https://www.googleapis.com/auth/gmail.modify"]
# Antes filtrava por assunto (subject:(PEDIDO OR BONIFICAÇÃO OR BONIFICACAO))
# — perdia qualquer e-mail sem essa palavra no assunto, inclusive assunto
# EM BRANCO (caso real confirmado em 2026-09-01: pedido do cliente 96005/
# MULTI GUARANI de 21/08, de Angelo Suzart pra Danielle + OFF TRADE Rigarr,
# assunto vazio — nunca processado, nunca faturado, ninguém percebeu por
# 11 dias). Filtra por DESTINATÁRIO em vez de assunto — pega todo e-mail
# endereçado à caixa de pedidos, não importa o assunto. O filtro de
# SISTEMAS_OK abaixo (CRC4/CRC04) continua sendo a proteção contra
# processar e-mail sem pedido de verdade.
QUERY       = 'to:offtrade@rigarr.com.br OR cc:offtrade@rigarr.com.br'
MAX_RESULTS = 50

# Variações vistas nos assuntos/formulários: "CRC - 04", "CRC-4", "CRC4"...
SISTEMAS_OK    = {"CRC4", "CRC04"}
VISION_MODEL   = os.getenv("OPENAI_VISION_MODEL", "gpt-4o-mini")


def _norm_sistema(s: str) -> str:
    return re.sub(r'[^A-Z0-9]', '', str(s or '').upper())


def _to_bool(v) -> bool:
    """A OpenAI vision (response_format=json_object, sem schema estrito) às
    vezes devolve booleanos como string ('false'/'true') em vez de JSON real
    — bool('false') é True em Python (string não-vazia), o que inverteu
    'bonificacao' de 66 dos 89 pedidos no comparativo (bug confirmado em
    2026-07-23). Trata string explicitamente antes de cair no bool() padrão."""
    if isinstance(v, str):
        return v.strip().lower() == 'true'
    return bool(v)


# ── Autenticação Gmail (mesmo padrão de alerta_logistica_rj.py) ───────────────

def _get_service():
    if not TOKEN_GMAIL.exists():
        raise FileNotFoundError(
            "token_gmail.json não encontrado. Execute gmail_setup.py uma vez "
            "para autorizar o acesso ao Gmail."
        )
    creds = Credentials.from_authorized_user_file(str(TOKEN_GMAIL), SCOPES)
    if not creds.valid:
        if creds.expired and creds.refresh_token:
            # oauth2.googleapis.com deu SSLEOFError repetidamente (blip de
            # rede/VPN transitório) em TODAS as execuções horárias de
            # 15 a 19/08/2026 — sem retry, um único blip derrubava a etapa
            # inteira, e como não há fallback, agendamento.html ficou 5 dias
            # sem nenhum e-mail novo (confirmado no offtrade.log). Mesmo
            # padrão de retry de meta.py::carregar_dados (3 tentativas).
            _ultimo_erro = None
            for _tentativa in range(1, 4):
                try:
                    creds.refresh(Request())
                    break
                except Exception as _ex:
                    _ultimo_erro = _ex
                    print(f"[AVISO] refresh do token Gmail falhou (tentativa {_tentativa}/3): {str(_ex)[:120]}")
                    if _tentativa < 3:
                        time.sleep(10)
            else:
                raise _ultimo_erro
            TOKEN_GMAIL.write_text(creds.to_json(), encoding="utf-8")
        else:
            raise RuntimeError(
                "Token Gmail inválido ou sem refresh_token. "
                "Rode gmail_setup.py novamente para reautorizar."
            )
    return build("gmail", "v1", credentials=creds)


def _decode_b64(data: str) -> str:
    return base64.urlsafe_b64decode(data + "==").decode("utf-8", errors="replace")


def _extract_parts(payload: dict, out: dict):
    """Preenche out['html_parts'] (texto html decodificado) e
    out['image_attachment_ids'] (id pra buscar via attachments().get)."""
    mime = payload.get("mimeType", "")
    body = payload.get("body", {})
    if mime == "text/html" and body.get("data"):
        out["html_parts"].append(_decode_b64(body["data"]))
    elif mime.startswith("image/") and body.get("attachmentId"):
        out["image_attachment_ids"].append(body["attachmentId"])
    for part in payload.get("parts", []) or []:
        _extract_parts(part, out)


# ── Parsing: tabela HTML colada (copy-paste de Excel) ─────────────────────────

_CAMPO_LABELS = {
    'COD CLIENTE':      'cod_cliente',
    'RAZÃO SOCIAL':     'razao_social',
    'FANTASIA':         'fantasia',
    'CNPJ':             'cnpj',
    'ENDEREÇO/BAIRRO':  'endereco',
    'PRAÇA':            'praca',
    'FORMA DE PG':      'forma_pg',
    'RCA':              'rca',
    'TIPO DE SEGMENTO':  'tipo_segmento',
    'OBS':              'obs',
    'PRAZO':            'prazo',
}


def _parse_valor(txt: str) -> float:
    limpo = re.sub(r'[^\d,.-]', '', txt or '').replace('.', '').replace(',', '.')
    try:
        return float(limpo)
    except ValueError:
        return 0.0


_ITEM_COLS = ['COD PROD', 'DESCRIÇÃO', 'QT', 'PREÇO', 'TOTAL']


def _parse_html_pedido(html: str) -> list:
    """Parseia 1+ blocos de formulário (SELECIONE O SISTEMA / COD CLIENTE /
    ... tabela COD PROD) colados como tabela HTML de Excel no corpo do email.
    A tabela tem uma coluna decorativa vazia à esquerda (borda laranja do
    template) — por isso os índices das colunas de item são achados pelo
    cabeçalho (COD PROD/DESCRIÇÃO/...) em vez de posição fixa."""
    soup = BeautifulSoup(html, "html.parser")
    blocos = []
    bloco_atual = None
    aguardando_itens = False
    col_idx = {}

    def _cell(celulas, idx):
        return celulas[idx] if 0 <= idx < len(celulas) else ''

    for tr in soup.find_all("tr"):
        celulas = [re.sub(r'\s+', ' ', c.get_text(" ", strip=True)).strip() for c in tr.find_all(["td", "th"])]
        if not celulas:
            continue
        celulas_up = [c.upper() for c in celulas]
        texto_linha = " ".join(celulas_up)

        # O template tem pelo menos 3 variantes de cabeçalho vistas na prática:
        # "SELECIONE O SISTEMA: CRC - 04" (pedido normal), "BONIFICAÇÃO CRC - 04"
        # (só aparece nessa forma quando é bonificação — o campo "BONIFICAÇÃO ?"
        # abaixo geralmente fica em branco no pedido normal, não dá pra confiar
        # nele sozinho) e "PEDIDO CRC - 04" (título simples, sem "SELECIONE").
        if "SELECIONE O SISTEMA" in texto_linha:
            sistema = celulas[-1] if celulas else ""
            bloco_atual = {'sistema': sistema, 'bonificacao': False, 'itens': []}
            blocos.append(bloco_atual)
            aguardando_itens = False
            continue

        if re.search(r'BONIFICA[ÇC][ÃA]O\s+CRC', texto_linha):
            sistema = re.sub(r'^.*BONIFICA[ÇC][ÃA]O\s+', '', celulas[0] if celulas else '', flags=re.IGNORECASE)
            bloco_atual = {'sistema': sistema, 'bonificacao': True, 'itens': []}
            blocos.append(bloco_atual)
            aguardando_itens = False
            continue

        if re.search(r'\bPEDIDO\s+CRC\b', texto_linha) and len(celulas) <= 2:
            sistema = re.sub(r'^.*\bPEDIDO\s+', '', celulas[0] if celulas else '', flags=re.IGNORECASE)
            bloco_atual = {'sistema': sistema, 'bonificacao': False, 'itens': []}
            blocos.append(bloco_atual)
            aguardando_itens = False
            continue

        if bloco_atual is None:
            continue

        if "BONIFICAÇÃO ?" in texto_linha and "SIM" in celulas_up:
            bloco_atual['bonificacao'] = True

        for label, chave in _CAMPO_LABELS.items():
            if label in celulas_up:
                idx = celulas_up.index(label)
                if idx + 1 < len(celulas) and celulas[idx + 1]:
                    bloco_atual[chave] = celulas[idx + 1]

        if not aguardando_itens and "COD PROD" in celulas_up and "DESCRIÇÃO" in texto_linha:
            aguardando_itens = True
            col_idx = {c: celulas_up.index(c) for c in _ITEM_COLS if c in celulas_up}
            continue

        if aguardando_itens:
            cod = _cell(celulas, col_idx.get('COD PROD', 1)).strip()
            if not re.match(r'^\d+$', cod):
                aguardando_itens = False
                continue
            qt_txt = re.sub(r'\D', '', _cell(celulas, col_idx.get('QT', 3)))
            bloco_atual['itens'].append({
                'cod_prod':  cod,
                'descricao': _cell(celulas, col_idx.get('DESCRIÇÃO', 2)),
                'qt':        int(qt_txt) if qt_txt else 0,
                'preco':     _parse_valor(_cell(celulas, col_idx.get('PREÇO', 4))),
                'total':     _parse_valor(_cell(celulas, col_idx.get('TOTAL', 5))),
            })

    return [b for b in blocos if b.get('itens')]


# ── Parsing: imagem colada no corpo (via OpenAI vision) ───────────────────────

_PROMPT_IMAGEM = """Esta imagem é um formulário de pedido de bebidas de um RCA de vendas.
Extraia os campos em JSON, respondendo APENAS o JSON (sem texto extra), no formato:

{
  "sistema": "o código do sistema/filial, ex: 'CRC - 04'",
  "cod_cliente": "número em COD CLIENTE",
  "razao_social": "...", "fantasia": "...", "cnpj": "...",
  "praca": "...", "forma_pg": "...", "rca": "código e nome em RCA",
  "bonificacao": true,
  "tipo_segmento": "...", "obs": "texto do campo OBS", "prazo": "texto do campo PRAZO",
  "itens": [{"cod_prod": "...", "descricao": "...", "qt": numero, "preco": numero, "total": numero}]
}

Regras importantes:
- "sistema": o cabeçalho/título do formulário varia ('SELECIONE O SISTEMA: CRC - 04',
  'BONIFICAÇÃO CRC - 04', 'PEDIDO CRC - 04', etc) — em qualquer caso, extraia só a parte
  'CRC - 04' (empresa + número), ignorando as palavras ao redor (SELECIONE O SISTEMA /
  BONIFICAÇÃO / PEDIDO não fazem parte do código).
- "bonificacao": deve ser o booleano JSON `true` ou `false` (nunca string, nunca texto
  explicativo). É `true` SOMENTE se o título/cabeçalho do formulário contiver a palavra
  'BONIFICAÇÃO' (ex: 'BONIFICAÇÃO CRC - XX'), OU se o campo 'BONIFICAÇÃO ?' contiver
  explicitamente o texto 'Sim'. Se o cabeçalho for 'SELECIONE O SISTEMA' ou 'PEDIDO CRC'
  e o campo 'BONIFICAÇÃO ?' estiver em branco/vazio, é `false` (pedido normal) — campo em
  branco NUNCA deve virar true.
- Ignore linhas da tabela de itens vazias (sem código ou só "-")."""


def _parse_imagem_pedido(png_bytes: bytes) -> dict | None:
    from openai import OpenAI
    client = OpenAI()
    b64 = base64.b64encode(png_bytes).decode()
    resp = client.chat.completions.create(
        model=VISION_MODEL,
        messages=[{
            "role": "user",
            "content": [
                {"type": "text", "text": _PROMPT_IMAGEM},
                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}},
            ],
        }],
        response_format={"type": "json_object"},
        temperature=0,
    )
    try:
        return json.loads(resp.choices[0].message.content)
    except (json.JSONDecodeError, IndexError, AttributeError):
        return None


# ── Parsing: texto livre do corpo (data de agendamento + observações) ─────────
# Pedido do usuário em 2026-09-02: o texto do PRÓPRIO e-mail (fora de
# tabela/imagem de pedido, que já tem parser dedicado acima) às vezes pede uma
# data de entrega ("Entrega agendada para amanhã dia 03/09/2026") e traz
# observações importantes pra logística (embalagem, horário de recebimento,
# taxa de descarga por pallet etc — ex real: e-mail "PEDIDO CRC 4 - Barra
# Oeste" de 02/09/2026). Isso nunca foi capturado: o parser de imagem só lê o
# formulário colado (campo OBS dele, se tiver), não o corpo do e-mail em volta.

_PROMPT_AGENDAMENTO_EMAIL = """O texto abaixo é o corpo de um e-mail que pede faturamento e/ou agendamento de entrega de pedido(s) da CRC filial 4. Tabelas/imagens de produtos já foram removidas — isso é só o texto livre ao redor. Extraia em JSON, respondendo APENAS o JSON:

{{
  "data_agendamento": "data de entrega solicitada, formato DD/MM/AAAA, ou \\"\\" se o texto não pedir uma data específica",
  "observacoes": "instruções/observações importantes pra logística (embalagem, horário de recebimento, taxas de descarga etc), texto corrido resumido mas completo, ou \\"\\" se não houver nada além de saudação/despedida"
}}

Regras:
- Se o texto disser "amanhã", "hoje" etc. em vez de uma data, calcule a data real a partir da data de envio informada abaixo.
- Não invente informação que não está no texto.

Data de envio do e-mail: {data_email}

Texto do e-mail:
\"\"\"
{corpo}
\"\"\""""


def _texto_email(html_parts: list) -> str:
    """Texto legível (sem tags) de todas as partes HTML do e-mail, concatenadas
    — é sobre ISSO que _extrair_agendamento_email procura data/observações,
    não sobre o formulário/imagem de pedido (parser à parte)."""
    textos = []
    for html in html_parts:
        textos.append(BeautifulSoup(html, "html.parser").get_text("\n", strip=True))
    return "\n".join(textos).strip()


def _extrair_agendamento_email(corpo: str, data_email: str) -> dict:
    """Só chama a OpenAI se já tem pelo menos 1 bloco CRC-4 reconhecido no
    e-mail (quem chama decide isso) — não vale gastar em e-mail irrelevante.
    Best-effort: qualquer falha (API fora, JSON inválido) devolve campos
    vazios em vez de derrubar o processamento do e-mail inteiro."""
    if not corpo or len(corpo) < 15:
        return {"data_agendamento": "", "observacoes": ""}
    from openai import OpenAI
    client = OpenAI()
    try:
        resp = client.chat.completions.create(
            model=os.getenv("OPENAI_TEXT_MODEL", "gpt-4o-mini"),
            messages=[{
                "role": "user",
                "content": _PROMPT_AGENDAMENTO_EMAIL.format(data_email=data_email, corpo=corpo[:4000]),
            }],
            response_format={"type": "json_object"},
            temperature=0,
        )
        d = json.loads(resp.choices[0].message.content)
        return {
            "data_agendamento": str(d.get("data_agendamento") or "").strip(),
            "observacoes": str(d.get("observacoes") or "").strip(),
        }
    except Exception as e:
        print(f"  [AVISO] extração de agendamento/observações do corpo do e-mail falhou: {str(e)[:120]}")
        return {"data_agendamento": "", "observacoes": ""}


# ── Comparativo com Oracle (crc.PBI_PCPEDI) ───────────────────────────────────

def _nfs_agendamento() -> set:
    """NFs presentes na aba 'Planilha de Agendamento' (gerada por
    exportacao_agendamento.py no mesmo agendamento_data.js) — usado pra
    marcar se a NF que faturou um item do comparativo já está agendada
    pra entrega ou não. Uma célula de NF pode ter mais de um número
    (ex: '56218 e 56216'), por isso extrai todos os dígitos separadamente."""
    if not DATA_PATH.exists():
        return set()
    raw = DATA_PATH.read_text(encoding='utf-8')
    m = re.search(r'=\s*(\{.*\});\s*$', raw.strip(), re.DOTALL)
    if not m:
        return set()
    try:
        existing = json.loads(m.group(1))
    except json.JSONDecodeError:
        return set()
    nfs = set()
    for vendedor in existing.get('agendamentos', []):
        for item in vendedor.get('itens', []):
            nfs.update(re.findall(r'\d+', str(item.get('nf', ''))))
    return nfs


_RE_PREFIXO_RESPOSTA = re.compile(r'^\s*(re|res|fwd|fw|enc)\s*:\s*', re.IGNORECASE)


def _assunto_normalizado(subject: str) -> str:
    """Remove prefixos de resposta/encaminhamento (Re:, Fwd:, Res:, "Re:
    Fwd:" etc, repetido até não sobrar nenhum) — usado pra agrupar
    mensagens da mesma thread mesmo sem threadId do Gmail."""
    s = (subject or '').strip()
    prev = None
    while prev != s:
        prev = s
        s = _RE_PREFIXO_RESPOSTA.sub('', s).strip()
    return s.upper()


def _assinatura_bloco(bloco: dict) -> tuple:
    """(cod_cliente, itens) — identifica o MESMO pedido (mesmo cliente,
    mesmos produtos/quantidades) independente do e-mail que o carregou."""
    itens_sig = tuple(sorted(
        (str(i.get('cod_prod', '')).strip(), float(i.get('qt') or 0))
        for i in bloco.get('itens', [])
    ))
    return (str(bloco.get('cod_cliente', '')).strip(), itens_sig)


def _construir_comparativo(cache: dict) -> list:
    import pandas as pd
    from meta import engine, carregar_dados

    def _cnpj_digits(v):
        return re.sub(r'\D', '', str(v or ''))

    cods_extraidos = set()
    cnpjs_extraidos = set()
    data_min = None
    for msg in cache.values():
        for bloco in msg.get('blocos', []):
            cod = str(bloco.get('cod_cliente', '')).strip()
            if cod.isdigit():
                cods_extraidos.add(cod)
            cnpj_digits = _cnpj_digits(bloco.get('cnpj'))
            if len(cnpj_digits) == 14:
                cnpjs_extraidos.add(cnpj_digits)
            d = msg.get('data_email', '')
            if d and (data_min is None or d < data_min):
                data_min = d

    if not cods_extraidos:
        print("Comparativo: nenhum cod_cliente reconhecido nos pedidos por e-mail.")
        return []

    # Busca cliente por CODCLI extraído OU por CNPJ, numa query só — cobre
    # tanto o caso normal (código certo) quanto o de correção abaixo (código
    # errado mas CNPJ bate). Também alimenta cliente_info (razão social/
    # fantasia/RCA) pra completar o que a extração deixou em branco.
    cliente_info = {}
    codcli_por_cnpj = {}
    condicoes = [f"C.CODCLI IN ({','.join(sorted(cods_extraidos))})"]
    if cnpjs_extraidos:
        cnpjs_sql = ",".join(f"'{c}'" for c in cnpjs_extraidos)
        condicoes.append(
            f"REPLACE(REPLACE(REPLACE(C.CGCENT,'.',''),'/',''),'-','') IN ({cnpjs_sql})"
        )
    try:
        df_cli = carregar_dados(f"""
            SELECT C.CODCLI, C.CLIENTE, COALESCE(C.FANTASIA, '') AS FANTASIA,
                   COALESCE(C.CGCENT, '') AS CNPJ, C.CODUSUR1,
                   COALESCE(C.BAIRROENT, '') AS BAIRRO,
                   COALESCE(U1.NOME, '') AS NOME_USUR1
            FROM crc.PCCLIENT C
            LEFT JOIN crc.PCUSUARI U1 ON C.CODUSUR1 = U1.CODUSUR
            WHERE {' OR '.join(condicoes)}
        """, engine, "comparativo_agendamento_clientes")
        df_cli.columns = df_cli.columns.str.upper()
        for _, r in df_cli.iterrows():
            rca_txt = f"{int(r['CODUSUR1'])} - {r['NOME_USUR1']}".strip(' -') if pd.notna(r['CODUSUR1']) else ''
            codcli = int(r['CODCLI'])
            cliente_info[codcli] = {
                'razao_social': str(r['CLIENTE'] or '').strip(),
                'fantasia':     str(r['FANTASIA'] or '').strip(),
                'cnpj':         str(r['CNPJ'] or '').strip(),
                'bairro':       str(r['BAIRRO'] or '').strip(),
                'rca':          rca_txt,
                'codusur':      int(r['CODUSUR1']) if pd.notna(r['CODUSUR1']) else None,
            }
            cnpj_num = _cnpj_digits(r['CNPJ'])
            if cnpj_num:
                codcli_por_cnpj[cnpj_num] = codcli
    except Exception as e:
        print(f"[AVISO] busca de cadastro de clientes (PCCLIENT) falhou ({str(e)[:100]}) — mantém só o que a extração leu.")

    # cod_cliente extraído por visão/IA às vezes vem com dígitos trocados
    # (ex: '62992' em vez do código real '69292', mesmo CNPJ/cliente/RCA —
    # confirmado real em 2026-08-26, pedido "FREITAS BEBIDAS": código errado
    # não batia com NENHUM cliente, a NF que já tinha faturado 3 dos 4 itens
    # nunca era encontrada, e o pedido inteiro aparecia "Pendente" à toa,
    # sem vendedor). CNPJ é bem mais confiável que um código de 5-6 dígitos
    # sem dígito verificador — só corrige quando o código extraído NÃO bate
    # com nenhum cliente de verdade mas o CNPJ bate (nunca troca um código
    # que já era válido por causa de um CNPJ digitado errado em outro lugar).
    cod_clientes = set()
    for msg in cache.values():
        for bloco in msg.get('blocos', []):
            cod_raw = str(bloco.get('cod_cliente', '')).strip()
            if not cod_raw.isdigit():
                continue
            if int(cod_raw) not in cliente_info:
                cnpj_num = _cnpj_digits(bloco.get('cnpj'))
                cod_corrigido = codcli_por_cnpj.get(cnpj_num)
                if cod_corrigido:
                    print(f"  [AVISO] cod_cliente corrigido via CNPJ: extraído={cod_raw!r} -> real={cod_corrigido!r} (CNPJ {cnpj_num})")
                    bloco['cod_cliente'] = str(cod_corrigido)
                    cod_raw = str(cod_corrigido)
            cod_clientes.add(cod_raw)

    codclis_sql = ",".join(sorted(cod_clientes))
    query = f"""
        SELECT CODCLI, CODPROD, NUMNOTA, DATA, QT, STATUS, CODUSUR
        FROM crc.PBI_PCPEDI
        WHERE CODFILIAL = 4
          AND CODCLI IN ({codclis_sql})
          AND DATA >= TO_DATE('{data_min}', 'YYYY-MM-DD')
          AND NVL(STATUS, 'X') != 'CANCELADA'
    """
    df = carregar_dados(query, engine, "comparativo_agendamento")
    df.columns = df.columns.str.upper()
    df['CODCLI']  = pd.to_numeric(df['CODCLI'], errors='coerce')
    df['CODPROD'] = pd.to_numeric(df['CODPROD'], errors='coerce')
    df['QT']      = pd.to_numeric(df['QT'], errors='coerce').fillna(0)
    df['NUMNOTA'] = pd.to_numeric(df['NUMNOTA'], errors='coerce')
    df['DATA']    = pd.to_datetime(df['DATA'], errors='coerce')
    df['CODUSUR'] = pd.to_numeric(df['CODUSUR'], errors='coerce')
    # Antes agregava faturado_por_par/nfs_por_par globalmente (todo o
    # período buscado, sem olhar a data do pedido) — uma NF emitida ANTES
    # do e-mail chegar podia ser creditada ao pedido como se o cumprisse
    # (achado real pelo usuário em 2026-08-31: pedido de cliente 90484 por
    # e-mail de 31/08 batido com NF 7120 emitida em 03/08, quase um mês
    # antes de o pedido existir). Agora filtra por cliente E por DATA >=
    # data do e-mail em _faturado_do_item, abaixo — só NF emitida no dia
    # do e-mail ou depois pode estar cumprindo aquele pedido.
    df_por_cli = {codcli: g for codcli, g in df.groupby('CODCLI')}
    nfs_agendamento = _nfs_agendamento()

    def _faturado_do_item(cod_cli_num, cod_prod_num, data_email_str, rca_codusur=None):
        sub = df_por_cli.get(cod_cli_num)
        if sub is None:
            return 0.0, []
        sub = sub[sub['CODPROD'] == cod_prod_num]
        try:
            dt_email = pd.Timestamp(data_email_str) if data_email_str else None
        except (ValueError, TypeError):
            dt_email = None
        if dt_email is not None:
            sub = sub[sub['DATA'] >= dt_email]
        # Exige CODUSUR da NF == RCA do pedido (extraído do e-mail, ou o
        # cadastrado do cliente como fallback) — pedido do usuário em
        # 2026-08-31. Só filtra quando dá pra saber o RCA esperado; sem
        # isso, um pedido faturado por outro RCA (giro normal da equipe)
        # nunca contaria como faturado.
        if rca_codusur is not None:
            sub = sub[sub['CODUSUR'] == rca_codusur]
        qt_faturada = float(sub['QT'].sum())
        nfs = sorted({str(int(v)) for v in sub['NUMNOTA'].dropna().unique()})
        return qt_faturada, nfs

    # O campo 'rca' de cada bloco vem de extração por IA (visão da OpenAI
    # lendo tabela/imagem do e-mail) — quando ela não lê um nome de verdade,
    # já devolveu lixo tipo "ANA CLARA"/"NATALI" (nome cortado) ou até "Não"
    # (resposta da IA quando não achou RCA nenhum, usada como se fosse valor)
    # — confirmado real pelo usuário em 2026-08-26. Só confia em texto puro
    # numérico (código de RCA), resolvido de verdade contra PCUSUARI; nome
    # extraído como texto livre nunca é usado — cai pro RCA cadastrado do
    # cliente (fallback acima, já vem de PCUSUARI via join).
    def _rca_codigo(v):
        """Código de RCA no início do texto extraído, mesmo com nome grudado
        depois (ex: '439 MATEUS CARDOSO - OFF TRADE', sem hífen separando —
        visto real, .isdigit() sozinho rejeitava e o RCA ficava em branco).
        None se não começar com número."""
        m = re.match(r'^\s*(\d+)', str(v or ''))
        return int(m.group(1)) if m else None

    nome_por_codusur = {}
    codusures_extraidos = {
        _rca_codigo(b.get('rca'))
        for msg in cache.values() for b in msg.get('blocos', [])
        if _rca_codigo(b.get('rca')) is not None
    }
    if codusures_extraidos:
        try:
            lista = ",".join(str(c) for c in codusures_extraidos)
            df_rca = carregar_dados(
                f"SELECT CODUSUR, NOME FROM crc.PCUSUARI WHERE CODUSUR IN ({lista})",
                engine, "comparativo_agendamento_rca",
            )
            df_rca.columns = df_rca.columns.str.upper()
            for _, r in df_rca.iterrows():
                nome_por_codusur[int(r['CODUSUR'])] = str(r['NOME'] or '').strip()
        except Exception as e:
            print(f"[AVISO] busca de RCA por código (PCUSUARI) falhou ({str(e)[:100]}) — mantém só o fallback do cliente.")

    # Agrupa respostas do mesmo pedido (mesma pessoa respondendo/reenviando
    # a própria thread) pra não duplicar no comparativo — cada "Re:"/"Fwd:"
    # inclui o corpo citado inteiro, e o parser HTML recria o mesmo bloco
    # com um msg_id novo (achado real em 2026-09-03: pedido do cliente
    # 90411/Padrão do Fonseca aparecia 5x, uma por resposta na thread).
    # Agrupa por (assunto sem Re/Fwd, cod_cliente, itens); mantém só o
    # e-mail mais antigo do grupo como "o pedido", mas herda
    # agendamento/observações de qualquer resposta que tenha essa info e o
    # original não (pedido do usuário: considerar o conteúdo das respostas).
    grupos: dict = {}
    for msg_id, msg in cache.items():
        assunto_norm = _assunto_normalizado(msg.get('subject', ''))
        for bloco in msg.get('blocos', []):
            chave = (assunto_norm,) + _assinatura_bloco(bloco)
            grupos.setdefault(chave, []).append((msg_id, msg, bloco))

    entradas = []
    duplicatas_removidas = 0
    for ocorrencias in grupos.values():
        if len(ocorrencias) == 1:
            entradas.append(ocorrencias[0])
            continue
        ocorrencias.sort(key=lambda o: o[1].get('data_email', ''))
        msg_id0, msg0, bloco0 = ocorrencias[0]
        agendamento = msg0.get('email_data_agendamento', '')
        observacoes = msg0.get('email_observacoes', '')
        for _, msg_r, _ in ocorrencias[1:]:
            if not agendamento and msg_r.get('email_data_agendamento'):
                agendamento = msg_r['email_data_agendamento']
            if not observacoes and msg_r.get('email_observacoes'):
                observacoes = msg_r['email_observacoes']
        if agendamento != msg0.get('email_data_agendamento', '') or observacoes != msg0.get('email_observacoes', ''):
            msg0 = {**msg0, 'email_data_agendamento': agendamento, 'email_observacoes': observacoes}
        entradas.append((msg_id0, msg0, bloco0))
        duplicatas_removidas += len(ocorrencias) - 1

    if duplicatas_removidas:
        print(f"  [DEDUPE] {duplicatas_removidas} bloco(s) duplicado(s) por resposta na mesma thread — removido(s) do comparativo.")

    resultado = []
    for msg_id, msg, bloco in entradas:
            cod_cli_raw = str(bloco.get('cod_cliente', '')).strip()
            if not cod_cli_raw.isdigit():
                continue
            cod_cli_num = int(cod_cli_raw)
            fallback = cliente_info.get(cod_cli_num, {})
            # rca_extraido só numérico (código de verdade lido pela extração)
            # — mais específico que o fallback do cliente (pode ser um RCA
            # cobrindo temporariamente, diferente do CODUSUR1 cadastrado).
            # Texto livre nunca é usado (ver comentário de _rca_codigo acima)
            # — cai direto pro RCA cadastrado do cliente. Resolvido AQUI (não
            # só pro texto exibido) porque agora também filtra o faturamento.
            rca_codigo = _rca_codigo(bloco.get('rca'))
            rca_codusur_match = rca_codigo if rca_codigo is not None else fallback.get('codusur')

            # Primeiro calcula a qtd faturada de cada item, sem ainda decidir
            # o status — precisa saber se ALGUM item do pedido já foi tocado
            # (faturado/parcial) antes de rotular os zerados.
            itens_calc = []
            for item in bloco.get('itens', []):
                cod_prod = str(item.get('cod_prod', '')).strip()
                if not cod_prod.isdigit():
                    continue
                qt_pedida = float(item.get('qt') or 0)
                qt_faturada, nfs_item = _faturado_do_item(
                    cod_cli_num, int(cod_prod), msg.get('data_email', ''), rca_codusur_match,
                )
                preco = float(item.get('preco') or 0)
                itens_calc.append((item, qt_pedida, qt_faturada, preco, nfs_item))

            # Se nenhum item do pedido foi faturado ainda, o pedido inteiro
            # provavelmente só não foi processado — "Pendente" (pode vir a
            # faturar). Se PELO MENOS um item já foi faturado/parcial, o
            # pedido já está sendo atendido, e quem ficou zerado foi cortado
            # de propósito — não vai mais vir.
            pedido_tocado = any(qt_faturada > 0 for _, _, qt_faturada, _, _ in itens_calc)
            status_zerado = 'Cortado' if pedido_tocado else 'Pendente'

            itens_out = []
            for item, qt_pedida, qt_faturada, preco, nfs_item in itens_calc:
                if qt_faturada <= 0:
                    status_item = status_zerado
                elif qt_faturada < qt_pedida:
                    status_item = 'Parcial'
                else:
                    status_item = 'Faturado'
                valor_faturado = round(preco * qt_faturada, 2)
                itens_out.append({
                    **item,
                    'qt_faturada':    qt_faturada,
                    'valor_faturado': valor_faturado,
                    'status':         status_item,
                    'nf':             ', '.join(nfs_item),
                    'agendado':       any(nf in nfs_agendamento for nf in nfs_item),
                })
            if not itens_out:
                continue
            # Nome pra exibição, a partir do rca_codigo já resolvido acima
            # (resolve o nome contra PCUSUARI, nome_por_codusur, montado no
            # início da função).
            if rca_codigo is not None:
                nome = nome_por_codusur.get(rca_codigo)
                rca_final = f"{rca_codigo} - {nome}" if nome else fallback.get('rca', '')
            else:
                rca_final = fallback.get('rca', '')
            resultado.append({
                'msg_id':       msg_id,
                'subject':      msg.get('subject', ''),
                'data_email':   msg.get('data_email', ''),
                'sistema':      bloco.get('sistema', ''),
                'cod_cliente':  cod_cli_raw,
                'razao_social': bloco.get('razao_social') or fallback.get('razao_social', ''),
                # Fantasia do CADASTRO (PCCLIENT, pelo cod_cliente) tem
                # prioridade sobre a extraída do e-mail — a extração por IA
                # costuma vir genérica (ex: "SUPERMARKET" sem identificar a
                # loja) enquanto o cadastro já tem o nome fantasia correto
                # daquele código específico (pedido do usuário em 2026-09-03).
                'fantasia':     fallback.get('fantasia') or bloco.get('fantasia', ''),
                'bairro':       fallback.get('bairro', ''),
                'cnpj':         bloco.get('cnpj') or fallback.get('cnpj', ''),
                'rca':          rca_final,
                'bonificacao':  _to_bool(bloco.get('bonificacao')),
                'prazo':        bloco.get('prazo', ''),
                'obs':          bloco.get('obs', ''),
                'email_data_agendamento': msg.get('email_data_agendamento', ''),
                'email_observacoes':      msg.get('email_observacoes', ''),
                'itens':        itens_out,
            })

    resultado.sort(key=lambda p: p['data_email'], reverse=True)
    return resultado


# ── Merge no agendamento_data.js ───────────────────────────────────────────────

def _merge_write(patch: dict):
    existing = {}
    if DATA_PATH.exists():
        raw = DATA_PATH.read_text(encoding='utf-8')
        m = re.search(r'=\s*(\{.*\});\s*$', raw.strip(), re.DOTALL)
        if m:
            try:
                existing = json.loads(m.group(1))
            except json.JSONDecodeError:
                existing = {}
    existing.update(patch)
    existing['atualizado_em'] = datetime.now().strftime('%d/%m/%Y %H:%M')
    with open(DATA_PATH, 'w', encoding='utf-8') as f:
        f.write(f"const AGENDAMENTO_DATA = {json.dumps(existing, ensure_ascii=False, indent=2)};\n")


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    cache = {}
    if CACHE_PATH.exists():
        try:
            cache = json.loads(CACHE_PATH.read_text(encoding='utf-8'))
        except json.JSONDecodeError:
            cache = {}

    service = _get_service()
    results = service.users().messages().list(userId='me', q=QUERY, maxResults=MAX_RESULTS).execute()
    messages = results.get('messages', [])
    print(f"Emails encontrados (destinatário offtrade@rigarr.com.br): {len(messages)}")

    novos = 0
    retentativas = 0
    for msg_meta in messages:
        msg_id = msg_meta['id']
        if msg_id in cache:
            # Reprocessa só a extração de agendamento/observações se ficou
            # vazia num e-mail que tem bloco CRC-4 reconhecido — cobre falha
            # pontual da OpenAI na primeira passada (achado em 2026-09-03:
            # e-mail "PEDIDOS ME MOVING REDE PADRAO DO FONSECA" tinha
            # "ENTREGAR DIA 03/09" bem no início do corpo, mas ficou com
            # email_data_agendamento vazio pra sempre porque o cache nunca
            # tenta de novo um msg_id já visto). Não repete blocos_crc4
            # (parsing HTML/imagem já ficou bom), só a chamada OpenAI, que é
            # o único passo com falha transitória conhecida.
            entry = cache[msg_id]
            if entry.get('blocos') and not entry.get('email_data_agendamento') and not entry.get('email_observacoes'):
                msg = service.users().messages().get(userId='me', id=msg_id, format='full').execute()
                partes_r = {'html_parts': [], 'image_attachment_ids': []}
                _extract_parts(msg['payload'], partes_r)
                agendamento_r = _extrair_agendamento_email(_texto_email(partes_r['html_parts']), entry.get('data_email', ''))
                if agendamento_r['data_agendamento'] or agendamento_r['observacoes']:
                    entry['email_data_agendamento'] = agendamento_r['data_agendamento']
                    entry['email_observacoes'] = agendamento_r['observacoes']
                    retentativas += 1
                    print(f"  [RETRY OK] '{entry.get('subject','')}': agendamento/observações recuperados")
                    CACHE_PATH.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding='utf-8')
            continue
        novos += 1

        msg = service.users().messages().get(userId='me', id=msg_id, format='full').execute()
        headers = {h['name'].lower(): h['value'] for h in msg['payload']['headers']}
        subject = headers.get('subject', '')
        data_email = datetime.fromtimestamp(int(msg['internalDate']) / 1000).strftime('%Y-%m-%d')

        partes = {'html_parts': [], 'image_attachment_ids': []}
        _extract_parts(msg['payload'], partes)

        # Nao filtra pelo texto cru do HTML antes de chamar _parse_html_pedido:
        # o label "SELECIONE O SISTEMA" as vezes vem quebrado por uma tag/quebra
        # de linha no meio ("SELECIONE<br>O SISTEMA"), entao o texto cru nunca
        # bate mas o parser (que junta o texto de cada celula) acha certinho.
        # Rodar o parser em toda parte html e' barato (retorna [] se nao achar
        # nada) e evita perder emails reais (bug confirmado em 2026-07-23).
        blocos = []
        for html in partes['html_parts']:
            blocos.extend(_parse_html_pedido(html))

        for att_id in partes['image_attachment_ids']:
            try:
                att = service.users().messages().attachments().get(
                    userId='me', messageId=msg_id, id=att_id).execute()
                png_bytes = base64.urlsafe_b64decode(att['data'] + '==')
                extraido = _parse_imagem_pedido(png_bytes)
                if extraido and extraido.get('itens'):
                    blocos.append(extraido)
                elif extraido is None:
                    print(f"  [AVISO] imagem de '{subject}' não retornou JSON válido — ignorada")
            except Exception as e:
                print(f"  [AVISO] falha ao processar imagem de '{subject}': {str(e)[:120]}")

        blocos_crc4 = [b for b in blocos if _norm_sistema(b.get('sistema', '')) in SISTEMAS_OK]

        if blocos_crc4:
            print(f"  '{subject}': {len(blocos_crc4)} bloco(s) CRC-4 ({len(blocos) - len(blocos_crc4)} de outra filial ignorado(s))")
        else:
            print(f"  '{subject}': nenhum bloco CRC-4 reconhecido — ignorado")

        agendamento_email = {"data_agendamento": "", "observacoes": ""}
        if blocos_crc4:
            agendamento_email = _extrair_agendamento_email(_texto_email(partes['html_parts']), data_email)

        cache[msg_id] = {
            'subject': subject, 'data_email': data_email, 'blocos': blocos_crc4,
            'email_data_agendamento': agendamento_email['data_agendamento'],
            'email_observacoes':      agendamento_email['observacoes'],
        }

        # Salva a cada e-mail (não só no final) — cada imagem já custou uma
        # chamada à OpenAI, então uma interrupção/timeout no meio do lote não
        # pode jogar fora o que já foi processado (mesmo raciocínio de
        # canhoto_digital.py, que salva a cada 20 itens).
        CACHE_PATH.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding='utf-8')
    print(f"OK - {novos} email(s) novo(s) processado(s), {retentativas} retentativa(s) de agendamento recuperada(s), {len(cache)} no total em {CACHE_PATH.name}")

    print("\nCruzando pedidos por e-mail com faturamento (crc.PBI_PCPEDI)...")
    try:
        comparativo = _construir_comparativo(cache)
        _merge_write({'comparativo': comparativo})
        print(f"OK - {len(comparativo)} pedido(s)/bonificação(ões) no comparativo -> {DATA_PATH}")
    except Exception as e:
        print(f"[AVISO] comparativo falhou ({e}) — agendamento_data.js mantém a aba 'comparativo' anterior.")

    repo_dir = str(BASE)
    try:
        subprocess.run(["git", "-C", repo_dir, "add", "agendamento_data.js", "pedidos_email_cache.json"], check=True)
        subprocess.run(["git", "-C", repo_dir, "commit", "-m",
                        f"Atualiza agendamento_data.js (pedidos por e-mail) - {datetime.now().strftime('%d/%m/%Y')}"])
        subprocess.run(["git", "-C", repo_dir, "push", "origin", "master"], check=True)
        print("OK agendamento_data.js enviado ao GitHub Pages.")
    except subprocess.CalledProcessError:
        print("[AVISO] git push falhou — ignorado, pipeline continua.")

    _publicar_static()


# ── Publica direto em /opt/offtrade-static (site) ─────────────────────────────
# Roda só na VPS — mesmo padrão de exportacao_pedidos_bloqueados.py::_publicar_static.
# Sem isso, agendamento_data.js só chegava ao site via "git push" — que SEMPRE
# falha na VPS (histórico divergente do clone local via SFTP, ver
# deploy_pipeline_vps.py) — então o 'comparativo' que este script recalcula do
# zero a cada hora nunca alcançava /opt/offtrade-static de verdade, deixando o
# site preso numa versão velha ("está faltando pedidos atuais", confirmado
# pelo usuário em 2026-08-26 — a VPS já tinha 332 itens no pipeline, mas o
# site mostrava uma cópia bem mais antiga). exportacao_agendamento.py tem a
# mesma chamada, já que os dois escrevem no mesmo arquivo merged.
def _publicar_static():
    if os.getenv("OFFTRADE_RUNTIME", "local") != "vps":
        return
    import shutil
    destino = "/opt/offtrade-static"
    if not DATA_PATH.exists():
        return
    tmp = os.path.join(destino, ".agendamento_data.js.tmp_publish")
    shutil.copy(DATA_PATH, tmp)
    os.replace(tmp, os.path.join(destino, "agendamento_data.js"))
    print(f"OK - agendamento_data.js copiado para {destino}")


if __name__ == "__main__":
    main()
