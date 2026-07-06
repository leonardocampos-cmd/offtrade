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
from pathlib import Path

BASE       = Path(__file__).parent
TOKEN      = BASE / "token.json"
CACHE_DIR  = BASE / "planilhas_cache"
SCOPES     = ["https://www.googleapis.com/auth/drive.readonly"]

_service = None


def _get_service():
    # Import tardio: se o pacote não estiver instalado (ex: venv local ainda
    # não atualizado), quem chama pode capturar o erro e cair no caminho
    # G:\ sincronizado, em vez do módulo inteiro falhar ao ser importado.
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
    _service = build("drive", "v3", credentials=creds)
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


def caminho_tabela_preco_rj() -> Path:
    return baixar_arquivo("TABELA DE PREÇO RJ.xlsx")


def caminho_preco_promo() -> Path:
    return baixar_arquivo("PREÇO PROMO.xlsx")


def caminho_profit_rj() -> Path:
    return baixar_arquivo(
        "Controle de ultima entrada, descontos-acréscimos e precificação RJ - versão 1.xlsb"
    )


def caminho_controle_notas(mm: str, mes_upper: str) -> Path:
    """Acha a planilha de Controle de Notas de um mês específico (nome varia:
    '07 JULHO CONTROLE DE NOTAS.xlsx', '06 JUNHO - Controle de Notas 2026.xlsx' etc).
    Busca por nome contendo o número do mês + 'controle de notas', cacheando
    localmente por mês (mm_mes_upper.xlsx) para não rebaixar historico já usado."""
    nome_saida = f"controle_notas_{mm}_{mes_upper.lower()}.xlsx"
    destino = CACHE_DIR / nome_saida
    CACHE_DIR.mkdir(exist_ok=True)

    query = f"name contains 'CONTROLE DE NOTAS' and trashed = false"
    candidatos = [
        f for f in _buscar(query)
        if mm in f["name"] or mes_upper in f["name"].upper()
    ]
    if not candidatos:
        if destino.exists():
            return destino
        raise FileNotFoundError(f"Controle de Notas de {mes_upper} não encontrado no Drive.")

    arquivo = candidatos[0]
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


def com_fallback(bpd_func, caminho_local_fallback: str) -> Path:
    """Tenta baixar via Drive; se falhar por qualquer motivo (pacote ausente,
    token expirado, rede etc.) e o caminho local sincronizado (G:\\...) ainda
    existir, usa ele em vez de propagar o erro."""
    try:
        return bpd_func()
    except Exception as e:
        p = Path(caminho_local_fallback)
        if p.exists():
            print(f"[AVISO] Drive falhou ({e}) — usando cópia local sincronizada: {p}")
            return p
        raise


if __name__ == "__main__":
    for fn in (caminho_metas_rj, caminho_metas_sp, caminho_tabela_preco_rj,
               caminho_preco_promo, caminho_profit_rj):
        try:
            fn()
        except Exception as e:
            print(f"[AVISO] falha ao baixar via {fn.__name__}: {e}")
