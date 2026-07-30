"""
Bot: lê emails 'ALERTA LOGÍSTICA RJ' do Gmail e marca NFs como Não Entregue.

Fluxo:
  1. Autentica Gmail (token_gmail.json gerado por gmail_setup.py)
  2. Busca emails com o assunto (lidos e não lidos)
  3. Parseia RCA + tabela de NFs do corpo do email
  4. Salva em alertas_rj.json (por data)
  5. Patcha entregas_data.js (move NFs para nao_entregue)
  6. Commit + push ao GitHub Pages
"""
import os
import re
import sys
import json
import base64
import subprocess
from pathlib import Path
from datetime import date, timedelta

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from bs4 import BeautifulSoup
from google.oauth2.credentials import Credentials
from google.oauth2 import service_account
from google.auth.transport.requests import Request
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

BASE          = Path(__file__).parent
TOKEN_GMAIL   = BASE / "token_gmail.json"
ALERTAS_JSON  = BASE / "alertas_rj.json"
ENTREGAS_JS   = BASE / "entregas_data.js"
SCOPES        = ["https://www.googleapis.com/auth/gmail.modify"]
SUBJECT       = "ALERTA LOGÍSTICA RJ"

# Service account com domain-wide delegation (opcional) — se configurada, evita
# de vez a reautenticação manual (o token de usuário via OAuth "installed app"
# expira sozinho a cada 7 dias enquanto o app estiver em modo "Testing" no
# Google Cloud Console). Ver GMAIL_AUTOMACAO.md para como habilitar.
SA_FILE          = BASE / "service_account_gmail.json"
GMAIL_DELEGATE   = os.getenv("GMAIL_DELEGATE_USER", "")

RCA_RE = re.compile(
    r"RCA:\s*(\d+)\s*[-–]\s*(.+?)\s*\|\s*Taxa de Falha:\s*([\d.,]+)%",
    re.IGNORECASE,
)


# ── Autenticação ───────────────────────────────────────────────────────────────

def _get_service():
    if SA_FILE.exists() and GMAIL_DELEGATE:
        creds = service_account.Credentials.from_service_account_file(
            str(SA_FILE), scopes=SCOPES
        ).with_subject(GMAIL_DELEGATE)
        return build("gmail", "v1", credentials=creds)

    if not TOKEN_GMAIL.exists():
        raise FileNotFoundError(
            f"token_gmail.json não encontrado. "
            f"Execute gmail_setup.py uma vez para autorizar o acesso ao Gmail."
        )
    creds = Credentials.from_authorized_user_file(str(TOKEN_GMAIL), SCOPES)
    if not creds.valid:
        if creds.expired and creds.refresh_token:
            creds.refresh(Request())
            TOKEN_GMAIL.write_text(creds.to_json(), encoding="utf-8")
        else:
            raise RuntimeError(
                "Token Gmail inválido ou sem refresh_token. "
                "Rode gmail_setup.py novamente para reautorizar."
            )
    return build("gmail", "v1", credentials=creds)


# ── Extração do corpo ──────────────────────────────────────────────────────────

def _decode_b64(data: str) -> str:
    return base64.urlsafe_b64decode(data + "==").decode("utf-8", errors="replace")


def _extract_body(payload: dict) -> tuple[str, str]:
    """Retorna (conteúdo, mime_type). Prefere HTML."""
    mime = payload.get("mimeType", "")

    if mime.startswith("multipart/"):
        parts = payload.get("parts", [])
        # Procura HTML primeiro, depois texto
        for want in ("text/html", "text/plain"):
            for part in parts:
                if part.get("mimeType") == want:
                    data = part.get("body", {}).get("data", "")
                    if data:
                        return _decode_b64(data), want
            # Recursão em multipart aninhado
            for part in parts:
                if part.get("mimeType", "").startswith("multipart/"):
                    result = _extract_body(part)
                    if result[0]:
                        return result
        return "", mime

    data = payload.get("body", {}).get("data", "")
    return (_decode_b64(data) if data else ""), mime


# ── Parsing: HTML ─────────────────────────────────────────────────────────────

def _parse_table_html(table_elem) -> list[dict]:
    rows = table_elem.find_all("tr")
    if len(rows) < 2:
        return []

    # Cabeçalho
    headers = [th.get_text(" ", strip=True).upper() for th in rows[0].find_all(["th", "td"])]
    col = {}
    for i, h in enumerate(headers):
        if re.search(r"^NF$|^N[ÚU]MERO\s*NF|^NF\s", h):
            col["nf"] = i
        elif "CLIENTE" in h:
            col["cliente"] = i
        elif "PLACA" in h or "ROTA" in h:
            col["rota"] = i
        elif "RESPONS" in h:
            col["responsavel"] = i
        elif "MOTIVO" in h:
            col["motivo"] = i
        elif "VALOR" in h:
            col["valor"] = i

    # Fallback: primeira coluna é NF se não encontrou
    if "nf" not in col and headers:
        col["nf"] = 0

    if "nf" not in col:
        return []

    def _cell(cells, key):
        idx = col.get(key)
        if idx is None or idx >= len(cells):
            return ""
        return cells[idx].get_text(" ", strip=True)

    nfs = []
    for row in rows[1:]:
        cells = row.find_all(["td", "th"])
        if not cells:
            continue
        nf_raw = _cell(cells, "nf")
        nf_num = re.sub(r"\D", "", nf_raw)
        if not nf_num:
            continue
        nfs.append({
            "nf":           nf_num,
            "cliente":      _cell(cells, "cliente"),
            "rota":         _cell(cells, "rota"),
            "responsavel":  _cell(cells, "responsavel"),
            "motivo":       _cell(cells, "motivo"),
            "valor":        _cell(cells, "valor"),
        })
    return nfs


def _parse_html(html: str) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    alertas = []
    current_rca = None

    # Itera elementos de nível superior em ordem de documento
    for elem in soup.find_all(True):
        if elem.name == "table":
            if current_rca is not None:
                nfs = _parse_table_html(elem)
                if nfs:
                    alertas.append({**current_rca, "nfs": nfs})
                    current_rca = None
        elif elem.name in ("p", "div", "h1", "h2", "h3", "h4", "h5", "td", "span", "b", "strong"):
            txt = elem.get_text(" ", strip=True)
            m = RCA_RE.search(txt)
            if m:
                # Um elemento pai (ex: <div>) pode "ver" o mesmo texto do
                # cabecalho <h4> filho via get_text() recursivo, disparando o
                # mesmo RCA duas vezes — a segunda vez engolindo uma tabela
                # que na verdade pertence a outro bloco (bug real: gerou NF
                # 134/66 "Taxa Outros ..." atribuidas ao RCA errado). So'
                # aceita o match mais especifico (sem filho que tambem bata).
                if any(RCA_RE.search(child.get_text(" ", strip=True)) for child in elem.find_all(True)):
                    continue
                current_rca = {
                    "rca":       int(m.group(1)),
                    "rca_nome":  m.group(2).strip(),
                    "taxa_falha": float(m.group(3).replace(",", ".")),
                }

    return alertas


# ── Parsing: texto puro ───────────────────────────────────────────────────────

def _parse_text(text: str) -> list[dict]:
    """Parseia email em texto plano com colunas separadas por espaços/tabs."""
    alertas = []
    current_rca = None
    col_indices = {}
    in_table = False

    for line in text.splitlines():
        line_s = line.strip()
        if not line_s:
            continue

        # Linha de RCA
        m = RCA_RE.search(line_s)
        if m:
            current_rca = {
                "rca":       int(m.group(1)),
                "rca_nome":  m.group(2).strip(),
                "taxa_falha": float(m.group(3).replace(",", ".")),
            }
            in_table = False
            col_indices = {}
            continue

        if current_rca is None:
            continue

        # Cabeçalho da tabela
        upper = line_s.upper()
        if re.search(r"\bNF\b", upper) and "CLIENTE" in upper:
            # Detecta posições das colunas no texto
            col_indices = {}
            for name, pattern in [
                ("nf",          r"\bNF\b"),
                ("cliente",     r"CLIENTE"),
                ("rota",        r"PLACA|ROTA"),
                ("responsavel", r"RESPONS"),
                ("motivo",      r"MOTIVO"),
                ("valor",       r"VALOR"),
            ]:
                mm = re.search(pattern, upper)
                if mm:
                    col_indices[name] = mm.start()
            in_table = True
            continue

        if not in_table or "nf" not in col_indices:
            continue

        # Linha de dados: split por múltiplos espaços ou tab
        parts = re.split(r"\t|  +", line_s)
        parts = [p.strip() for p in parts if p.strip()]

        if not parts:
            continue

        # Verifica se começa com número (NF)
        nf_raw = parts[0]
        if not re.match(r"^\d+$", nf_raw):
            continue

        nfs_list = alertas[-1]["nfs"] if alertas and alertas[-1]["rca"] == current_rca["rca"] else None
        if nfs_list is None:
            alertas.append({**current_rca, "nfs": []})
            nfs_list = alertas[-1]["nfs"]

        def _get(idx):
            return parts[idx] if idx < len(parts) else ""

        nfs_list.append({
            "nf":           nf_raw,
            "cliente":      _get(1),
            "rota":         _get(2),
            "responsavel":  _get(3),
            "motivo":       _get(4),
            "valor":        _get(5),
        })

    return alertas


# ── Atualiza alertas_rj.json ──────────────────────────────────────────────────

def _save_alertas(alertas: list[dict], hoje: str):
    existing = {}
    if ALERTAS_JSON.exists():
        try:
            existing = json.loads(ALERTAS_JSON.read_text(encoding="utf-8"))
        except Exception:
            pass

    # Acumula NFs do dia (evita duplicatas por NF)
    dia_atual = existing.get(hoje, [])
    nfs_ja_salvos = {item["nf"] for item in dia_atual}

    for alerta in alertas:
        for nf_info in alerta["nfs"]:
            if nf_info["nf"] not in nfs_ja_salvos:
                dia_atual.append({
                    "nf":          nf_info["nf"],
                    "rca":         alerta["rca"],
                    "rca_nome":    alerta["rca_nome"],
                    "cliente":     nf_info["cliente"],
                    "rota":        nf_info["rota"],
                    "responsavel": nf_info["responsavel"],
                    "motivo":      nf_info["motivo"],
                    "valor":       nf_info["valor"],
                })
                nfs_ja_salvos.add(nf_info["nf"])

    existing[hoje] = dia_atual
    ALERTAS_JSON.write_text(json.dumps(existing, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"  alertas_rj.json: {len(dia_atual)} NF(s) não entregue(s) em {hoje}")


# ── Patcha entregas_data.js ───────────────────────────────────────────────────

def _nf_clean(numnota: str) -> str:
    try:
        return str(int(float(numnota))) if numnota else ""
    except Exception:
        return numnota.strip()


def _patch_entregas(alertas: list[dict], hoje: str):
    if not ENTREGAS_JS.exists():
        print(f"  AVISO: {ENTREGAS_JS} não encontrado — execute entregas.py primeiro.")
        return 0

    raw = ENTREGAS_JS.read_text(encoding="utf-8")
    json_str = re.sub(r"^const\s+ENTREGAS_DATA\s*=\s*", "", raw.strip()).rstrip(";").strip()
    data = json.loads(json_str)

    # Monta set de NFs a marcar (carrega RCA/nome do alerta-pai junto, usado
    # no fallback abaixo)
    nfs_alerta = {}
    for alerta in alertas:
        for nf_info in alerta["nfs"]:
            nfs_alerta[nf_info["nf"]] = {**nf_info, "rca": alerta.get("rca"), "rca_nome": alerta.get("rca_nome", "")}

    total_movidas = 0
    nfs_restantes = dict(nfs_alerta)

    for vendedor in data["vendedores"]:
        if "nao_entregue" not in vendedor:
            vendedor["nao_entregue"] = []

        nfs_ja = {_nf_clean(p.get("numnota", "")) for p in vendedor["nao_entregue"]}

        for lista_key in ("em_rota", "emitido_s_rota"):
            restantes = []
            for ped in vendedor.get(lista_key, []):
                nf = _nf_clean(ped.get("numnota", ""))
                if nf in nfs_restantes and nf not in nfs_ja:
                    info = nfs_restantes.pop(nf)
                    ped_copy = dict(ped)
                    ped_copy["motivo_alerta"]      = info.get("motivo", "")
                    ped_copy["responsavel_alerta"] = info.get("responsavel", "")
                    vendedor["nao_entregue"].append(ped_copy)
                    nfs_ja.add(nf)
                    total_movidas += 1
                    print(f"  NF {nf} → Não Entregue ({vendedor['nome']})")
                else:
                    restantes.append(ped)
            vendedor[lista_key] = restantes

    # Fallback: NF que ja teve rota num dia passado e falhou, entao nao esta
    # em 'em_rota' (so' olha rota de hoje) nem 'emitido_s_rota' (exclui de
    # proposito quem ja teve rota algum dia) — sem acesso aos dataframes do
    # entregas.py aqui, monta uma entrada minima so' com os dados do proprio
    # alerta (cliente/motivo/valor), casando o vendedor pelo nome do RCA.
    for nf, info in nfs_restantes.items():
        rca_nome = (info.get("rca_nome") or "").strip().upper()
        if not rca_nome:
            continue
        vendedor = next((v for v in data["vendedores"] if v["nome"].upper().startswith(rca_nome)), None)
        if vendedor is None:
            continue
        vendedor.setdefault("nao_entregue", []).append({
            "numped": "", "numnota": nf, "data": "", "cliente": info.get("cliente", ""),
            "placa": "", "rota": info.get("rota", ""), "status_ped": "", "status_log": "",
            "motivo": "", "obs": "", "total": 0.0, "itens": [],
            "motivo_alerta": info.get("motivo", ""), "responsavel_alerta": info.get("responsavel", ""),
        })
        total_movidas += 1
        print(f"  NF {nf} → Não Entregue ({vendedor['nome']}, fallback sem pedido original)")

    ENTREGAS_JS.write_text(
        f"const ENTREGAS_DATA = {json.dumps(data, ensure_ascii=False, indent=2)};\n",
        encoding="utf-8",
    )
    return total_movidas


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    print(f"\n{'='*50}")
    print(f"  ALERTA LOGÍSTICA RJ - Gmail Bot")
    print(f"{'='*50}")

    service = _get_service()

    hoje = date.today()
    # Segunda-feira: pega sábado + domingo; demais dias: emails de hoje
    if hoje.weekday() == 0:
        data_inicio = hoje - timedelta(days=2)
        after_str  = data_inicio.strftime("%Y/%m/%d")
        before_str = hoje.strftime("%Y/%m/%d")
        q = f'subject:"{SUBJECT}" after:{after_str} before:{before_str}'
        print(f"Buscando emails de {data_inicio.strftime('%d/%m')} a {(hoje - timedelta(days=1)).strftime('%d/%m')}...")
    else:
        after_str = hoje.strftime("%Y/%m/%d")
        q = f'subject:"{SUBJECT}" after:{after_str}'
        print(f"Buscando emails de hoje ({hoje.strftime('%d/%m')})...")

    results = service.users().messages().list(
        userId="me",
        q=q,
        maxResults=20,
    ).execute()

    messages = results.get("messages", [])
    print(f"Emails encontrados: {len(messages)}")

    if not messages:
        print("Nenhum alerta novo.")
        return

    todos_alertas = []
    hoje = hoje.isoformat()

    for msg_meta in messages:
        msg = service.users().messages().get(
            userId="me", id=msg_meta["id"], format="full"
        ).execute()

        subject = next(
            (h["value"] for h in msg["payload"]["headers"] if h["name"].lower() == "subject"),
            "",
        )
        print(f"\n-> {subject}")

        body, mime = _extract_body(msg["payload"])

        if "html" in mime:
            alertas = _parse_html(body)
        else:
            # Tenta HTML mesmo assim, fallback texto
            alertas = _parse_html(body) or _parse_text(body)

        if not alertas:
            print("  AVISO: nenhum RCA/NF encontrado no email.")
            continue

        for a in alertas:
            print(f"  RCA {a['rca']} ({a['rca_nome']}): {len(a['nfs'])} NF(s)")
            for nf in a["nfs"]:
                print(f"    NF {nf['nf']} | {nf['cliente'][:40]} | {nf['motivo'][:50]}")

        todos_alertas.extend(alertas)

        # Marca como lido
        service.users().messages().modify(
            userId="me",
            id=msg_meta["id"],
            body={"removeLabelIds": ["UNREAD"]},
        ).execute()

    if not todos_alertas:
        print("\nNenhum alerta parseiado com sucesso.")
        return

    print(f"\n--- Salvando alertas ---")
    _save_alertas(todos_alertas, hoje)

    print(f"\n--- Atualizando entregas_data.js ---")
    movidas = _patch_entregas(todos_alertas, hoje)

    if movidas > 0:
        repo_dir = str(BASE)
        files = ["alertas_rj.json", "entregas_data.js"]
        subprocess.run(["git", "-C", repo_dir, "add"] + files, check=True)
        subprocess.run(
            ["git", "-C", repo_dir, "commit", "-m",
             f"Alerta Logística RJ: {movidas} NF(s) não entregues - {hoje}"],
        )
        subprocess.run(["git", "-C", repo_dir, "push", "origin", "master"], check=True)
        print(f"\n[OK] {movidas} NF(s) marcadas como Não Entregue e enviadas ao GitHub Pages.")
    else:
        print("\n[OK] Alertas salvos. Nenhuma NF nova para mover (já processadas anteriormente).")


if __name__ == "__main__":
    main()
