"""
Gera status_paginas_data.js: varre todo *_data.js da raiz, extrai o
"atualizado_em" (ou o comentário "Gerado em" nos poucos arquivos que não
têm esse campo no payload) e classifica cada página por há quanto tempo foi
gerada. Existe pra responder "alguma página parou de atualizar sem
ninguém notar?" — confirmado em 2026-07-23 que nao_pos_sp_data.js estava 6
dias parado (etapa do main.py falhando silenciosamente) sem nenhum alerta.

Só as páginas em PAGINAS_MANUAIS (raiox_*, catálogo, amarula, clientes
inativos por nome) não fazem parte do ciclo horário do main.py — ficam
marcadas como "Manual" (neutro) em vez de Crítico, pra não gerar alarme
falso num dado que é normal ficar dias sem atualizar.

PAGINAS_VPS_ONLY (metas/vendas/fontes_status, geradas por exportacao_meta.py
e afins) rodam num cron próprio só na VPS desde 2026-08-05 (a cada 15min,
ver main.py) — no PC local (OFFTRADE_RUNTIME != "vps") esses arquivos nunca
mais atualizam por design, então também viram "Manual" aqui pra não repetir
o falso alarme "Crítico" toda hora (confirmado em 2026-08-07: local dizia
29h parado enquanto a cópia na VPS tinha 8min). Na própria VPS
(OFFTRADE_RUNTIME == "vps") continuam classificadas normalmente, porque lá
é onde a staleness de verdade importa.
"""
import json
import os
import re
import subprocess
from datetime import datetime
from pathlib import Path

BASE = Path(__file__).parent
OUT_PATH = BASE / "status_paginas_data.js"

# Rodam fora do ciclo horário do main.py (manual/esporádico) — não alertar
# como crítico só por estarem "velhas".
PAGINAS_MANUAIS = {
    "raiox_clientes_data.js", "raiox_vendedores_data.js", "raiox_industrias_data.js",
    "raiox_cliente_detalhe_data.js", "raiox_vendedor_detalhe_data.js",
    "raiox_industria_detalhe_data.js",
    "catalogo_data.js", "amarula_data.js", "clientes_inativos_nome_data.js",
}

PAGINAS_VPS_ONLY = {
    "metas_data.js", "vendas_data.js", "vendas_es_data.js",
    "vendas_mg_data.js", "vendas_sp_data.js", "fontes_status_data.js",
}
if os.getenv("OFFTRADE_RUNTIME", "local") != "vps":
    PAGINAS_MANUAIS = PAGINAS_MANUAIS | PAGINAS_VPS_ONLY

# Excluídos por não serem payload de página (dado de apoio consumido por
# outras páginas, sem timestamp próprio relevante) ou por serem gerados só
# na VPS (log do nginx não existe localmente).
EXCLUIR = {"fontes_alert.js", "gerentes_data.js", "acessos_data.js"}

_TS_RE_JSON = re.compile(r'"atualizado_em"\s*:\s*"([^"]+)"')
_TS_RE_COMENTARIO = re.compile(r'//\s*Gerado em\s*([\d/: ]+)')


def _extrair_timestamp(texto: str):
    m = _TS_RE_JSON.search(texto) or _TS_RE_COMENTARIO.search(texto)
    if not m:
        return None
    try:
        return datetime.strptime(m.group(1).strip(), "%d/%m/%Y %H:%M")
    except ValueError:
        return None


def _classificar(idade_horas: float, manual: bool) -> str:
    if manual:
        return "Manual"
    if idade_horas < 3:
        return "OK"
    if idade_horas < 12:
        return "Atenção"
    return "Crítico"


def main():
    agora = datetime.now()
    paginas = []

    for arq in sorted(BASE.glob("*_data.js")):
        if arq.name in EXCLUIR:
            continue
        texto = arq.read_text(encoding="utf-8", errors="replace")
        ts = _extrair_timestamp(texto)
        manual = arq.name in PAGINAS_MANUAIS
        if ts is None:
            paginas.append({
                "arquivo": arq.name, "atualizado_em": "", "idade_horas": None,
                "status": "Sem timestamp", "manual": manual,
            })
            continue
        idade_horas = round((agora - ts).total_seconds() / 3600, 1)
        paginas.append({
            "arquivo": arq.name,
            "atualizado_em": ts.strftime("%d/%m/%Y %H:%M"),
            "idade_horas": idade_horas,
            "status": _classificar(idade_horas, manual),
            "manual": manual,
        })

    paginas.sort(key=lambda p: (p["idade_horas"] is None, -(p["idade_horas"] or 0)))

    criticos = [p["arquivo"] for p in paginas if p["status"] == "Crítico"]
    payload = {
        "atualizado_em": agora.strftime("%d/%m/%Y %H:%M"),
        "paginas": paginas,
        "total_criticos": len(criticos),
    }

    with open(OUT_PATH, "w", encoding="utf-8") as f:
        f.write(f"const STATUS_PAGINAS_DATA = {json.dumps(payload, ensure_ascii=False, indent=2)};\n")

    print(f"OK - {len(paginas)} página(s) verificada(s), {len(criticos)} crítica(s) -> {OUT_PATH}")
    if criticos:
        print(f"[AVISO] Páginas críticas (>12h sem atualizar): {', '.join(criticos)}")

    repo_dir = str(BASE)
    try:
        subprocess.run(["git", "-C", repo_dir, "add", "status_paginas_data.js"], check=True)
        subprocess.run(["git", "-C", repo_dir, "commit", "-m",
                        f"Atualiza status_paginas_data.js - {agora.strftime('%d/%m/%Y')}"])
        subprocess.run(["git", "-C", repo_dir, "push", "origin", "master"], check=True)
        print("OK status_paginas_data.js enviado ao GitHub Pages.")
    except subprocess.CalledProcessError:
        print("[AVISO] git push falhou — ignorado, pipeline continua.")


if __name__ == "__main__":
    main()
