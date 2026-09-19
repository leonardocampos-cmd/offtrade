"""
Gera controle_agendamento_data.js a partir da aba "controle" de
"CONTROLE DE AGEND. geovanna.xlsx" (Drive) — pedido do usuário em
2026-09-18: substitui a antiga aba "Planilha de Agendamento" de
agendamento.html (que vinha de outra planilha, CONTROLE AGENDAMENTOS.xlsx,
ver exportacao_agendamento.py) por uma tabela editável direto no site,
espelhando a aba "controle" tal como ela é.

Colunas são lidas DINAMICAMENTE do cabeçalho da aba (ignora as 3 primeiras
colunas, sem nome) em vez de hardcoded — a planilha já mudou de estrutura
sozinha entre duas leituras feitas em sequência nesta mesma tarde
(coluna "STATUS LOGI" sumiu do cabeçalho), então qualquer lista fixa de
nomes quebraria na próxima edição de quem mantém a planilha.

Só ADICIONA linha nova — pedido explícito do usuário em 2026-09-18:
edição feita no site fica só no site (controle_agendamento.json via
pedidos_mercos_api.py), nunca grava de volta nesta planilha. Dedup por
fingerprint (hash das colunas-chave no momento da importação) pra não
duplicar a mesma linha a cada rodada do cron — depois de importada, uma
linha nunca é sobrescrita/removida por este script, mesmo que os valores
dela mudem na planilha original.
"""
import hashlib
import json
import os
import subprocess
from datetime import datetime
from pathlib import Path

import pandas as pd

import baixar_planilhas_drive as _bpd

HERE = Path(__file__).parent
OUT_JS = HERE / "controle_agendamento_data.js"
STATE_JSON = HERE / "controle_agendamento.json"

SHEET = "controle"


def _caminho_local_fallback() -> str:
    return r"G:\Drives compartilhados\Off Trade\CONTROLE DE AGEND. geovanna.xlsx"


def _s(v) -> str:
    if pd.isna(v):
        return ""
    if isinstance(v, (pd.Timestamp, datetime)):
        return v.strftime("%d/%m/%Y")
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v).strip()


def _fingerprint(valores: dict) -> str:
    # A LINHA INTEIRA vira a chave — a planilha real tem centenas de linhas
    # com conteúdo idêntico linha a linha (confirmado em 2026-09-18: 1239
    # linhas não-vazias, só 510 fingerprints distintos por linha inteira),
    # então uma chave parcial (ex: só COD+CNPJ+VALOR) colidiria ainda mais e
    # descartaria linhas genuinamente diferentes por engano.
    bruto = "|".join(f"{k}={v}" for k, v in sorted(valores.items()))
    return hashlib.sha1(bruto.encode("utf-8")).hexdigest()


def _carregar_estado() -> dict:
    if STATE_JSON.exists():
        try:
            return json.loads(STATE_JSON.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"linhas": {}, "prox_id": 1}


def _salvar_estado(estado: dict):
    tmp = STATE_JSON.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(estado, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, STATE_JSON)


def main():
    caminho = _bpd.com_fallback(_bpd.caminho_controle_agend_geovanna, _caminho_local_fallback())
    df = pd.read_excel(caminho, sheet_name=SHEET)

    colunas = [str(c).strip() for c in df.columns if not str(c).startswith("Unnamed") and str(c).strip()]

    estado = _carregar_estado()
    linhas = estado.setdefault("linhas", {})
    # Chave de dedupe real: (fingerprint da linha, nº de ocorrências iguais
    # já vistas ANTES dela no arquivo) — não só o fingerprint sozinho, senão
    # duas linhas idênticas de verdade (existem várias na planilha real)
    # colapsariam numa só. Isso faz cada CÓPIA adicional de uma linha
    # repetida ser tratada como "uma nova ocorrência", preservando a
    # contagem exata de linhas da planilha.
    identidades_existentes = {
        (v.get("_fingerprint"), v.get("_ocorrencia", 0))
        for v in linhas.values() if v.get("_fingerprint") is not None
    }
    prox_id = estado.get("prox_id", 1)
    novas = 0
    ocorrencias_nesta_leitura = {}

    for _, r in df.iterrows():
        valores = {}
        for c in df.columns:
            nome = str(c).strip()
            if nome.startswith("Unnamed") or not nome:
                continue
            valores[nome] = _s(r[c])
        if not any(valores.values()):
            continue  # linha em branco (sobra no fim da planilha)

        fp = _fingerprint(valores)
        ocorrencia = ocorrencias_nesta_leitura.get(fp, 0)
        ocorrencias_nesta_leitura[fp] = ocorrencia + 1

        if (fp, ocorrencia) in identidades_existentes:
            continue

        linha_id = str(prox_id)
        prox_id += 1
        valores["_fingerprint"] = fp
        valores["_ocorrencia"] = ocorrencia
        valores["_criado_em"] = datetime.now().strftime("%d/%m/%Y %H:%M")
        linhas[linha_id] = valores
        identidades_existentes.add((fp, ocorrencia))
        novas += 1

    estado["prox_id"] = prox_id
    estado["colunas"] = colunas
    _salvar_estado(estado)

    payload = {
        "atualizado_em": datetime.now().strftime("%d/%m/%Y %H:%M"),
        "colunas": colunas,
        "linhas": linhas,
    }
    tmp = OUT_JS.with_suffix(".js.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        f.write("const CONTROLE_AGENDAMENTO_DATA = ")
        json.dump(payload, f, ensure_ascii=False)
        f.write(";\n")
    os.replace(tmp, OUT_JS)
    print(f"OK - {novas} linha(s) nova(s), {len(linhas)} no total -> {OUT_JS}")

    repo_dir = str(HERE)
    try:
        subprocess.run(["git", "-C", repo_dir, "add", "controle_agendamento_data.js"], check=True)
        subprocess.run(["git", "-C", repo_dir, "commit", "-m",
                        f"Atualiza controle_agendamento_data.js - {datetime.now().strftime('%d/%m/%Y %H:%M')}"])
        subprocess.run(["git", "-C", repo_dir, "push", "origin", "master"], check=True)
        print("OK controle_agendamento_data.js enviado ao GitHub Pages.")
    except subprocess.CalledProcessError:
        print("[AVISO] git push falhou — ignorado, pipeline continua.")

    _publicar_static()


# ── Publica direto em /opt/offtrade-static (mesmo motivo de
# exportacao_agendamento.py::_publicar_static) ──────────────────────────────
def _publicar_static():
    if os.getenv("OFFTRADE_RUNTIME", "local") != "vps":
        return
    import shutil
    destino = "/opt/offtrade-static"
    if not OUT_JS.exists():
        return
    tmp = os.path.join(destino, ".controle_agendamento_data.js.tmp_publish")
    shutil.copy(OUT_JS, tmp)
    os.replace(tmp, os.path.join(destino, "controle_agendamento_data.js"))
    print(f"OK - controle_agendamento_data.js copiado para {destino}")


if __name__ == "__main__":
    main()
