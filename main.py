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
        step("1/4 - Carregando metas e vendas (Oracle + Excel)")
        import meta

        step("2/4 - Exportando dashboard HTML (metas_data.js)")
        import exportacao_meta

        step("3/4 - Conferência de preços")
        import conferencia_preco

        step("4/4 - Enviando alerta WhatsApp")
        import envio_whatsapp

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
