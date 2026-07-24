import os
import sys
import atexit
import traceback
from datetime import datetime
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

SEND_ALERTS = os.getenv("SEND_ALERTS", "1") == "1"
OFFTRADE_RUNTIME = os.getenv("OFFTRADE_RUNTIME", "local")

def step(nome):
    print(f"\n{'-' * 50}")
    print(f"  {nome}")
    print(f"{'-' * 50}")

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
LOCK_EXPIRA_SEG = 18000  # 5h — acima disso, trava é considerada órfã

def _adquirir_lock():
    if LOCK_PATH.exists():
        try:
            _, timestamp_str = LOCK_PATH.read_text(encoding='utf-8').strip().split('|', 1)
            idade = (datetime.now() - datetime.fromisoformat(timestamp_str)).total_seconds()
        except Exception:
            idade = None
        if idade is not None and idade < LOCK_EXPIRA_SEG:
            print(f"[AVISO] Já existe uma execução do pipeline em andamento (lock criado há {int(idade)}s) — abortando esta execução para não escrever em cima dela.")
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

        step("1/8 - Carregando metas e vendas (Oracle + Excel)")
        try:
            import meta
            meta.tabela_vendas  # força o carregamento pesado aqui (lazy por padrão, ver meta.py)
        except Exception:
            print("[AVISO] meta falhou — metas_data.js não será atualizado, pipeline continua.")
            traceback.print_exc()

        step("2/8 - Exportando dashboard HTML (metas_data.js)")
        try:
            import exportacao_meta
        except Exception:
            print("[AVISO] exportacao_meta falhou — ignorado, pipeline continua.")
            traceback.print_exc()

        step("3/8 - Metas Gerais por estado/indústria (metas_gerais_data.js)")
        try:
            import subprocess, sys as _sys
            result = subprocess.run(
                [_sys.executable, "exportacao_metas_gerais.py"],
                capture_output=True, text=True, timeout=600
            )
            print(result.stdout)
            if result.returncode != 0:
                print("[AVISO] exportacao_metas_gerais falhou — ignorado, pipeline continua.")
                print(result.stderr)
        except Exception:
            print("[AVISO] exportacao_metas_gerais falhou — ignorado, pipeline continua.")
            traceback.print_exc()

        step("3b - Vendas por Indústria (industria_data.js)")
        try:
            import subprocess, sys as _sys
            result = subprocess.run(
                [_sys.executable, "exportacao_industria.py"],
                capture_output=True, text=True, timeout=600
            )
            print(result.stdout)
            if result.returncode != 0:
                print("[AVISO] exportacao_industria falhou — ignorado, pipeline continua.")
                print(result.stderr)
        except Exception:
            print("[AVISO] exportacao_industria falhou — ignorado, pipeline continua.")
            traceback.print_exc()

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

        step("4/8 - Campanha Robinson Crusoe (crusoe_data.js)")
        try:
            import subprocess, sys as _sys
            result = subprocess.run(
                [_sys.executable, "campanha_crusoe.py"],
                capture_output=True, text=True, timeout=600
            )
            print(result.stdout)
            if result.returncode != 0:
                print("[AVISO] campanha_crusoe falhou — ignorado, pipeline continua.")
                print(result.stderr)
        except Exception:
            print("[AVISO] campanha_crusoe falhou — ignorado, pipeline continua.")
            traceback.print_exc()

        step("4/8 - Conferência de preços")
        try:
            import conferencia_preco
        except Exception:
            print("[AVISO] conferencia_preco falhou — ignorado, pipeline continua.")
            traceback.print_exc()

        step("5/8 - Gerando página de entregas (entregas_data.js)")
        try:
            import entregas
        except Exception:
            print("[AVISO] entregas falhou — entregas_data.js não será atualizado, pipeline continua.")
            traceback.print_exc()

        step("5b - Gerando página de pedidos (pedidos_data.js)")
        try:
            import pedidos
        except Exception:
            print("[AVISO] pedidos falhou — pedidos_data.js não será atualizado, pipeline continua.")
            traceback.print_exc()

        step("5c - Agendamento CRC4: planilha (agendamento_data.js)")
        try:
            import subprocess, sys as _sys
            result = subprocess.run(
                [_sys.executable, "exportacao_agendamento.py"],
                capture_output=True, text=True, timeout=600
            )
            print(result.stdout)
            if result.returncode != 0:
                print("[AVISO] exportacao_agendamento falhou — ignorado, pipeline continua.")
                print(result.stderr)
        except Exception:
            print("[AVISO] exportacao_agendamento falhou — ignorado, pipeline continua.")
            traceback.print_exc()

        step("5d - Agendamento CRC4: pedidos por e-mail x faturado (agendamento_data.js)")
        try:
            import subprocess, sys as _sys
            result = subprocess.run(
                [_sys.executable, "email_pedidos.py"],
                capture_output=True, text=True, timeout=600
            )
            print(result.stdout)
            if result.returncode != 0:
                print("[AVISO] email_pedidos falhou — ignorado, pipeline continua.")
                print(result.stderr)
        except Exception:
            print("[AVISO] email_pedidos falhou — ignorado, pipeline continua.")
            traceback.print_exc()

        step("6/8 - Exportando dashboard SP (vendas_sp_data.js)")
        try:
            import subprocess, sys as _sys
            result = subprocess.run(
                [_sys.executable, "exportacao_sp.py"],
                capture_output=True, text=True, timeout=600
            )
            print(result.stdout)
            if result.returncode != 0:
                print("[AVISO] exportacao_sp falhou — SP ignorado, pipeline continua.")
                print(result.stderr)
        except Exception:
            print("[AVISO] exportacao_sp falhou — SP ignorado, pipeline continua.")
            traceback.print_exc()

        step("6b - Exportando dashboard ES (vendas_es_data.js)")
        try:
            import subprocess, sys as _sys
            result = subprocess.run(
                [_sys.executable, "exportacao_es.py"],
                capture_output=True, text=True, timeout=600
            )
            print(result.stdout)
            if result.returncode != 0:
                print("[AVISO] exportacao_es falhou — ES ignorado, pipeline continua.")
                print(result.stderr)
        except Exception:
            print("[AVISO] exportacao_es falhou — ES ignorado, pipeline continua.")
            traceback.print_exc()

        step("6c - Exportando dashboard MG (vendas_mg_data.js)")
        try:
            import subprocess, sys as _sys
            result = subprocess.run(
                [_sys.executable, "exportacao_mg.py"],
                capture_output=True, text=True, timeout=600
            )
            print(result.stdout)
            if result.returncode != 0:
                print("[AVISO] exportacao_mg falhou — MG ignorado, pipeline continua.")
                print(result.stderr)
        except Exception:
            print("[AVISO] exportacao_mg falhou — MG ignorado, pipeline continua.")
            traceback.print_exc()

        step("7/9 - Nao positivados SP (nao_pos_sp_data.js)")
        try:
            import subprocess, sys as _sys
            result = subprocess.run(
                [_sys.executable, "exportacao_nao_pos_sp.py"],
                capture_output=True, text=True, timeout=600
            )
            print(result.stdout)
            if result.returncode != 0:
                print("[AVISO] exportacao_nao_pos_sp falhou — ignorado, pipeline continua.")
                print(result.stderr)
        except Exception:
            print("[AVISO] exportacao_nao_pos_sp falhou — ignorado, pipeline continua.")
            traceback.print_exc()

        step("7b - Nao positivados ES (nao_pos_es_data.js)")
        try:
            import subprocess, sys as _sys
            result = subprocess.run(
                [_sys.executable, "exportacao_nao_pos_es.py"],
                capture_output=True, text=True, timeout=600
            )
            print(result.stdout)
            if result.returncode != 0:
                print("[AVISO] exportacao_nao_pos_es falhou — ignorado, pipeline continua.")
                print(result.stderr)
        except Exception:
            print("[AVISO] exportacao_nao_pos_es falhou — ignorado, pipeline continua.")
            traceback.print_exc()

        step("7c - Nao positivados MG (nao_pos_mg_data.js)")
        try:
            import subprocess, sys as _sys
            result = subprocess.run(
                [_sys.executable, "exportacao_nao_pos_mg.py"],
                capture_output=True, text=True, timeout=600
            )
            print(result.stdout)
            if result.returncode != 0:
                print("[AVISO] exportacao_nao_pos_mg falhou — ignorado, pipeline continua.")
                print(result.stderr)
        except Exception:
            print("[AVISO] exportacao_nao_pos_mg falhou — ignorado, pipeline continua.")
            traceback.print_exc()

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

        step("8/9 - Clientes migrados RCA 588 (clientes_588_data.js)")
        try:
            import subprocess, sys as _sys
            result = subprocess.run(
                [_sys.executable, "exportacao_588.py"],
                capture_output=True, text=True, timeout=600
            )
            print(result.stdout)
            if result.returncode != 0:
                print("[AVISO] exportacao_588 falhou — ignorado, pipeline continua.")
                print(result.stderr)
        except Exception:
            print("[AVISO] exportacao_588 falhou — ignorado, pipeline continua.")
            traceback.print_exc()

        step("8/9 - Exportando auth de vendedores (vendedores_auth_data.js)")
        try:
            import subprocess, sys as _sys
            result = subprocess.run(
                [_sys.executable, "exportacao_vendedores_auth.py"],
                capture_output=True, text=True, timeout=600
            )
            print(result.stdout)
            if result.returncode != 0:
                print("[AVISO] exportacao_vendedores_auth falhou — ignorado, pipeline continua.")
                print(result.stderr)
        except Exception:
            print("[AVISO] exportacao_vendedores_auth falhou — ignorado, pipeline continua.")
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
                    print(result.stderr.decode("utf-8", errors="replace"))
            except Exception:
                print("[AVISO] alerta_logistica_rj falhou — ignorado, pipeline continua.")
                traceback.print_exc()

            step("10/10 - Enviando alerta WhatsApp")
            try:
                import envio_whatsapp
            except Exception:
                print("[AVISO] envio_whatsapp falhou — ignorado, pipeline continua.")
                traceback.print_exc()
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
