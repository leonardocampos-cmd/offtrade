"""
Faz commit e push dos arquivos gerados para o GitHub.
GitHub Pages serve automaticamente o branch master.
"""
import subprocess
import sys
from datetime import datetime

def deploy():
    data = datetime.now().strftime("%d/%m/%Y %H:%M")

    cmds = [
        ["git", "add",
         "metas_data.js", "vendas_data.js", "vendas_sp_data.js",
         "entregas_data.js", "amarula_data.js",
         "clientes_rca_data.js", "vendedores_auth_data.js",
         "login_vendedor.html", "vendedor.html", "auth_vendedor.js",
         "login.html", "auth.js",
         "index.html", "metas.html", "sp.html", "entregas.html",
         "amarula.html", "pernod.html", "clientes_rca.html", "moving.html",
        ],
        ["git", "commit", "--allow-empty", "-m", f"Auto-update {data}"],
        ["git", "push", "origin", "master"],
    ]

    for cmd in cmds:
        print(f"-> {' '.join(cmd)}")
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.stdout.strip():
            print(r.stdout.strip())
        if r.returncode != 0:
            if "nothing to commit" in r.stderr or "nothing to commit" in r.stdout:
                print("   (nenhuma alteração)")
                continue
            print(f"[ERRO] {r.stderr.strip()}")
            sys.exit(1)

    print("OK GitHub Pages atualizado — https://leonardocampos-cmd.github.io/offtrade/")

if __name__ == "__main__":
    deploy()
