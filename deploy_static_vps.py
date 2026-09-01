"""
Deploy do site estático para VPS Hostinger.
- Sincroniza todo *.html da raiz (exceto exemplo.html) e todo *.js (dados + auth)
  para /opt/offtrade-static.
- Servido pelo nginx com URL limpa (try_files) — não precisa reiniciar serviço.
"""
import os, sys
import paramiko
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

VPS_IP       = os.getenv("VPS_IP",       "147.79.107.137")
VPS_USER     = os.getenv("VPS_USER",     "root")
VPS_PASSWORD = os.getenv("VPS_PASSWORD", "")
REMOTE_DIR   = "/opt/offtrade-static"
# main.py, quando roda na VPS (cron horário), tem seu próprio passo de
# "deploy" que copia todo *.html/*.js de /opt/offtrade-pipeline por cima de
# /opt/offtrade-static (sem exclusão nenhuma) — assumindo que o pipeline já
# está atualizado. Como deploy_pipeline_vps.py não roda toda hora, esse
# diretório ficava velho e o cron revertia qualquer HTML publicado só aqui
# (bug real, achado pelo usuário em 2026-09-01: pedidos_mercos.html perdeu
# os botões de PDF/WhatsApp horas depois de publicados). Sincronizar os
# dois destinos juntos, sempre, elimina esse drift.
REMOTE_DIR_PIPELINE = "/opt/offtrade-pipeline"
PORT         = 22

HERE = Path(__file__).parent

EXCLUDE_HTML = {"exemplo.html"}

# acessos_data.js só existe de verdade na VPS (gerado a partir do log do
# nginx por exportacao_acessos.py, via cron próprio a cada 10 min) — a cópia
# local é só um stub vazio de teste (não há log de nginx fora da VPS).
# Sincronizar isso por cima sobrescreveria os dados reais de acesso.
#
# metas/vendas/fontes_status: exportacao_meta.py (+ exportacao_es/mg/sp.py)
# saíram do main.py em 2026-08-05 e passaram a rodar sozinhos via cron
# próprio na VPS, de 15 em 15min — a cópia local desses arquivos ficou
# congelada em 06/08 11:37 (não é mais gerada aqui) e, sem essa exclusão,
# essa versão velha era reenviada por cima do dado fresco da VPS toda vez
# que a tarefa agendada local rodava main.py (confirmado em 2026-08-07:
# site ficou >30h mostrando dado de 06/08 apesar da VPS atualizar a cada
# 15min sem parar).
EXCLUDE_JS = {
    "acessos_data.js",
    "metas_data.js", "vendas_data.js", "vendas_es_data.js",
    "vendas_mg_data.js", "vendas_sp_data.js", "fontes_status_data.js",
    # promotoria_data.js / estoque_movimentacao_data.js: mesmo motivo de
    # metas/vendas acima — a VPS gera sozinha (via main.py) e a cópia local
    # fica velha entre uma rodada e outra; sincronizar por cima sobrescreveria
    # o dado fresco (2026-08-21).
    "promotoria_data.js",
    "estoque_movimentacao_data.js",
    # pedidos_bloqueados_data.js: exportacao_pedidos_bloqueados.py roda só na
    # VPS, cron próprio de 5 em 5 min, fora do main.py (pedido do usuário em
    # 2026-08-25) — mesmo motivo de metas/vendas acima, sem cópia local pra
    # sincronizar (nem deveria existir uma).
    "pedidos_bloqueados_data.js",
    # agendamento_data.js: mesmo motivo de metas/vendas acima — está dentro do
    # main.py (não saiu como metas/vendas), mas a tarefa agendada local parou
    # de rodar (cópia local presa em 28/08/2026 16:51) enquanto o cron da VPS
    # seguiu gerando dado fresco a cada hora; sincronizar por cima sobrescreveu
    # o dado fresco da VPS com essa cópia local velha (bug real, 2026-08-31 —
    # ver [[project_agendamento_deploy_overwrite]]).
    "agendamento_data.js",
    # pedidos_mercos_data.js / estoque_mercos_data.js: gerar_pedidos_mercos_
    # data.py e gerar_estoque_mercos_spon_data.py rodam só na VPS, cron
    # próprio de 30 em 30 min, fora do main.py — mesmo padrão de auto-
    # publicação de metas/vendas/promotoria acima, mas nunca tinham entrado
    # nessa lista (bug real, achado pelo usuário em 2026-08-31: página
    # presa em 27/08 porque 4 deploys desta sessão sobrescreveram o dado
    # fresco da VPS com a cópia local parada).
    "pedidos_mercos_data.js",
    "estoque_mercos_data.js",
}


def ssh_run(client, cmd, check=True):
    _, stdout, stderr = client.exec_command(cmd)
    code = stdout.channel.recv_exit_status()
    out  = stdout.read().decode(errors="replace").strip()
    err  = stderr.read().decode(errors="replace").strip()
    if out:
        print(out)
    if err and check and code != 0:
        print(f"[stderr] {err}")
    if check and code != 0:
        raise RuntimeError(f"Falhou (cod {code}): {cmd}")
    return out, code


def static_files() -> list[Path]:
    html_files = [f for f in HERE.glob("*.html") if f.name not in EXCLUDE_HTML]
    js_files   = [f for f in HERE.glob("*.js") if f.name not in EXCLUDE_JS]
    return sorted(html_files) + sorted(js_files)


def deploy():
    print(f"-> Conectando a VPS {VPS_IP}...")
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        client.connect(VPS_IP, port=PORT, username=VPS_USER, password=VPS_PASSWORD, timeout=20)
    except Exception as e:
        print(f"[ERRO] SSH: {e}")
        sys.exit(1)

    sftp = client.open_sftp()
    ssh_run(client, f"mkdir -p {REMOTE_DIR}", check=False)
    ssh_run(client, f"mkdir -p {REMOTE_DIR_PIPELINE}", check=False)

    arquivos = static_files()
    print("\n-> Sincronizando arquivos estáticos...")
    for local in arquivos:
        remote = f"{REMOTE_DIR}/{local.name}"
        sftp.put(str(local), remote)
        print(f"   {local.name} -> {remote}")

    print("\n-> Sincronizando cópia em /opt/offtrade-pipeline (evita reversão pelo deploy do main.py)...")
    for local in arquivos:
        remote = f"{REMOTE_DIR_PIPELINE}/{local.name}"
        sftp.put(str(local), remote)

    sftp.close()
    client.close()
    print(f"\nOK site estático atualizado em {REMOTE_DIR} (e espelhado em {REMOTE_DIR_PIPELINE}) - https://offtrade.duckdns.org")


if __name__ == "__main__":
    deploy()
