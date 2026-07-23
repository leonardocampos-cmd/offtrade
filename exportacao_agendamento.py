"""
Gera a aba "Planilha de Agendamento" de agendamento_data.js a partir de
CONTROLE AGENDAMENTOS.xlsx (Drive) — controle manual de agendamento de
entrega por NF (cliente, CNPJ, valor, data de agendamento, status). Só a
filial 4 da CRC (ANTIGO SISTEMA = 'CRC - 4') entra nesta página; as demais
filiais/empresas da planilha ficam de fora por enquanto.

Roda como step independente de email_pedidos.py (que gera a outra aba,
"Pedidos por E-mail x Faturado") — cada um faz merge no mesmo
agendamento_data.js sem sobrescrever o que o outro já gravou.
"""
import json
import re
import subprocess
from datetime import datetime
from pathlib import Path

import pandas as pd

import baixar_planilhas_drive as _bpd

DATA_PATH = Path(__file__).parent / 'agendamento_data.js'


def _caminho_local_fallback() -> str:
    return r"G:\Drives compartilhados\Off Trade\CONTROLE AGENDAMENTOS.xlsx"


def _parse_valor(v) -> float:
    """A coluna VALOR mistura formatos na planilha: string tipo 'R$ 1.234,56'
    (formato BR, precisa trocar . por milhar e , por decimal) e número puro
    já convertido pelo Excel/pandas (ex: 394.19999999999993, erro de ponto
    flutuante). Tratar os dois como string cegamente destrói o segundo caso
    (o replace('.', '') vira '39419999999999993' — bug confirmado em
    2026-07-23, gerou 'Valor Agendado' de R$ 209 quatrilhões no KPI)."""
    if pd.isna(v):
        return 0.0
    if isinstance(v, (int, float)):
        return round(float(v), 2)
    limpo = re.sub(r'[^\d,.-]', '', str(v)).replace('.', '').replace(',', '.')
    try:
        return float(limpo)
    except ValueError:
        return 0.0


def _s(v) -> str:
    return '' if pd.isna(v) else str(v).strip()


def _cod(v) -> str:
    """Código de cliente vem como float por causa de NaN na coluna (ex: 6940.0)."""
    if pd.isna(v):
        return ''
    try:
        return str(int(float(v)))
    except (TypeError, ValueError):
        return str(v).strip()


def _merge_write(patch: dict):
    existing = {}
    if DATA_PATH.exists():
        raw = DATA_PATH.read_text(encoding='utf-8')
        m = re.search(r'=\s*(\{.*\});\s*$', raw.strip(), re.DOTALL)
        if m:
            try:
                existing = json.loads(m.group(1))
            except json.JSONDecodeError:
                existing = {}
    existing.update(patch)
    existing['atualizado_em'] = datetime.now().strftime('%d/%m/%Y %H:%M')
    with open(DATA_PATH, 'w', encoding='utf-8') as f:
        f.write(f"const AGENDAMENTO_DATA = {json.dumps(existing, ensure_ascii=False, indent=2)};\n")


SHEETS = ['ANTIGO', 'NOVO']  # 'RCA' (lookup) e 'INFO' não são dados de agendamento


def main():
    caminho = _bpd.com_fallback(_bpd.caminho_controle_agendamentos, _caminho_local_fallback())

    # O arquivo tem 4 abas — ANTIGO e NOVO são conjuntos de dados diferentes,
    # sem NFs em comum (não é uma sobrescrevendo a outra), então as duas
    # precisam ser lidas e somadas pra não perder ~875 linhas CRC-4 que só
    # existem na aba NOVO (confirmado em 2026-07-23: 0 NF em comum entre elas).
    partes = []
    for sheet in SHEETS:
        df_sheet = pd.read_excel(caminho, sheet_name=sheet)
        df_sheet.columns = [str(c).strip().upper() for c in df_sheet.columns]
        print(f"Aba '{sheet}': {len(df_sheet)} linha(s), colunas: {df_sheet.columns.tolist()}")
        partes.append(df_sheet)
    df = pd.concat(partes, ignore_index=True)

    df['_SISTEMA_NORM'] = df.get('SISTEMA', '').fillna('').astype(str).str.strip().str.upper()
    df_crc4 = df[df['_SISTEMA_NORM'] == 'CRC - 4'].copy()

    df_crc4['VALOR_NUM'] = df_crc4['VALOR'].apply(_parse_valor)
    df_crc4['DATA_DT']   = pd.to_datetime(df_crc4['DATA AGENDAMENTO'], errors='coerce')
    df_crc4['STATUS']    = df_crc4['STATUS'].fillna('').astype(str).str.strip().str.upper()
    df_crc4['STATUS LOGISTICA'] = df_crc4.get('STATUS LOGISTICA', '').fillna('').astype(str).str.strip().str.upper()
    df_crc4['VENDEDOR']  = df_crc4['VENDEDOR'].fillna('').astype(str).str.strip()

    vendedores_out = []
    for vendedor, grp in df_crc4.groupby('VENDEDOR', sort=False):
        itens = []
        for _, r in grp.iterrows():
            data_dt = r['DATA_DT']
            itens.append({
                'codigo_cliente':          _cod(r.get('CÓDIGO', '')),
                'cliente':                 _s(r.get('CLIENTE', '')),
                'cnpj':                    _s(r.get('CNPJ', '')),
                'filial':                  _s(r.get('FILIAL', '')),
                'valor':                   round(float(r['VALOR_NUM']), 2),
                'nf':                      _s(r.get('NF', '')),
                'data_agendamento':        data_dt.strftime('%d/%m/%Y') if pd.notna(data_dt) else '',
                'data_ord':                data_dt.strftime('%Y-%m-%d') if pd.notna(data_dt) else '',
                'status':                  _s(r['STATUS']),
                'obs':                     _s(r.get('OBS', '')),
                'status_logistica':        _s(r['STATUS LOGISTICA']),
                'justificativa_logistica': _s(r.get('JUSTIFICATIVA LOGISTICA', '')),
            })
        itens.sort(key=lambda i: i['data_ord'], reverse=True)
        vendedores_out.append({'nome': vendedor or 'Sem Vendedor', 'itens': itens})

    vendedores_out.sort(key=lambda v: v['nome'])

    _merge_write({'agendamentos': vendedores_out})
    print(f"OK - {len(vendedores_out)} vendedor(es), {len(df_crc4)} linha(s) CRC-4 -> {DATA_PATH}")

    repo_dir = str(Path(__file__).parent)
    try:
        subprocess.run(["git", "-C", repo_dir, "add", "agendamento_data.js"], check=True)
        subprocess.run(["git", "-C", repo_dir, "commit", "-m",
                        f"Atualiza agendamento_data.js (planilha) - {datetime.now().strftime('%d/%m/%Y')}"])
        subprocess.run(["git", "-C", repo_dir, "push", "origin", "master"], check=True)
        print("OK agendamento_data.js enviado ao GitHub Pages.")
    except subprocess.CalledProcessError:
        print("[AVISO] git push falhou — ignorado, pipeline continua.")


if __name__ == "__main__":
    main()
