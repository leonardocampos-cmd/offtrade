"""
Watchdog do pipeline — roda via cron a cada 10min na VPS.

Se o main.py atual estiver rodando há mais que MAX_DURACAO_SEG, mata o
processo, limpa o lock e reinicia. Sem isso, uma trava (ex: conexão
Oracle/VPN pendurada) só seria percebida na próxima janela do cron
(8h-18h) — em 2026-07-28 um processo ficou travado 4h20min sem que
ninguém notasse, até um pedido manual de verificação. Fora da janela
comercial isso deixaria a produção sem atualizar a noite inteira.

Não precisa (nem deve) checar horário comercial: um processo travado
consome recursos e deve ser limpo a qualquer hora; o próprio main.py
decide se há trabalho a fazer.
"""
import os
import signal
import subprocess
import sys
from datetime import datetime
from pathlib import Path

BASE = Path(__file__).parent
LOCK_PATH = BASE / ".pipeline.lock"
LOG_PATH = Path("/var/log/offtrade_pipeline.log")
MAX_DURACAO_SEG = 90 * 60  # 1h30 — pipeline normal leva 35-60min


def _pid_vivo(pid):
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _alertar(msg):
    try:
        sys.path.insert(0, str(BASE))
        from whatsapp_evolution import enviar_whatsapp
        enviar_whatsapp(os.getenv("ALERTA_PIPELINE_NUMERO", "5521992085320"), msg)
    except Exception:
        pass


def main():
    if not LOCK_PATH.exists():
        return

    try:
        pid_str, ts_str = LOCK_PATH.read_text(encoding="utf-8").strip().split("|", 1)
        pid = int(pid_str)
        idade = (datetime.now() - datetime.fromisoformat(ts_str)).total_seconds()
    except Exception:
        return

    if idade < MAX_DURACAO_SEG:
        return

    if not _pid_vivo(pid):
        LOCK_PATH.unlink(missing_ok=True)
        return

    print(f"[WATCHDOG] PID {pid} rodando há {int(idade)}s (> {MAX_DURACAO_SEG}s) — matando e reiniciando.")
    try:
        os.kill(pid, signal.SIGKILL)
    except OSError:
        pass
    LOCK_PATH.unlink(missing_ok=True)

    _alertar(
        f"⚠️ Pipeline Off Trade travado há {int(idade / 60)}min — "
        f"processo encerrado e reiniciado automaticamente pelo watchdog."
    )

    subprocess.Popen(
        [sys.executable, "main.py"],
        cwd=str(BASE),
        stdout=open(LOG_PATH, "a"),
        stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL,
        start_new_session=True,
    )
    print("[WATCHDOG] main.py reiniciado.")


if __name__ == "__main__":
    main()
