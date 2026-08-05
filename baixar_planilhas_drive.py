"""
Baixa as planilhas do Google Drive compartilhado que antes eram lidas
diretamente do Google Drive Desktop sincronizado localmente (G:\\Drives
compartilhados\\...) — caminho que não existe na VPS.

Fluxo:
  1. Autentica Drive (token.json gerado por google_auth_setup.py)
  2. Busca cada planilha por nome (Shared Drives) e baixa se estiver
     desatualizada, cacheando em planilhas_cache/
  3. Os scripts consumidores (meta.py, conferencia_preco.py,
     exportacao_sp.py, entregas.py) chamam as funções `caminho_*` deste
     módulo em vez de apontar direto pro caminho G:\\...
"""
import io
import socket
import threading
from pathlib import Path

BASE       = Path(__file__).parent
TOKEN      = BASE / "token.json"
CACHE_DIR  = BASE / "planilhas_cache"
SCOPES     = ["https://www.googleapis.com/auth/drive.readonly"]

_service = None


DRIVE_TIMEOUT = 30  # segundos — evita travar pra sempre numa conexão morta com o Google

# Timeout global de socket: o timeout do httplib2.Http(timeout=...) abaixo não
# cobre TODO caminho de rede usado por baixo dos panos (ex: refresh de token
# OAuth, que passa por google.auth.transport.requests / urllib3, não
# httplib2) — confirmado na prática: uma conexão https com o Google ficava
# em CLOSE_WAIT com o processo bloqueado num read() que nunca retornava,
# mesmo com o timeout do httplib2 configurado. socket.setdefaulttimeout()
# vira o piso para qualquer socket Python que não define o próprio timeout —
# não afeta o Oracle (thick client usa a lib C da Oracle, não o módulo
# socket do Python). Travou o pipeline da VPS por horas em 2026-07-20.
socket.setdefaulttimeout(DRIVE_TIMEOUT)


def _get_service():
    # Import tardio: se o pacote não estiver instalado (ex: venv local ainda
    # não atualizado), quem chama pode capturar o erro e cair no caminho
    # G:\ sincronizado, em vez do módulo inteiro falhar ao ser importado.
    import httplib2
    import google_auth_httplib2
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request
    from googleapiclient.discovery import build

    global _service
    if _service is not None:
        return _service
    if not TOKEN.exists():
        raise FileNotFoundError(
            f"token.json não encontrado. Execute google_auth_setup.py uma vez "
            f"para autorizar o acesso ao Drive."
        )
    creds = Credentials.from_authorized_user_file(str(TOKEN), SCOPES)
    if not creds.valid:
        if creds.expired and creds.refresh_token:
            creds.refresh(Request())
            TOKEN.write_text(creds.to_json(), encoding="utf-8")
        else:
            raise RuntimeError(
                "Token do Drive inválido ou sem refresh_token. "
                "Rode google_auth_setup.py novamente para reautorizar."
            )
    http = google_auth_httplib2.AuthorizedHttp(creds, http=httplib2.Http(timeout=DRIVE_TIMEOUT))
    _service = build("drive", "v3", http=http)
    return _service


def _buscar(query: str):
    """Retorna os arquivos (id, name, modifiedTime) que casam com `query`,
    ordenados do mais recente para o mais antigo."""
    service = _get_service()
    resp = service.files().list(
        q=query,
        corpora="allDrives",
        supportsAllDrives=True,
        includeItemsFromAllDrives=True,
        fields="files(id, name, modifiedTime)",
        orderBy="modifiedTime desc",
        pageSize=10,
    ).execute()
    return resp.get("files", [])


def baixar_arquivo(nome: str, nome_saida: str = None, contains: bool = False) -> Path:
    """Baixa (ou reaproveita do cache) o arquivo `nome` do Drive.
    Se `contains=True`, busca por `name contains nome` em vez de nome exato
    (útil quando o nome varia, ex: planilhas de "Controle de Notas")."""
    nome_saida = nome_saida or nome
    destino = CACHE_DIR / nome_saida
    CACHE_DIR.mkdir(exist_ok=True)

    nome_escapado = nome.replace("'", "\\'")
    op = "contains" if contains else "="
    query = f"name {op} '{nome_escapado}' and trashed = false"
    arquivos = _buscar(query)
    if not arquivos:
        if destino.exists():
            print(f"[AVISO] '{nome}' não encontrado no Drive — usando cópia em cache ({destino}).")
            return destino
        raise FileNotFoundError(f"'{nome}' não encontrado no Drive (query: {query}).")

    arquivo = arquivos[0]
    remoto_mtime = arquivo["modifiedTime"]

    if destino.exists():
        marker = destino.with_suffix(destino.suffix + ".mtime")
        if marker.exists() and marker.read_text().strip() == remoto_mtime:
            return destino

    from googleapiclient.http import MediaIoBaseDownload
    service = _get_service()
    request = service.files().get_media(fileId=arquivo["id"], supportsAllDrives=True)
    buf = io.BytesIO()
    downloader = MediaIoBaseDownload(buf, request)
    done = False
    while not done:
        _, done = downloader.next_chunk()
    destino.write_bytes(buf.getvalue())
    destino.with_suffix(destino.suffix + ".mtime").write_text(remoto_mtime)
    print(f"[OK] '{arquivo['name']}' baixado -> {destino}")
    return destino


# ── Atalhos para as planilhas usadas pelo pipeline ─────────────────────────────

def caminho_metas_rj() -> Path:
    return baixar_arquivo("METAS RJ.xlsx")


def caminho_metas_sp() -> Path:
    return baixar_arquivo("METAS SP.xlsx")


def caminho_controle_agendamentos() -> Path:
    return baixar_arquivo("CONTROLE AGENDAMENTOS.xlsx")


def caminho_tabela_preco_rj() -> Path:
    return baixar_arquivo("TABELA DE PREÇO RJ.xlsx")


def caminho_preco_promo() -> Path:
    return baixar_arquivo("PREÇO PROMO.xlsx")


def caminho_profit_rj() -> Path:
    return baixar_arquivo(
        "Controle de ultima entrada, descontos-acréscimos e precificação RJ - versão 1.xlsb"
    )


_PASTA_CONTROLE_NOTAS = ["LOGÍSTICA RJ", "APOIO LOGÍSTICO", "CONTROLE DE NOTAS"]
_drives_cache: dict = {}


def _resolver_drive_id(nome_drive: str) -> str:
    """Acha o ID de um Drive Compartilhado pelo nome (ex: '01-Logística').
    '01-Logística' é ele mesmo um Shared Drive, não uma pasta comum —
    confirmado inspecionando service.drives().list()."""
    if nome_drive in _drives_cache:
        return _drives_cache[nome_drive]
    service = _get_service()
    resp = service.drives().list(pageSize=50).execute()
    for d in resp.get("drives", []):
        if d.get("name") == nome_drive:
            _drives_cache[nome_drive] = d["id"]
            return d["id"]
    raise FileNotFoundError(f"Drive compartilhado '{nome_drive}' não encontrado.")


def _resolver_pasta(nome: str, drive_id: str, pasta_pai_id: str = None) -> str:
    service = _get_service()
    escapado = nome.replace("'", "\\'")
    query = f"name = '{escapado}' and mimeType = 'application/vnd.google-apps.folder' and trashed = false"
    if pasta_pai_id:
        query += f" and '{pasta_pai_id}' in parents"
    resp = service.files().list(
        q=query, corpora="drive", driveId=drive_id,
        includeItemsFromAllDrives=True, supportsAllDrives=True,
        fields="files(id, name)", pageSize=5,
    ).execute()
    achados = resp.get("files", [])
    if not achados:
        raise FileNotFoundError(f"Pasta '{nome}' não encontrada no Drive (dentro de {pasta_pai_id}).")
    return achados[0]["id"]


def _listar_arquivos_pasta(pasta_id: str, drive_id: str) -> list:
    service = _get_service()
    resp = service.files().list(
        q=f"'{pasta_id}' in parents and trashed = false",
        corpora="drive", driveId=drive_id,
        includeItemsFromAllDrives=True, supportsAllDrives=True,
        fields="files(id, name, modifiedTime)", orderBy="name", pageSize=20,
    ).execute()
    return resp.get("files", [])


def caminho_controle_notas(mm: str, mes_upper: str, ano) -> Path:
    """Acha a planilha de Controle de Notas de um mês/ano específico navegando
    a pasta real no Drive (.../CONTROLE DE NOTAS/{ano}/{mm} {mes_upper}/) em
    vez de buscar por nome em todo o Drive.

    Antes buscava globalmente por 'name contains CONTROLE DE NOTAS' + mês —
    isso pegava sem querer um arquivo de ano errado quando um decoy antigo
    batia no filtro (ex: 'Controle de Notas - AGOSTO.2025.xlsx' sendo usado
    como se fosse agosto/2026, porque a busca não filtrava por ano), e
    também falhava quando o arquivo do mês foi renomeado sem o texto
    'CONTROLE DE NOTAS' no nome (ex: agosto/2026 virou '8 AGOSTO.xlsx').
    Navegar pela pasta certa evita as duas causas de uma vez — mesma lógica
    do fallback local (_caminho_controle_notas_local), que já usa a pasta
    do mês em vez de procurar pelo nome do arquivo."""
    ano = str(ano)
    nome_saida = f"controle_notas_{ano}_{mm}_{mes_upper.lower()}.xlsx"
    destino = CACHE_DIR / nome_saida
    CACHE_DIR.mkdir(exist_ok=True)

    drive_id = _resolver_drive_id("01-Logística")
    pasta_id = None
    for nome_pasta in _PASTA_CONTROLE_NOTAS + [ano, f"{mm} {mes_upper}"]:
        pasta_id = _resolver_pasta(nome_pasta, drive_id, pasta_id)

    candidatos = [
        f for f in _listar_arquivos_pasta(pasta_id, drive_id)
        if f["name"].lower().endswith(".xlsx")
    ]
    if not candidatos:
        if destino.exists():
            return destino
        raise FileNotFoundError(f"Nenhuma planilha .xlsx na pasta Controle de Notas de {mes_upper}/{ano}.")

    # Prioriza arquivo com 'CONTROLE DE NOTAS' no nome (convenção mais comum);
    # senão pega o primeiro em ordem alfabética — mesmo critério do fallback
    # local (pasta_dir.glob("*.xlsx")[0]).
    preferidos = [f for f in candidatos if "CONTROLE DE NOTAS" in f["name"].upper()]
    arquivo = (preferidos or candidatos)[0]

    remoto_mtime = arquivo["modifiedTime"]
    marker = destino.with_suffix(destino.suffix + ".mtime")
    if destino.exists() and marker.exists() and marker.read_text().strip() == remoto_mtime:
        return destino

    from googleapiclient.http import MediaIoBaseDownload
    service = _get_service()
    request = service.files().get_media(fileId=arquivo["id"], supportsAllDrives=True)
    buf = io.BytesIO()
    downloader = MediaIoBaseDownload(buf, request)
    done = False
    while not done:
        _, done = downloader.next_chunk()
    destino.write_bytes(buf.getvalue())
    marker.write_text(remoto_mtime)
    print(f"[OK] '{arquivo['name']}' baixado -> {destino}")
    return destino


def _com_timeout_forcado(func, timeout):
    """Roda `func` numa thread daemon e desiste após `timeout`s, mesmo que a
    thread continue pendurada pra sempre. Necessário porque os timeouts das
    próprias libs do Google (httplib2, socket.setdefaulttimeout) já se
    mostraram insuficientes na prática — uma conexão que o Google derruba em
    silêncio (CLOSE_WAIT) deixa o processo bloqueado num read() que nenhum
    desses timeouts interrompe. Como a thread é daemon, ela não impede o
    processo de encerrar mesmo que nunca retorne. Travou o pipeline da VPS
    por dias em 2026-07-20 até essa correção."""
    resultado = {}

    def _run():
        try:
            resultado['valor'] = func()
        except Exception as e:
            resultado['erro'] = e

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    t.join(timeout)
    if t.is_alive():
        raise TimeoutError(f"Download do Drive não respondeu em {timeout}s (conexão travada)")
    if 'erro' in resultado:
        raise resultado['erro']
    return resultado['valor']


def com_fallback(bpd_func, caminho_local_fallback: str) -> Path:
    """Tenta baixar via Drive; se falhar por qualquer motivo (pacote ausente,
    token expirado, rede etc.) e o caminho local sincronizado (G:\\...) ainda
    existir, usa ele em vez de propagar o erro."""
    try:
        return _com_timeout_forcado(bpd_func, DRIVE_TIMEOUT * 2)
    except Exception as e:
        p = Path(caminho_local_fallback)
        if p.exists():
            print(f"[AVISO] Drive falhou ({e}) — usando cópia local sincronizada: {p}")
            return p
        raise


if __name__ == "__main__":
    # main.py roda este arquivo standalone (step "0/8") via subprocess — esse
    # caminho NÃO passa por com_fallback(), então sem o timeout forçado aqui
    # também ele trava pra sempre do mesmo jeito (confirmado em 2026-07-20:
    # com_fallback já protegido, mas esse loop ainda travava o pipeline).
    for fn in (caminho_metas_rj, caminho_metas_sp, caminho_tabela_preco_rj,
               caminho_preco_promo, caminho_profit_rj):
        try:
            _com_timeout_forcado(fn, DRIVE_TIMEOUT * 2)
        except Exception as e:
            print(f"[AVISO] falha ao baixar via {fn.__name__}: {e}")
