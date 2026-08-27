"""
Sincroniza os arquivos exportados manualmente da Mercos (Downloads local)
pra um diretorio fixo na VPS (/opt/mercos-exports), pra o cron de la
(gerar_pedidos_mercos_data.py / gerar_estoque_mercos_spon_data.py, a cada
30min) sempre ter o snapshot mais recente.

Rodar depois de cada reexportacao da Mercos:
  1. relatorio.xls              (Indicadores > Relatorios > Produtos por pedido)
  2. Vendas detalhadas.xls      (Indicadores > Relatorios > Vendas detalhadas)
  3. produtos_mercos_spon.csv   (Representadas > SPON DISTRIBUIDORA > Produtos e tabelas)

Uso: python sync_mercos_exports_vps.py
"""
import os
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

ARQUIVOS = ["relatorio.xls", "Vendas detalhadas.xls", "produtos_mercos_spon.csv"]


def sync():
    faltando = [f for f in ARQUIVOS if not (DOWNLOADS / f).exists()]
    if faltando:
        print(f"[AVISO] arquivo(s) nao encontrados em {DOWNLOADS}: {', '.join(faltando)} — sincronizando só os que existem.")

    print(f"-> Conectando a VPS {VPS_IP}...")
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(VPS_IP, port=PORT, username=VPS_USER, password=VPS_PASSWORD, timeout=20)
    sftp = client.open_sftp()

    _, stdout, _ = client.exec_command(f"mkdir -p {REMOTE_DIR}")
    stdout.channel.recv_exit_status()

    print(f"\n-> Enviando pra {REMOTE_DIR}...")
    for fname in ARQUIVOS:
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
