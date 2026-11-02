# Avaliação funcional

Os cenários são propostas para revisão do especialista; não foram usados como casos aprovados na memória. Cada execução deve registrar estado inicial real, modelo, versão do prompt/catálogo, fontes recuperadas, saída, duração, tokens, intervenções e julgamento humano. Não assumir que o estado demonstrativo do HCM coincide com o cenário.

Separar por família: não colocar paráfrases do mesmo problema em memória de exemplos e teste cego. Comparar A (pedido + catálogo + dados atuais) com B (mesmo modelo e entradas + casos recuperados). Repetir amostras para observar variabilidade. Classificar entendimento, parâmetros/valores, dependências, preservação, necessidade de dúvida e correção completa. Contar também propostas indevidas e falhas de acesso, inclusive quando o backend as bloqueia. Registrar respostas impossíveis como esclarecimento/limitação esperada, não incentivar alterações.

Com preço conhecido: custo = tokens de entrada × tarifa + tokens de saída × tarifa, acrescido de recuperação/operação e tempo humano. Sem preço, reportar consumo e custo desconhecido. Os dados futuros de treinamento devem ser curados dos casos validados; guardar o teste cego fora de qualquer contexto de treinamento/RAG.
