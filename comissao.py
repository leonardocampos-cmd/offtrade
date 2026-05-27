#logica

peso_atig = 0.5
atingimento = 10
total_atig = peso_atig * atingimento if peso_atig * atingimento > peso_atig else peso_atig * atingimento
total_liquidados = 40000
comissao = (total_atig * total_liquidados)/100
comissao = round(comissao, 2)
print(f"Total atingimento: {total_atig}%")
print(f"Total comissão: R$ {comissao}")

#BANCO DE DADOS
#ROTINA_1048

#ATINGIMRNTO: DATA DE EMISSÃO DA NOTA X DATA DE LIQUIDAÇÃO DO PEDIDO