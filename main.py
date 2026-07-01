import sys
import traceback
from datetime import datetime

def step(nome):
    print(f"\n{'-' * 50}")
    print(f"  {nome}")
    print(f"{'-' * 50}")

def main():
    inicio = datetime.now()
    print(f"\n{'='*50}")
    print(f"  OFFTRADE - {inicio.strftime('%d/%m/%Y %H:%M')}")
    print(f"{'='*50}")

    try:
        step("1/8 - Carregando metas e vendas (Oracle + Excel)")
        import meta

        step("2/8 - Exportando dashboard HTML (metas_data.js)")
        import exportacao_meta

        step("3/8 - Metas Gerais por estado/indústria (metas_gerais_data.js)")
        try:
            import subprocess, sys as _sys
            result = subprocess.run(
                [_sys.executable, "exportacao_metas_gerais.py"],
                capture_output=True, text=True
            )
            print(result.stdout)
            if result.returncode != 0:
                print("[AVISO] exportacao_metas_gerais falhou — ignorado, pipeline continua.")
                print(result.stderr)
        except Exception:
            print("[AVISO] exportacao_metas_gerais falhou — ignorado, pipeline continua.")
            traceback.print_exc()

        step("4/8 - Campanha Amarula (amarula_data.js)")
        try:
            import subprocess, sys as _sys
            result = subprocess.run(
                [_sys.executable, "exportacao_amarula.py"],
                capture_output=True, text=True
            )
            print(result.stdout)
            if result.returncode != 0:
                print("[AVISO] exportacao_amarula falhou — ignorado, pipeline continua.")
                print(result.stderr)
        except Exception:
            print("[AVISO] exportacao_amarula falhou — ignorado, pipeline continua.")
            traceback.print_exc()

        step("4/8 - Conferência de preços")
        import conferencia_preco

        step("5/8 - Gerando página de entregas (entregas_data.js)")
        import entregas

        step("6/8 - Exportando dashboard SP (vendas_sp_data.js)")
        try:
            import subprocess, sys as _sys
            result = subprocess.run(
                [_sys.executable, "exportacao_sp.py"],
                capture_output=True, text=True
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
                capture_output=True, text=True
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
                capture_output=True, text=True
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
                capture_output=True, text=True
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
                capture_output=True, text=True
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
                capture_output=True, text=True
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
                capture_output=True, text=True
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
                capture_output=True, text=True
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
                capture_output=True, text=True
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
                capture_output=True, text=True
            )
            print(result.stdout)
            if result.returncode != 0:
                print("[AVISO] exportacao_clientes_inativos falhou — ignorado, pipeline continua.")
                print(result.stderr)
        except Exception:
            print("[AVISO] exportacao_clientes_inativos falhou — ignorado, pipeline continua.")
            traceback.print_exc()

        step("10/10 - Alertas Logistica RJ (Gmail -> nao entregues)")
        try:
            import subprocess, sys as _sys
            result = subprocess.run(
                [_sys.executable, "alerta_logistica_rj.py"],
                capture_output=True
            )
            print(result.stdout.decode("utf-8", errors="replace"))
            if result.returncode != 0:
                print("[AVISO] alerta_logistica_rj falhou — ignorado, pipeline continua.")
                print(result.stderr.decode("utf-8", errors="replace"))
        except Exception:
            print("[AVISO] alerta_logistica_rj falhou — ignorado, pipeline continua.")
            traceback.print_exc()

        step("10/10 - Enviando alerta WhatsApp")
        import envio_whatsapp

        step("11/11 - Deploy para VPS")
        for script in ["deploy_vps.py"]:
            try:
                import subprocess, sys as _sys
                result = subprocess.run(
                    [_sys.executable, script],
                    capture_output=True, text=True
                )
                print(result.stdout)
                if result.returncode != 0:
                    print(f"[AVISO] {script} falhou — ignorado, pipeline continua.")
                    print(result.stderr)
            except Exception:
                print(f"[AVISO] {script} falhou — ignorado, pipeline continua.")
                traceback.print_exc()

    except Exception:
        print("\n[ERRO] Falha na execução:")
        traceback.print_exc()
        sys.exit(1)

    fim = datetime.now()
    duracao = (fim - inicio).seconds
    print(f"\n{'='*50}")
    print(f"  Concluído em {duracao}s")
    print(f"{'='*50}\n")

if __name__ == "__main__":  
    main()
