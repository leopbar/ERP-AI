# Acompanhamento visual do atendimento

O card acima da solicitação apresenta 12 etapas, distinguindo agentes de IA,
rotinas automáticas e decisões humanas. Antes do pedido, é um mapa explicativo;
em um atendimento, usa o estado confirmado e os registros das etapas internas.

## Por dentro da etapa

Selecionar uma etapa abre, dentro do próprio mapa e logo abaixo da fileira dela,
uma linha de paradas numeradas, como estações de metrô. A numeração recomeça em
cada etapa. Uma chamada de IA vira quatro paradas: o agente pergunta (com o mesmo
desenho do mapa), o formulário da chamada (com o nome do modelo), aguardando o
modelo e a conferência da resposta; as rotinas de consulta e execução viram
paradas com ícones. O estado de
cada parada vem do registro mais recente: concluída, em andamento, aguardando
você, interrompida ou ainda não aconteceu. Em telas estreitas, a linha fica
vertical.

Nas etapas do LangGraph (02 a 08), a linha mostra as perguntas do grafo como
losangos de sim ou não. Cada losango é uma aresta condicional que sai da linha
principal, com o desvio indicado abaixo (pergunta para você, consultor, volta a
outra etapa ou fim). O caminho que o atendimento seguiu aparece em destaque e
os outros ficam tracejados. Antes do pedido, a linha mostra o roteiro previsto.
Fora do LangGraph (01 e 09 a 12), há só paradas.

Clicar numa parada mostra a tecnologia envolvida (nó do LangGraph, modelo de IA,
API do HCM, regras ou banco do ERP), as operações com horário e duração e, nas
chamadas de IA, o modelo solicitado e o retornado, o esforço enviado, o limite
de saída, a rodada, reenvios e correções. Tokens de entrada, saída, raciocínio e
cache aparecem somente quando reportados. O custo é uma estimativa pelo consumo
reportado, nunca uma fatura. Um seletor permite ver execuções anteriores.

O mapa de paradas e perguntas fica em `backend/app/workflow_map.py` e é
publicado por `GET /api/workflow/map`, autenticado e sem dados de atendimento.
Cada aresta condicional do grafo registra a rota tomada (`progress.route`),
guardando apenas os nomes dos nós de origem e destino. Execuções gravadas antes
desse registro mostram as perguntas sem destacar um caminho.

## Trecho do LangGraph

Dois marcadores tracejados mostram onde o LangGraph começa e termina. O grafo
começa no Escopo (`guard_input`) e termina depois da Validação (`plan` → `END`):
classificação, contexto, interpretações, a pausa da sua escolha, o plano
validado e o encaminhamento ao consultor acontecem dentro dele. Aprovação,
aplicação no HCM, testes e experiência seguem fora do grafo, pelas rotas de
execução. Pedidos fora do escopo e atendimentos encerrados pelo consultor
terminam o grafo antes da Validação.

## Ocultar o fluxo

O botão Ocultar fluxo, no cabeçalho do card, deixa apenas o título e o estado
atual em uma linha. A escolha fica guardada no navegador e vale para a tela
inicial e para os atendimentos. Oculto, o card continua acompanhando o
atendimento e atualizando a tela.

## Isolamento

- Não altera código, dados, endpoints, formatos ou contrato do HCM.
- Não adiciona chamadas à API do HCM nem ao provedor de IA.
- Não retoma, aprova, aplica ou reverte operações pelo gráfico.
- Não altera a revisão do atendimento, o hash de aprovação ou o checkpoint.
- Guarda somente identificadores de etapa e operação, nomes de nós do grafo nas
  rotas, estado, horário e uma lista explícita de metadados primitivos de
  execução. Não guarda prompts, respostas textuais dos agentes, parâmetros de
  negócio, URLs do provedor ou credenciais.
- A tabela `workflow_progress` é exclusiva do ERP, criada pela migração
  `0005_workflow_progress`, sem chave estrangeira para a linha do atendimento.
- As gravações usam conexão, pool e transação independentes. No PostgreSQL,
  há limite de 100 ms para locks e 250 ms para instruções. Uma falha desativa
  a observação naquela invocação, sem cancelar ou repetir a operação.

Os registros são preservados entre as pausas humanas, aplicações e reanálises:
o armazenamento mantém a execução atual e as últimas 20 anteriores, com até
160 registros por execução. O painel informa quando há truncamento. Esta
extensão usa o JSON da tabela existente e não exige outra migração.

O progresso é observado na entrada/saída dos nós e em subetapas de consulta,
geração e validação. Os testes internos do HCM continuam sendo realizados pelo
próprio HCM durante a aplicação: o ERP mostra o relatório quando ele chega,
sem inventar progresso dentro do sistema externo.

## Atualização da tela

O endpoint autenticado `GET /api/cases/{id}/progress` é somente leitura e não
usa locks do atendimento. A interface consulta aproximadamente a cada 1,8 s
durante o processamento e 8 s em espera. Consultas não se sobrepõem, são
canceladas ao trocar de atendimento e pausam quando a aba está oculta.

Sem registros (por exemplo, em atendimentos anteriores à instalação), o card
usa os dados confirmados do atendimento. Se a conexão falha, informa a
indisponibilidade do acompanhamento e tenta reconectar. O horário do último
registro é exibido; uma operação abruptamente interrompida pode deixar como
último registro a etapa iniciada. Os estados do atendimento e as opções de
retomada existentes continuam sendo a referência para agir.

Atendimentos antigos mostram os registros e métricas já existentes, sem inferir
o esforço ou reconstruir operações ausentes. Abrir detalhes não refaz chamadas.

Não há porcentagens ou durações simuladas. O tempo decorrido de uma operação
ativa é estimado na tela pelo horário de início; a duração concluída é medida
pelo servidor. Animações respeitam a preferência
por movimento reduzido. Em telas menores, as etapas seguem em ordem de leitura.

## Validação

`backend/tests/test_progress.py` verifica visibilidade durante uma chamada de
agente ainda bloqueada, leitura sem mudança de revisão, autenticação, registros
limitados e conclusão do fluxo com observação indisponível. Também confirma que
o grafo começa no Escopo, termina depois da Validação e não tem nós nas etapas
seguintes, como indicam os marcadores; que as rotas são gravadas só com nomes de
nós; e que o mapa de paradas mostra todas as arestas do grafo compilado e todas
as operações registradas. Um caminho novo no grafo sem pergunta no mapa faz o
teste falhar. Todos os testes de execução usam HCM simulado.

`node --test tests/stageTrace.test.mjs`, no frontend, verifica uma chamada de IA
como parada única e dividida em pergunta, formulário, espera e conferência, o
desvio seguido e o restante da linha não alcançado,
execuções antigas sem caminho inventado, a decisão humana que encerra a pausa e
a falha registrada só no nível da etapa.
