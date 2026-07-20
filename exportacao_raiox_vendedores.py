"""
Gera raiox_vendedores_data.js — estrutura das equipes de campo OFF TRADE
(RJ): times (Key Account / Atacarejo / Convenience / Outros), quantidade
de vendedores por time e distribuição de clientes atendidos por região
(cidade).
"""
import json
import subprocess
from datetime import date, datetime
from pathlib import Path

from meta import engine, carregar_dados

# RCA -> (nome, time). Fonte: campanha_crusoe.py — lista oficial confirmada
# com o usuário em 08/07/2026 (ver memória project-campanhas-times).
TIMES = {
    275: ("Maria Luiza", "KEY_ACCOUNT"),
    158: ("Jose Marcelo Cardoso", "KEY_ACCOUNT"),
    144: ("Diogo Raposo", "ATACAREJO"),
    153: ("Angelo Neves Suzart", "ATACAREJO"),
    412: ("Barbara Cabral", "ATACAREJO"),
    419: ("Natali de Oliveira", "ATACAREJO"),
    439: ("Mateus Cardoso", "ATACAREJO"),
    450: ("Leandro Souza", "ATACAREJO"),
    471: ("Ana Clara Fassano", "ATACAREJO"),
    156: ("Marilena Tragel", "CONVENIENCE"),
    378: ("Fabio Valotti", "CONVENIENCE"),
    379: ("Jorge Maciel", "CONVENIENCE"),
    431: ("Adeilson Gonçalvez", "CONVENIENCE"),
}

TIME_LABEL = {
    "KEY_ACCOUNT": "Key Account",
    "ATACAREJO": "Atacarejo (Pequeno e médio varejo)",
    "CONVENIENCE": "Convenience (Varejo tradicional e Conveniência)",
    "OUTROS": "Outros / sem time definido",
}

vendedores_off_trade = carregar_dados("""
    SELECT CODUSUR, NOME FROM CRC.PCUSUARI WHERE NOME LIKE '%OFF TRADE%' AND ESTADO = 'RJ'
""", engine, "raiox_vendedores_off_trade")
vendedores_off_trade.columns = vendedores_off_trade.columns.str.upper()
_nomes_por_rca = {
    int(r['CODUSUR']): r['NOME'].replace('- OFF TRADE', '').replace('-OFF TRADE', '').strip()
    for _, r in vendedores_off_trade.iterrows()
}
todos_rcas = sorted(_nomes_por_rca)

clientes = carregar_dados("""
    SELECT C.CODCLI, COALESCE(C.MUNICENT,'') CIDADE, C.CODUSUR1, C.CODUSUR2
    FROM CRC.PCCLIENT C
    LEFT JOIN CRC.PCUSUARI U1 ON C.CODUSUR1 = U1.CODUSUR
    LEFT JOIN CRC.PCUSUARI U2 ON C.CODUSUR2 = U2.CODUSUR
    WHERE (U1.NOME LIKE '%OFF TRADE%' OR U2.NOME LIKE '%OFF TRADE%')
""", engine, "raiox_vendedores_clientes")
clientes.columns = clientes.columns.str.upper()
clientes['CIDADE'] = clientes['CIDADE'].fillna('').str.strip()
for col in ('CODUSUR1', 'CODUSUR2'):
    clientes[col] = clientes[col].apply(lambda v: int(v) if str(v).strip().replace('.0', '').isdigit() else None)


def _clientes_do_rca(rca):
    return clientes[(clientes['CODUSUR1'] == rca) | (clientes['CODUSUR2'] == rca)]


times = {}
for rca in todos_rcas:
    nome, time_key = TIMES.get(rca, (_nomes_por_rca.get(rca, f"RCA {rca}"), "OUTROS"))
    cli_rca = _clientes_do_rca(rca)
    cidades = (
        cli_rca.groupby('CIDADE')['CODCLI'].nunique()
        .sort_values(ascending=False)
    )
    vendedor = {
        'rca': int(rca),
        'nome': nome,
        'total_clientes': int(cli_rca['CODCLI'].nunique()),
        'cidades': [
            {'cidade': c or 'N/D', 'clientes': int(n)}
            for c, n in cidades.items()
        ],
    }
    times.setdefault(time_key, []).append(vendedor)

resultado_times = []
for time_key in ["KEY_ACCOUNT", "ATACAREJO", "CONVENIENCE", "OUTROS"]:
    vendedores = sorted(times.get(time_key, []), key=lambda v: v['nome'])
    total_clientes_time = sum(v['total_clientes'] for v in vendedores)
    # Regiões cobertas pelo time inteiro (soma das cidades de todos os vendedores)
    regioes = {}
    for v in vendedores:
        for c in v['cidades']:
            regioes[c['cidade']] = regioes.get(c['cidade'], 0) + c['clientes']
    regioes_ordenadas = sorted(regioes.items(), key=lambda kv: kv[1], reverse=True)

    resultado_times.append({
        'time': time_key,
        'label': TIME_LABEL[time_key],
        'qtd_vendedores': len(vendedores),
        'total_clientes': total_clientes_time,
        'vendedores': vendedores,
        'regioes': [{'cidade': c or 'N/D', 'clientes': n} for c, n in regioes_ordenadas],
    })

payload = {
    'atualizado_em': datetime.now().strftime('%d/%m/%Y %H:%M'),
    'times': resultado_times,
}

out_path = Path(__file__).parent / "raiox_vendedores_data.js"
with open(out_path, 'w', encoding='utf-8') as f:
    f.write(f"// Gerado automaticamente\nconst RAIOX_VENDEDORES_DATA = {json.dumps(payload, ensure_ascii=False, indent=2)};\n")

total_vend = sum(t['qtd_vendedores'] for t in resultado_times)
print(f"OK raiox_vendedores_data.js — {total_vend} vendedores em {len(resultado_times)} times")

repo_dir = str(Path(__file__).parent)
subprocess.run(["git", "-C", repo_dir, "add", "raiox_vendedores_data.js", "raiox_vendedores.html"], check=False)
subprocess.run(["git", "-C", repo_dir, "commit", "-m",
                f"Atualiza raiox_vendedores_data.js - {date.today().strftime('%d/%m/%Y')}"])
subprocess.run(["git", "-C", repo_dir, "push", "origin", "master"], check=False)
print("OK GitHub Pages atualizado.")
