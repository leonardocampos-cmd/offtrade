"""
Scraping do Canhoto Digital (ComprovaFácil, softcenter.com.br) via Selenium.
Sem acesso à API oficial ainda — busca NF por NF no portal web e extrai o
status (ABERTO / CARREGADO / COMPROVADO, com sub-status tipo "ENTREGA
TOTAL" quando COMPROVADO). Usado pra alimentar o status_log de pedidos SP
em pedidos.py, equivalente ao que a planilha "Controle de Notas" (RJ) já
faz pros pedidos CRC.

Cache em canhoto_status.json: uma vez que uma NF chega em COMPROVADO
(status terminal — não muda mais), não é reconsultada nas próximas
execuções. Evita rebuscar milhares de NFs toda hora.
"""
import json
import os
import time
from datetime import datetime
from pathlib import Path

from selenium import webdriver
from selenium.webdriver.edge.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

LOGIN_URL = "https://canhotodigital.softcenter.com.br/entrar"
CACHE_PATH = Path(__file__).parent / "canhoto_status.json"
STATUS_TERMINAL = {"COMPROVADO"}
EDGE_BINARY = os.getenv("EDGE_BINARY", r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe")


def _novo_driver():
    opts = Options()
    opts.add_argument("--headless=new")
    opts.add_argument("--window-size=1400,900")
    if os.path.exists(EDGE_BINARY):
        opts.binary_location = EDGE_BINARY
    return webdriver.Edge(options=opts)


def _login(driver):
    usuario = os.getenv("CANHOTO_DIGITAL_USER")
    senha = os.getenv("CANHOTO_DIGITAL_PASS")
    if not usuario or not senha:
        raise RuntimeError("CANHOTO_DIGITAL_USER/CANHOTO_DIGITAL_PASS não configurados no .env")
    driver.get(LOGIN_URL)
    driver.find_element(By.NAME, "email").send_keys(usuario)
    driver.find_element(By.NAME, "password").send_keys(senha)
    driver.find_element(By.CSS_SELECTOR, "button[type=submit]").click()
    time.sleep(4)
    if "entrar" in driver.current_url:
        raise RuntimeError("Login no Canhoto Digital falhou — checar CANHOTO_DIGITAL_USER/PASS no .env.")


_COLUNAS = {
    "ABERTO":     "kanban-column-open",
    "CARREGADO":  "kanban-column-loaded",
    "COMPROVADO": "kanban-column-proved",
}


def _buscar_nf(driver, nf: str) -> dict | None:
    """Busca uma NF e retorna {'status', 'sub_status', 'documento', 'info'}
    ou None se não encontrada em nenhuma das 3 colunas (ABERTO/CARREGADO/
    COMPROVADO). Usa data-testid/data-slot do board (kanban), mais estável
    que recortar texto solto da página."""
    campo = driver.find_element(By.NAME, "document")
    campo.clear()
    campo.send_keys(nf)
    campo.send_keys(Keys.ENTER)
    time.sleep(1.8)

    for status, testid in _COLUNAS.items():
        try:
            coluna = driver.find_element(By.CSS_SELECTOR, f'[data-testid="{testid}"]')
        except Exception:
            continue
        cards = coluna.find_elements(By.CSS_SELECTOR, '[data-slot="sheet-trigger"]')
        if not cards:
            continue
        card = cards[0]
        try:
            sub_status = card.find_element(By.CSS_SELECTOR, '[data-slot="card-header"] span').text.strip()
        except Exception:
            sub_status = ""
        spans = card.find_elements(By.CSS_SELECTOR, '[data-slot="card-content"] span')
        documento = spans[0].text.strip() if spans else ""
        info = spans[1].text.strip() if len(spans) > 1 else ""
        return {"status": status, "sub_status": sub_status, "documento": documento, "info": info}

    return None


def buscar_status_lote(nfs: list) -> dict:
    """Busca status de uma lista de NFs, usando cache local pra pular as já
    finalizadas (COMPROVADO). Retorna {nf: {...}} pras NFs encontradas."""
    cache = {}
    if CACHE_PATH.exists():
        try:
            cache = json.loads(CACHE_PATH.read_text(encoding="utf-8"))
        except Exception:
            cache = {}

    nfs_unicas = sorted({str(nf).strip() for nf in nfs if nf and str(nf).strip()})
    pendentes = [
        nf for nf in nfs_unicas
        if nf not in cache or cache[nf].get("status") not in STATUS_TERMINAL
    ]

    if not pendentes:
        print("Canhoto Digital: nada pendente, tudo já em cache (COMPROVADO).")
        return {nf: cache[nf] for nf in nfs_unicas if nf in cache}

    print(f"Canhoto Digital: {len(pendentes)} NF(s) pendente(s) de consulta (de {len(nfs_unicas)} única(s)).")
    driver = _novo_driver()
    try:
        _login(driver)
        for i, nf in enumerate(pendentes, 1):
            try:
                resultado = _buscar_nf(driver, nf)
            except Exception as e:
                print(f"  [AVISO] falha ao buscar NF {nf}: {str(e)[:100]}")
                continue
            if resultado:
                resultado["atualizado_em"] = datetime.now().strftime("%d/%m/%Y %H:%M")
                cache[nf] = resultado
            if i % 20 == 0:
                print(f"  {i}/{len(pendentes)} consultada(s)...")
                CACHE_PATH.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")
    finally:
        driver.quit()

    CACHE_PATH.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Canhoto Digital: cache salvo em {CACHE_PATH} ({len(cache)} NF(s) no total).")
    return {nf: cache[nf] for nf in nfs_unicas if nf in cache}


def _nfs_sp_de_pedidos_data() -> list:
    """Lê pedidos_data.js e extrai as NFs de pedidos faturados no sistema
    SPON (única empresa coberta pelo Canhoto Digital até agora)."""
    import re
    caminho = Path(__file__).parent / "pedidos_data.js"
    if not caminho.exists():
        print("[AVISO] pedidos_data.js não encontrado — rode pedidos.py primeiro.")
        return []
    texto = caminho.read_text(encoding="utf-8")
    m = re.search(r"const PEDIDOS_DATA\s*=\s*(\{.*\});", texto, re.DOTALL)
    if not m:
        return []
    dados = json.loads(m.group(1))
    return [
        p["numnota"] for p in dados.get("faturados", [])
        if p.get("sistema") == "SPON" and p.get("numnota")
    ]


if __name__ == "__main__":
    import sys
    nfs = sys.argv[1:] or _nfs_sp_de_pedidos_data()
    if not nfs:
        print("Nenhuma NF de SP para consultar.")
    else:
        resultado = buscar_status_lote(nfs)
        print(f"OK - {len(resultado)} NF(s) com status no cache.")
