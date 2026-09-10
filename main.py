import os
import socket
import sys
import atexit
import traceback
from datetime import datetime
from pathlib import Path
from dotenv import load_dotenv

# Sem isso, uma conexão https com o Google (Drive/Gmail) pode ficar pendurada
# num read() que nunca retorna (socket em CLOSE_WAIT — o servidor já fechou
# do lado dele, mas nosso lado nunca recebe erro) — travou o main.py inteiro
# por quase 1h em 2026-07-24 (steps como meta.py/entregas.py rodam
# importados neste mesmo processo, não em subprocess, então herdam esse
# timeout). Mesma proteção que baixar_planilhas_drive.py já usa isolado,
# aplicada aqui bem cedo pra cobrir o processo inteiro. Não afeta o Oracle
# (thick client usa a lib C da Oracle, não o módulo socket do Python).
socket.setdefaulttimeout(30)

load_dotenv(Path(__file__).parent / ".env")

SEND_ALERTS = os.getenv("SEND_ALERTS", "1") == "1"
OFFTRADE_RUNTIME = os.getenv("OFFTRADE_RUNTIME", "local")

def step(nome):
    print(f"\n{'-' * 50}")
    print(f"  {nome}")
    print(f"{'-' * 50}")

_ALERTA_PIPELINE_NUMERO = os.getenv("ALERTA_PIPELINE_NUMERO", "5521992085320")


def _alertar_falha_pipeline(etapa, detalhe):
    """Avisa por WhatsApp quando uma etapa falha de um jeito silencioso (ex:
    token OAuth expirado) — sem isso, uma falha como essa só é notada dias
    depois, olhando o arquivo de saída manualmente (aconteceu com o token do
    Gmail do alerta_logistica_rj em 2026-07-28)."""
    try:
        from whatsapp_evolution import enviar_whatsapp
        texto = f"⚠️ Pipeline Off Trade — falha em: {etapa}\n{detalhe[:300]}"
        enviar_whatsapp(_ALERTA_PIPELINE_NUMERO, texto)
    except Exception:
        pass

# O pipeline roda de forma independente em dois lugares (Task Scheduler local,
# de hora em hora, e cron da VPS em horário comercial) — sem essa trava, duas
# execuções concorrentes escrevem nos mesmos _data.js/git ao mesmo tempo
# (aconteceu em 2026-07-06, gerou commits duplicados em sequência).
LOCK_PATH = Path(__file__).parent / ".pipeline.lock"
# Pipeline normal leva ~35-45min, mas com os timeouts forçados adicionados em
# 2026-07-20/21 (subprocess.run(timeout=600) em cada um dos ~18 passos +
# _com_timeout_forcado nas queries em-processo) o pior caso deixou de ser
# "trava pra sempre" e passou a ser "demora horas" quando a VPN/Oracle está
# ruim — 1h de expiração do lock virou curta demais e chegou a deixar dois
# main.py rodando ao mesmo tempo (confirmado em 2026-07-21, VPN lenta).
LOCK_EXPIRA_SEG = 18000  # 5h — acima disso, trava é considerada órfã mesmo se o PID ainda existir


def _pid_vivo(pid):
    """True/False se confirmado vivo/morto, None se não dá pra saber nesta
    plataforma. os.kill(pid, 0) não mata nada — só testa se o processo existe."""
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except OSError:
        return False
    except Exception:
        return None


def _adquirir_lock():
    if LOCK_PATH.exists():
        pid, idade = None, None
        try:
            pid_str, timestamp_str = LOCK_PATH.read_text(encoding='utf-8').strip().split('|', 1)
            pid = int(pid_str)
            idade = (datetime.now() - datetime.fromisoformat(timestamp_str)).total_seconds()
        except Exception:
            pass
        vivo = _pid_vivo(pid) if pid is not None else None
        # Trava só é considerada válida se o processo dono ainda está vivo E
        # dentro do prazo — só checar idade deixou passar dois main.py rodando
        # ao mesmo tempo em 2026-07-28 (processo travado há 3h, ainda vivo,
        # mas abaixo do teto de 5h só por tempo).
        if vivo is False:
            print("[AVISO] Lock encontrado mas o processo dono já não existe — assumindo execução anterior morta, prosseguindo.")
        elif idade is not None and idade < LOCK_EXPIRA_SEG:
            print(f"[AVISO] Já existe uma execução do pipeline em andamento (lock criado há {int(idade)}s, PID {pid}) — abortando esta execução para não escrever em cima dela.")
            sys.exit(0)
        else:
            print("[AVISO] Lock encontrado mas expirado/inválido — assumindo execução anterior travada, prosseguindo.")
    LOCK_PATH.write_text(f"{os.getpid()}|{datetime.now().isoformat()}", encoding='utf-8')
    atexit.register(_liberar_lock)

def _liberar_lock():
    try:
        LOCK_PATH.unlink(missing_ok=True)
    except Exception:
        pass

def main():
    _adquirir_lock()

    inicio = datetime.now()
    print(f"\n{'='*50}")
    print(f"  OFFTRADE - {inicio.strftime('%d/%m/%Y %H:%M')}")
    print(f"{'='*50}")

    try:
        step("0/8 - Baixando planilhas do Google Drive")
        try:
            import subprocess, sys as _sys
            result = subprocess.run(
                [_sys.executable, "baixar_planilhas_drive.py"],
                capture_output=True, text=True, timeout=600
            )
            print(result.stdout)
            if result.returncode != 0:
                print("[AVISO] baixar_planilhas_drive falhou — ignorado, pipeline continua.")
                print(result.stderr)
        except Exception:
            print("[AVISO] baixar_planilhas_drive falhou — ignorado, pipeline continua.")
            traceback.print_exc()

        # step 1/8 (import meta + meta.tabela_vendas) removido — só existia
        # pra pré-carregar meta.py antes do import exportacao_meta em
        # processo, que também saiu daqui (ver abaixo). Sem consumidor
        # in-process, virou trabalho pesado à toa em todo run.

        # exportacao_meta.py saiu do pipeline "principal" — roda sozinho a
        # cada 15min via cron próprio na VPS (pedido em 2026-08-05,
        # atualização mais frequente que o resto do pipeline). Rodar aqui
        # DE NOVO na VPS duplicaria a cada hora exatamente na mesma janela do
        # cron de 15min, dois processos escrevendo metas_data.js/vendas_data.js
        # ao mesmo tempo — por isso só roda quando OFFTRADE_RUNTIME != 'vps'.
        #
        # Só na VPS, porém, a fonte CASTAS é inalcançável (rede interna, IP
        # privado — ver memória project_banco_castas_rede_local): toda
        # execução do cron de 15min fica estruturalmente sem CASTAS. Rodar
        # aqui na execução LOCAL (hora em hora, já agendada) cobre esse
        # buraco — o próprio exportacao_meta.py se publica direto em
        # /opt/offtrade-static ao final (ver _publicar_static() nele), então
        # não depende do deploy_static_vps.py (que ignora esses arquivos de
        # propósito). Pedido do usuário em 2026-08-10.
        if OFFTRADE_RUNTIME != "vps":
            step("2b/8 - Metas + Histórico (metas_data.js, cobre CASTAS)")
            try:
                import subprocess, sys as _sys
                result = subprocess.run(
                    [_sys.executable, "exportacao_meta.py"],
                    capture_output=True, text=True, timeout=900
                )
                print(result.stdout)
                if result.returncode != 0:
                    print("[AVISO] exportacao_meta falhou — ignorado, pipeline continua.")
                    print(result.stderr)
            except Exception:
                print("[AVISO] exportacao_meta falhou — ignorado, pipeline continua.")
                traceback.print_exc()

        # exportacao_metas_gerais.py / exportacao_industria.py / exportacao_
        # raiox_oportunidades.py saíram do main.py em 09/09/2026 — ganharam
        # cron próprio na VPS (mesmo motivo de exportacao_meta.py/pedidos.py):
        # uma falha de conexão Oracle no meio dos ~20 passos sequenciais do
        # main.py marcava a engine "morta" pro resto do PROCESSO (ver
        # meta.py::carregar_dados), derrubando vários passos seguintes juntos
        # — confirmado real em 09/09/2026, essas 3 e mais 8 outras páginas
        # apareciam "Crítico" (>12h parado) de forma intermitente.

        # Campanha Amarula encerrada em 25/06/2026 — geração de dados desativada.
        # Reativar descomentando o bloco abaixo se a campanha voltar.
        # step("4/8 - Campanha Amarula (amarula_data.js)")
        # try:
        #     import subprocess, sys as _sys
        #     result = subprocess.run(
        #         [_sys.executable, "exportacao_amarula.py"],
        #         capture_output=True, text=True
        #     )
        #     print(result.stdout)
        #     if result.returncode != 0:
        #         print("[AVISO] exportacao_amarula falhou — ignorado, pipeline continua.")
        #         print(result.stderr)
        # except Exception:
        #     print("[AVISO] exportacao_amarula falhou — ignorado, pipeline continua.")
        #     traceback.print_exc()

        # campanha_crusoe.py / campanha_acao_amarula.py / entregas.py saíram
        # do main.py em 09/09/2026 — mesmo motivo do bloco acima (cron
        # próprio, evita cascata de engine Oracle "morta" no meio do run).

        # Conferência de preços desativada a pedido do usuário em 2026-07-31.
        # Reativar descomentando o bloco abaixo quando for pedido novamente.
        # step("4/8 - Conferência de preços")
        # try:
        #     import conferencia_preco
        # except Exception:
        #     print("[AVISO] conferencia_preco falhou — ignorado, pipeline continua.")
        #     traceback.print_exc()

        # pedidos.py (pedidos_data.js) saiu do main.py em 2026-09-04 — ganhou
        # cron próprio de 5 em 5 min (mesmo padrão/motivo de
        # exportacao_pedidos_bloqueados.py, pedido do usuário), fora do
        # main.py.

        # exportacao_agendamento.py / email_pedidos.py (agendamento_data.js)
        # saíram do main.py em 2026-09-04: a tarefa agendada local ficou
        # travada em 28/08 (ver deploy_static_vps.py::EXCLUDE_JS e
        # [[project_agendamento_deploy_overwrite]]), então rodar dentro do
        # main.py compartilhado (local + VPS) deixava o resultado refém de
        # qual dos dois lados rodou por último. Precisam de cron próprio na
        # VPS (mesmo padrão de exportacao_meta.py/exportacao_pedidos_
        # bloqueados.py) — ainda não configurado, então agendamento_data.js
        # fica sem atualizar até alguém configurar esse cron.

        # exportacao_sp.py / exportacao_es.py / exportacao_mg.py saíram daqui
        # pelo mesmo motivo do exportacao_meta.py acima — cron próprio de
        # 15min na VPS (2026-08-05).

        # exportacao_nao_pos_{sp,es,mg}.py saíram do main.py em 09/09/2026 —
        # mesmo motivo do bloco acima (cron próprio).

        step("8/9 - Base de clientes por RCA (clientes_rca_data.js)")
        try:
            import subprocess, sys as _sys
            result = subprocess.run(
                [_sys.executable, "exportacao_clientes_rca.py"],
                capture_output=True, text=True, timeout=600
            )
            print(result.stdout)
            if result.returncode != 0:
                print("[AVISO] exportacao_clientes_rca falhou — ignorado, pipeline continua.")
                print(result.stderr)
        except Exception:
            print("[AVISO] exportacao_clientes_rca falhou — ignorado, pipeline continua.")
            traceback.print_exc()

        # exportacao_performance_equipe.py saiu do main.py em 09/09/2026 —
        # mesmo motivo do bloco acima (cron próprio).

        step("8/9 - Comissão RJ Executivos/Pequenos Varejos (comissao_data.js)")
        try:
            import subprocess, sys as _sys
            result = subprocess.run(
                [_sys.executable, "exportacao_comissao.py"],
                capture_output=True, text=True, timeout=600
            )
            print(result.stdout)
            if result.returncode != 0:
                print("[AVISO] exportacao_comissao falhou — ignorado, pipeline continua.")
                print(result.stderr)
        except Exception:
            print("[AVISO] exportacao_comissao falhou — ignorado, pipeline continua.")
            traceback.print_exc()

        # exportacao_vendedores_auth.py saiu do main.py em 09/09/2026 — mesmo
        # motivo do bloco acima (cron próprio).

        step("8c - Promotoria: relatório Max Promotor (promotoria_data.js)")
        try:
            import subprocess, sys as _sys
            # 900s (era 600s): a partir de 2026-08-31 esse script também
            # resolve cidade/bairro no Oracle e gera análise de IA (OpenAI)
            # por visita nova — folga extra pra rodadas de catch-up.
            result = subprocess.run(
                [_sys.executable, "exportacao_promotoria.py"],
                capture_output=True, text=True, timeout=900
            )
            print(result.stdout)
            if result.returncode != 0:
                print("[AVISO] exportacao_promotoria falhou — ignorado, pipeline continua.")
                print(result.stderr)
        except Exception:
            print("[AVISO] exportacao_promotoria falhou — ignorado, pipeline continua.")
            traceback.print_exc()

        step("9/9 - Clientes inativos / sem compra / novos (clientes_inativos_data.js)")
        try:
            import subprocess, sys as _sys
            result = subprocess.run(
                [_sys.executable, "exportacao_clientes_inativos.py"],
                capture_output=True, text=True, timeout=600
            )
            print(result.stdout)
            if result.returncode != 0:
                print("[AVISO] exportacao_clientes_inativos falhou — ignorado, pipeline continua.")
                print(result.stderr)
        except Exception:
            print("[AVISO] exportacao_clientes_inativos falhou — ignorado, pipeline continua.")
            traceback.print_exc()

        step("9b - Inadimplencia (inadimplencia_data.js)")
        try:
            import subprocess, sys as _sys
            result = subprocess.run(
                [_sys.executable, "exportacao_inadimplencia.py"],
                capture_output=True, text=True, timeout=600
            )
            print(result.stdout)
            if result.returncode != 0:
                print("[AVISO] exportacao_inadimplencia falhou — ignorado, pipeline continua.")
                print(result.stderr)
        except Exception:
            print("[AVISO] exportacao_inadimplencia falhou — ignorado, pipeline continua.")
            traceback.print_exc()

        step("9c - Estoque (estoque_data.js)")
        try:
            import subprocess, sys as _sys
            result = subprocess.run(
                [_sys.executable, "exportacao_estoque.py"],
                capture_output=True, text=True, timeout=600
            )
            print(result.stdout)
            if result.returncode != 0:
                print("[AVISO] exportacao_estoque falhou — ignorado, pipeline continua.")
                print(result.stderr)
        except Exception:
            print("[AVISO] exportacao_estoque falhou — ignorado, pipeline continua.")
            traceback.print_exc()

        step("9d - Movimentação de estoque (estoque_movimentacao_data.js)")
        try:
            import subprocess, sys as _sys
            result = subprocess.run(
                [_sys.executable, "exportacao_estoque_movimentacao.py"],
                capture_output=True, text=True, timeout=900
            )
            print(result.stdout)
            if result.returncode != 0:
                print("[AVISO] exportacao_estoque_movimentacao falhou — ignorado, pipeline continua.")
                print(result.stderr)
        except Exception:
            print("[AVISO] exportacao_estoque_movimentacao falhou — ignorado, pipeline continua.")
            traceback.print_exc()

        if SEND_ALERTS:
            step("10/10 - Alertas Logistica RJ (Gmail -> nao entregues)")
            try:
                import subprocess, sys as _sys
                result = subprocess.run(
                    [_sys.executable, "alerta_logistica_rj.py"],
                    capture_output=True, timeout=600
                )
                print(result.stdout.decode("utf-8", errors="replace"))
                if result.returncode != 0:
                    print("[AVISO] alerta_logistica_rj falhou — ignorado, pipeline continua.")
                    _stderr = result.stderr.decode("utf-8", errors="replace")
                    print(_stderr)
                    _alertar_falha_pipeline("Alertas Logistica RJ (Gmail)", _stderr.strip().splitlines()[-1] if _stderr.strip() else "erro desconhecido")
            except Exception as _e:
                print("[AVISO] alerta_logistica_rj falhou — ignorado, pipeline continua.")
                traceback.print_exc()
                _alertar_falha_pipeline("Alertas Logistica RJ (Gmail)", str(_e))

            # Alerta de WhatsApp (conferência de preços) desativado a pedido
            # do usuário em 2026-07-31. Reativar quando for pedido novamente.
            # step("10/10 - Enviando alerta WhatsApp")
            # try:
            #     import envio_whatsapp
            # except Exception:
            #     print("[AVISO] envio_whatsapp falhou — ignorado, pipeline continua.")
            #     traceback.print_exc()
        else:
            step("10/10 - Alertas (pulado — SEND_ALERTS=0 neste ambiente)")

        step("11/11 - Deploy para VPS")
        if OFFTRADE_RUNTIME == "vps":
            try:
                import shutil
                destino = "/opt/offtrade-static"
                repo_dir = Path(__file__).parent
                arquivos = [f for f in repo_dir.glob("*.html") if f.name != "exemplo.html"]
                arquivos += list(repo_dir.glob("*.js"))
                # *.json fica de fora por padrão (evita publicar token/credenciais
                # por engano) — mesma allowlist de deploy_static_vps.py::ALLOWLIST_JSON.
                # Sem isso, promotoria_data.json (usado no Power BI) ficava
                # desatualizado: o cron da VPS só atualiza promotoria_data.js aqui,
                # e só um deploy_static_vps.py manual pegava o .json (achado real
                # em 2026-09-10 — .js em 10/09 11:09, .json parado em 08/09 12:32).
                ALLOWLIST_JSON = {"promotoria_data.json"}
                arquivos += [f for f in repo_dir.glob("*.json") if f.name in ALLOWLIST_JSON]
                for f in arquivos:
                    shutil.copy(f, os.path.join(destino, f.name))
                print(f"OK - {len(arquivos)} arquivo(s) copiados para {destino} (deploy local, sem SSH)")
            except Exception:
                print("[AVISO] cópia local para /opt/offtrade-static falhou — ignorado, pipeline continua.")
                traceback.print_exc()
        else:
            for script in ["deploy_static_vps.py", "deploy_vps.py"]:
                try:
                    import subprocess, sys as _sys
                    result = subprocess.run(
                        [_sys.executable, script],
                        capture_output=True, text=True, timeout=600
                    )
                    print(result.stdout)
                    if result.returncode != 0:
                        print(f"[AVISO] {script} falhou — ignorado, pipeline continua.")
                        print(result.stderr)
                except Exception:
                    print(f"[AVISO] {script} falhou — ignorado, pipeline continua.")
                    traceback.print_exc()

        step("12/12 - Verificação final: todas as páginas atualizaram?")
        try:
            import subprocess, sys as _sys
            result = subprocess.run(
                [_sys.executable, "exportacao_status_paginas.py"],
                capture_output=True, text=True, timeout=120
            )
            print(result.stdout)
            if result.returncode != 0:
                print("[AVISO] exportacao_status_paginas falhou — ignorado, pipeline continua.")
                print(result.stderr)
        except Exception:
            print("[AVISO] exportacao_status_paginas falhou — ignorado, pipeline continua.")
            traceback.print_exc()

    except Exception:
        print("\n[ERRO] Falha na execução:")
        traceback.print_exc()
        sys.exit(1)

    fim = datetime.now()
    duracao = (fim - inicio).seconds
    print(f"\n{'='*50}")
    print(f"  Concluído em {duracao}s")
    print(f"{'='*50}")

    try:
        import json as _json, re as _re2
        _status_path = Path(__file__).parent / "status_paginas_data.js"
        _raw = _status_path.read_text(encoding="utf-8")
        _m = _re2.search(r"=\s*(\{.*\});\s*$", _raw.strip(), _re2.DOTALL)
        _dados = _json.loads(_m.group(1)) if _m else {}
        _criticos = [p["arquivo"] for p in _dados.get("paginas", []) if p["status"] in ("Crítico", "Sem timestamp")]
        if _criticos:
            print(f"  [AVISO] {len(_criticos)} página(s) SEM atualizar (>12h): {', '.join(_criticos)}")
        else:
            print(f"  [OK] Todas as páginas do ciclo horário estão atualizadas.")
    except Exception:
        print("  [AVISO] Não foi possível ler status_paginas_data.js pra resumo final.")
    print()

if __name__ == "__main__":  
    main()
