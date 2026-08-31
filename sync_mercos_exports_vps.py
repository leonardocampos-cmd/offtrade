"""
Sincroniza os arquivos exportados manualmente da Mercos (Downloads local)
pra um diretorio fixo na VPS (/opt/mercos-exports), pra o cron de la
(gerar_pedidos_mercos_data.py / gerar_estoque_mercos_spon_data.py, a cada
30min) sempre ter o snapshot mais recente.

Rodar depois de cada reexportacao da Mercos:
  1. relatorio*.xls             (Indicadores > Relatorios > Produtos por pedido —
     pode ser um unico "relatorio.xls" ou varios pedacos, ex: "relatorio_sem3.xls"
     "relatorio_sem4.xls", quando o periodo precisa ser dividido pra nao estourar
     o limite de 5000 linhas do relatorio)
  2. Vendas detalhadas.xls      (Indicadores > Relatorios > Vendas detalhadas)
  3. produtos_mercos_spon.csv   (Representadas > SPON DISTRIBUIDORA > Produtos e tabelas)

Uso: python sync_mercos_exports_vps.py
"""
import os
from glob import glob
from pathlib import Path

import paramiko
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

VPS_IP       = os.getenv("VPS_IP",       "147.79.107.137")
VPS_USER     = os.getenv("VPS_USER",     "root")
VPS_PASSWORD = os.getenv("VPS_PASSWORD", "")
REMOTE_DIR   = "/opt/mercos-exports"
PORT         = 22

DOWNLOADS = Path(r"C:\Users\LeonardoCampos\Downloads")

ARQUIVOS_FIXOS = ["Vendas detalhadas.xls", "produtos_mercos_spon.csv"]


def _arquivos_produtos():
    # "relatorio.xls" (export unico) ou "relatorio_sem*.xls" (export dividido
    # por periodo) — nunca pega "relatorio (N).xls" (variantes de download
    # duplicado do Chrome) nem relatorios de outro tipo (ex: estoque).
    nomes = set()
    if (DOWNLOADS / "relatorio.xls").exists():
        nomes.add("relatorio.xls")
    for caminho in glob(str(DOWNLOADS / "relatorio_sem*.xls")):
        nomes.add(Path(caminho).name)
    return sorted(nomes)


def sync():
    arquivos = _arquivos_produtos() + ARQUIVOS_FIXOS
    faltando = [f for f in ARQUIVOS_FIXOS if not (DOWNLOADS / f).exists()]
    if faltando:
        print(f"[AVISO] arquivo(s) nao encontrados em {DOWNLOADS}: {', '.join(faltando)} — sincronizando só os que existem.")
    if not _arquivos_produtos():
        print(f"[AVISO] nenhum relatorio.xls / relatorio_sem*.xls encontrado em {DOWNLOADS}.")

    print(f"-> Conectando a VPS {VPS_IP}...")
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(VPS_IP, port=PORT, username=VPS_USER, password=VPS_PASSWORD, timeout=20)
    sftp = client.open_sftp()

    _, stdout, _ = client.exec_command(f"mkdir -p {REMOTE_DIR}")
    stdout.channel.recv_exit_status()

    # Limpa relatorio*.xls remotos antes de reenviar, pra nao deixar pedacos
    # de uma exportacao anterior (ex: "relatorio.xls" de um periodo que foi
    # substituido por "relatorio_sem*.xls") misturados com o snapshot novo.
    _, stdout, _ = client.exec_command(f"rm -f {REMOTE_DIR}/relatorio*.xls")
    stdout.channel.recv_exit_status()

    print(f"\n-> Enviando pra {REMOTE_DIR}...")
    for fname in arquivos:
        local = DOWNLOADS / fname
        if not local.exists():
            continue
        remoto_tmp = f"{REMOTE_DIR}/.{fname}.tmp_upload"
        sftp.put(str(local), remoto_tmp)
        sftp.posix_rename(remoto_tmp, f"{REMOTE_DIR}/{fname}")
        print(f"   {fname}")

    sftp.close()
    client.close()
    print("\nOK - snapshot da Mercos sincronizado com a VPS.")


if __name__ == "__main__":
    sync()
