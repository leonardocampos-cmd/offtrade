"""
Consulta de status de entrega SP via API do Canhoto Digital (ComprovaFácil,
softcenter.com.br). Usado pra alimentar o status_log de pedidos SP em
pedidos.py, equivalente ao que a planilha "Controle de Notas" (RJ) já faz
pros pedidos CRC.

Até 2026-08-20 isso rodava via Selenium (scraping do HTML do painel) — só
funcionava local (a VPS não tem navegador) e era lento (~1 NF a cada
poucos segundos). Trocado por chamadas diretas em
https://api.web.softcenter.com.br/v1 (o próprio backend que o painel React
usa por trás), descoberto inspecionando as requisições de rede do painel
logado. NÃO é uma API oficial/documentada pelo softcenter — pode mudar sem
aviso — mas hoje é bem mais rápida e roda em qualquer lugar (inclusive na
VPS), sem precisar de navegador. Autentica por sessão (cookies
accessToken/XSRF-TOKEN, setados automaticamente pelo requests.Session()
após o POST em /auth/new-session) — nunca lida com o token em texto puro
fora da sessão HTTP.

Cache em canhoto_status.json: uma vez que uma NF chega em COMPROVADO
(status terminal — não muda mais), não é reconsultada nas próximas
execuções. Evita rebuscar milhares de NFs toda hora.
"""
import json
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

API_BASE = "https://api.web.softcenter.com.br/v1"
CACHE_PATH = Path(__file__).parent / "canhoto_status.json"
STATUS_TERMINAL = {"COMPROVADO"}

VPS_IP       = os.getenv("VPS_IP", "147.79.107.137")
VPS_USER     = os.getenv("VPS_USER", "root")
VPS_PASSWORD = os.getenv("VPS_PASSWORD", "")
VPS_REMOTE_PATH = "/opt/offtrade-pipeline/canhoto_status.json"

# IDs de empresa na conta do Canhoto Digital (fixos — não mudam; extraídos
# uma vez do initialUser.companies do painel logado). "Rigarr SP" é a
# empresa padrão da conta; "Spon Filial" é uma filial separada (CODFILIAL
# diferente no Oracle SPON) cujas NFs não existem sob "Rigarr SP" — bug
# real confirmado em 2026-08-20 com a NF 7392/SPON (CODFILIAL=1), que
# ficava "ABERTO" no cache achando por engano um documento parecido de
# "Rigarr SP" quando na verdade estava "ENTREGA TOTAL" sob "Spon Filial".
# "BLENDED" fica de fora — é outra marca, não usa numeração de NF do SPON.
_EMPRESAS = {
    "Rigarr SP":   "677c07ef3c0622a850855265",
    "Spon Filial": "6965260ddb0d932dff2e789a",
}

# Status/sub-status da API (inglês) -> vocabulário em português que o resto
# do pipeline (pedidos.py, pedidos.html, report_diario_pedidos.py) já espera
# (mesmo texto que aparecia nos cards do kanban raspado antes).
_STATUS_MAP = {
    "OPEN":     "ABERTO",
    "LOADED":   "CARREGADO",
    "PROVED":   "COMPROVADO",
    "UNUSABLE": "CARREGADO",  # documento excluído/invalidado — sub_status='EXCLUIDO' cobre o detalhe
    "TRANSFER": "CARREGADO",  # em transferência entre veículo/CD, ainda não comprovado — visto em 2026-08-20
}
_SUBSTATUS_MAP = {
    "ALL":        "ENTREGA TOTAL",
    "PARTIAL":    "ENTREGA PARCIAL",
    "DEVOLUTION": "DEVOLUÇÃO",
}

_TZ_BR = timezone(timedelta(hours=-3))


def _sincronizar_vps():
    """Envia o cache pra VPS, que roda pedidos.py sozinha via cron (horário
    comercial) mas não faz essa consulta por conta própria — sem isso o
    status_log de pedidos SP fica sempre vazio/desatualizado nas rodadas da
    VPS, sobrescrevendo a versão boa gerada aqui."""
    if not VPS_PASSWORD:
        print("[AVISO] VPS_PASSWORD não configurado — pulando sincronização com a VPS.")
        return
    try:
        import paramiko
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        client.connect(VPS_IP, username=VPS_USER, password=VPS_PASSWORD, timeout=20)
        sftp = client.open_sftp()
        sftp.put(str(CACHE_PATH), VPS_REMOTE_PATH)
        sftp.close()
        client.close()
        print(f"Canhoto Digital: cache sincronizado com a VPS ({VPS_REMOTE_PATH}).")
    except Exception as e:
        print(f"[AVISO] falha ao sincronizar cache com a VPS: {str(e)[:150]}")


def _fmt_dt(iso_str, prefixo):
    """'2026-08-10T15:59:46.831Z' (UTC) -> 'Entregue em: 10/08/2026 às 12:59'
    (BRT, UTC-3) — mesmo formato de texto que pedidos.py já sabe interpretar
    (_RE_ENTREGUE_EM)."""
    if not iso_str:
        return ""
    try:
        dt = datetime.strptime(iso_str.split(".")[0], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)
    except ValueError:
        return ""
    dt_br = dt.astimezone(_TZ_BR)
    return f"{prefixo} {dt_br.strftime('%d/%m/%Y')} às {dt_br.strftime('%H:%M')}"


def _login() -> requests.Session:
    usuario = os.getenv("CANHOTO_DIGITAL_USER")
    senha = os.getenv("CANHOTO_DIGITAL_PASS")
    if not usuario or not senha:
        raise RuntimeError("CANHOTO_DIGITAL_USER/CANHOTO_DIGITAL_PASS não configurados no .env")
    session = requests.Session()
    r = session.post(f"{API_BASE}/auth/new-session", json={"email": usuario, "password": senha}, timeout=20)
    if r.status_code != 200:
        raise RuntimeError(f"Login na API do Canhoto Digital falhou (HTTP {r.status_code}).")
    return session


def _consultar(session, empresa_id, nf) -> dict | None:
    """Consulta uma NF numa empresa. Retorna {'status','sub_status','documento','info'}
    ou None se não encontrada. A API também casa por substring (digitar
    "7392" pode retornar "3/177392") — mesmo cuidado que existia no scraping:
    só aceita se o numnota do 'document' bater exato com a NF buscada."""
    params = {
        "company": empresa_id,
        "from": "2025-01-01T03:00:00.000Z",
        "to": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.999Z"),
        "page": 0,
        "document": nf,
        "filterType": "document",
        "sort": "MOST_RECENT",
        "type": "ALL",
    }
    # Retry com backoff em 429 — sem delay nenhum entre chamadas (que o
    # scraping via Selenium tinha de graça pelo time.sleep(1.8) de cada
    # busca) a API bloqueia rápido com "Too Many Requests" (confirmado em
    # 2026-08-20 logo na primeira revarredura em lote).
    for tentativa in range(5):
        r = session.get(f"{API_BASE}/deliveries", params=params, timeout=20)
        if r.status_code != 429:
            break
        espera = float(r.headers.get("Retry-After", 0)) or (2 ** tentativa)
        time.sleep(espera)
    r.raise_for_status()
    nf_alvo = nf.strip()
    for item in r.json().get("data", []):
        documento = item.get("document", "") or ""
        if documento.rsplit("/", 1)[-1].strip() != nf_alvo:
            continue
        status_raw = item.get("status", "")
        status = _STATUS_MAP.get(status_raw, status_raw)
        recinfo = item.get("receivementInfos") or {}
        if status_raw == "UNUSABLE":
            sub_status, info = "EXCLUIDO", ""
        elif status_raw == "TRANSFER":
            sub_status, info = "TRANSFERÊNCIA", _fmt_dt(item.get("issueDate"), "Emitido em:")
        elif recinfo:
            tipo = recinfo.get("type", "")
            sub_status = _SUBSTATUS_MAP.get(tipo, tipo or status)
            info = _fmt_dt(recinfo.get("date"), "Entregue em:")
        else:
            sub_status = status
            info = _fmt_dt(item.get("issueDate"), "Emitido em:")
        return {"status": status, "sub_status": sub_status, "documento": documento, "info": info}
    return None


def buscar_status_lote(nfs: list) -> dict:
    """Busca status de uma lista de NFs, usando cache local pra pular as já
    finalizadas (COMPROVADO). Retorna {nf: {...}} pras NFs encontradas."""
    cache = {}
    if CACHE_PATH.exists():
        try:
            cache = json.loads(CACHE_PATH.read_text(encoding="utf-8"))
        except Exception:
            cache = {}

    # Preserva a ordem de chegada (não reordena alfabeticamente) — quem monta
    # a lista (_nfs_sp_de_pedidos_data) já entrega mais recente primeiro,
    # pedido do usuário em 2026-08-20 pra revarredura priorizar as notas mais
    # atuais em vez de ordem lexicográfica de NF (sem relação com data).
    nfs_unicas = list(dict.fromkeys(str(nf).strip() for nf in nfs if nf and str(nf).strip()))
    pendentes = [
        nf for nf in nfs_unicas
        if nf not in cache or cache[nf].get("status") not in STATUS_TERMINAL
    ]

    if not pendentes:
        print("Canhoto Digital: nada pendente, tudo já em cache (COMPROVADO).")
        _sincronizar_vps()
        return {nf: cache[nf] for nf in nfs_unicas if nf in cache}

    print(f"Canhoto Digital: {len(pendentes)} NF(s) pendente(s) de consulta (de {len(nfs_unicas)} única(s)).")
    session = _login()
    for i, nf in enumerate(pendentes, 1):
        resultado = None
        for nome_empresa, empresa_id in _EMPRESAS.items():
            time.sleep(0.25)  # espaça as chamadas — sem isso a API devolve 429 rápido
            try:
                resultado = _consultar(session, empresa_id, nf)
            except Exception as e:
                print(f"  [AVISO] falha ao buscar NF {nf} em '{nome_empresa}': {str(e)[:120]}")
                continue
            if resultado:
                if nome_empresa != "Rigarr SP":
                    resultado["empresa"] = nome_empresa
                break
        if resultado:
            resultado["atualizado_em"] = datetime.now().strftime("%d/%m/%Y %H:%M")
            cache[nf] = resultado
        if i % 50 == 0:
            print(f"  {i}/{len(pendentes)} consultada(s)...")
            CACHE_PATH.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")

    CACHE_PATH.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Canhoto Digital: cache salvo em {CACHE_PATH} ({len(cache)} NF(s) no total).")
    _sincronizar_vps()
    return {nf: cache[nf] for nf in nfs_unicas if nf in cache}


def _nfs_sp_de_pedidos_data() -> list:
    """Lê pedidos_data.js e extrai as NFs de pedidos faturados no sistema
    SPON (única empresa coberta pelo Canhoto Digital até agora), da mais
    recente pra mais antiga ('data_ord', 'YYYY-MM-DD') — pedido do usuário
    em 2026-08-20 pra revarredura priorizar as notas mais atuais."""
    return [nf for _mes, nfs in _nfs_por_mes_de_pedidos_data() for nf in nfs]


def _nfs_por_mes_de_pedidos_data() -> list:
    """Mesma extração de _nfs_sp_de_pedidos_data(), mas agrupada por mês
    ('AAAA-MM', mais recente primeiro) — [(mes, [numnota, ...]), ...]. Pedido
    do usuário em 2026-08-20 pra sincronizar o cache com a VPS a cada mês
    processado (buscar_status_lote já chama _sincronizar_vps() no fim de
    cada lote), em vez de só depois da revarredura inteira terminar."""
    import re
    from collections import OrderedDict
    caminho = Path(__file__).parent / "pedidos_data.js"
    if not caminho.exists():
        print("[AVISO] pedidos_data.js não encontrado — rode pedidos.py primeiro.")
        return []
    texto = caminho.read_text(encoding="utf-8")
    m = re.search(r"const PEDIDOS_DATA\s*=\s*(\{.*\});", texto, re.DOTALL)
    if not m:
        return []
    dados = json.loads(m.group(1))
    faturados = [
        p for p in dados.get("faturados", [])
        if p.get("sistema") == "SPON" and p.get("numnota")
    ]
    faturados.sort(key=lambda p: p.get("data_ord") or "", reverse=True)
    por_mes = OrderedDict()
    for p in faturados:
        mes = (p.get("data_ord") or "")[:7] or "sem-data"
        por_mes.setdefault(mes, []).append(p["numnota"])
    return list(por_mes.items())


if __name__ == "__main__":
    import sys
    if sys.argv[1:]:
        resultado = buscar_status_lote(sys.argv[1:])
        print(f"OK - {len(resultado)} NF(s) com status no cache.")
    else:
        grupos = _nfs_por_mes_de_pedidos_data()
        if not grupos:
            print("Nenhuma NF de SP para consultar.")
        else:
            total = 0
            for mes, nfs_mes in grupos:
                print(f"=== Mês {mes}: {len(nfs_mes)} NF(s) ===")
                resultado = buscar_status_lote(nfs_mes)
                total += len(resultado)
            print(f"OK - {total} NF(s) com status no cache (todos os meses, sincronizado com a VPS a cada mês).")
