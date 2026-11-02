# ERP AI — parametrização assistida do HCM

Sistema independente, conectado ao HCM Lab por API. A parametrização dos módulos disponíveis atende todas as empresas cadastradas no HCM, em homologação. O pedido é interpretado por um modelo configurável; a alteração exige aprovação humana e é executada automaticamente no HCM. Produção não é publicada pela integração.

## Como usar

1. Entre em http://localhost:3001 com o acesso de `.local-access.txt`.
2. Em **Modelo de IA**, escolha um modelo, salve sua chave e teste a conexão. A chave não deve ser enviada pelo chat. Preços, disponibilidade e estimativas aparecem na tela.
3. Em **Atendimentos**, descreva o objetivo e a vigência. Em **Modelos dos agentes**, confira primeira rodada (Luna por padrão) e reanálise (Sol por padrão), com preços. A chave salva é reutilizada; o modelo global anterior é preservado.
4. Clique **Analisar solicitação**. O sistema identifica empresa e módulo e mostra diretamente três interpretações. Escolha a correta; “Nenhuma dessas opções” refaz a identificação usando sua correção. Confira então a proposta de configuração.
5. Clique **Aprovar e aplicar em homologação**. O HCM aplica o plano exato, testa e registra a operação. Não é necessário abrir o HCM para configurar.
6. Após a mensagem de configuração aplicada, clique **Aprovar e guardar experiência** se o resultado atende ao pedido. O relatório automático está em um detalhe opcional; não há questionário de evidências.
7. Se precisar desfazer, use **Reverter esta aplicação em homologação**. Mudanças posteriores bloqueiam a reversão, para preservar o trabalho de outras pessoas.

O menu **Como usar** explica o fluxo dentro do aplicativo. Se um atendimento antigo foi criado antes da execução automática e o HCM já mudou, faça nova análise; não tente aplicar uma proposta antiga sobre dados novos.

## Contrato de integração v2

O ERP AI conversa com o HCM por `/api/integration/v2`. O catálogo de cada módulo traz os campos com JSON Schema, os escopos disponíveis, a precedência das regras e um `contract_hash`. Esse hash fica gravado em cada atendimento e em cada memória de interpretação: se o HCM mudar o significado de um campo ou a precedência, mesmo sem trocar a versão do catálogo, o ERP bloqueia aceites e execuções do atendimento antigo e deixa suas memórias fora da recuperação automática.

Em Férias, uma alteração pode mirar a regra geral, a regra própria de um grupo (`group_id`) ou uma configuração nomeada (`config_code`). Configurações nomeadas novas são declaradas em `named_configurations`; a lista de Atribuições de Configurações pode ser substituída no mesmo plano (`assignments`) e entra no hash de aprovação. Quando a regra efetiva de um grupo vem de uma atribuição, o ERP recusa alterar a regra do grupo e orienta a mexer na configuração nomeada ou nas atribuições. Os demais módulos do HCM usam escopo de empresa, e seus parâmetros estruturados, como etapas de aprovação, são validados pelo esquema do catálogo antes de chegar ao validador oficial.

A lista de módulos e seus rótulos vêm do HCM (`GET /v2/modules`); o ERP guarda apenas o apelido `time` para `timekeeping`. A saída do modelo é um esquema estrito gerado a partir do catálogo, com uma variante por parâmetro; mapas do catálogo viram listas chave/valor para o modelo e voltam a mapas antes da validação.

## Memória e aprendizagem

Documentação e casos aprovados são recuperados por busca lexical com radicalização leve em português, filtrados por empresa, módulo, versão e hash de contrato. Casos rejeitados, revertidos e obsoletos ficam fora da memória de soluções aprovadas. Duas fontes extras, ligadas por padrão e desligáveis em **Conhecimento**: experiência anonimizada de outras empresas no mesmo módulo e contrato, com nomes de empresa e grupos redigidos, usada como analogia e nunca como valor a copiar; e lições, os contraexemplos colhidos do próprio fluxo: interpretações recusadas com comentário, planos rejeitados pelo validador, planos com pendências e conclusões do consultor, também redigidos. O intérprete e o planejador recebem essas fontes marcadas como dados, não ordens. Isso melhora o conhecimento externo disponível à IA; não atualiza automaticamente seus pesos. Fine-tuning permanece uma decisão futura, dependente de dados revisados e avaliação.

**Uso e resultados**, em Conhecimento, agrega todas as chamadas por modelo e papel (chamadas, reparos, tokens, duração média, custo estimado) e o funil por módulo (aceitas na primeira rodada, reanálises, encaminhamentos, aplicados, revertidos, validados), com custo por caso validado. **Capacidades ausentes**, em Não tratados, agrupa as lacunas que os agentes descreveram, com exportação CSV para o roadmap do HCM.

Avaliação funcional: `scripts/evaluate.py --execute` roda os cenários `kind=parameter` de `evaluation/scenarios.json` com o modelo real sobre um estado sintético por cenário, julga a proposta contra o esperado e grava `artifacts/evaluation-<data>.json`. Rode dentro da imagem do backend, com o repositório montado, conforme `evaluation/README.md`. Não altera o HCM.

## Orquestração e memória de intenção

LangGraph organiza classificação, consulta ao catálogo, apresentação guiada, geração de três interpretações, pausa para escolha, memória de intenção, plano validado e encaminhamento ao consultor. As pausas usam `interrupt`/`Command` e `thread_id` estável. Checkpoints ficam no PostgreSQL do ERP AI; testes locais usam SQLite em arquivo. O servidor prepara o esquema antes de receber requisições. Recompilar o grafo ou reiniciar o backend não perde as escolhas nem repete as etapas já concluídas.

Há uma reanálise mais capaz após a primeira recusa. A segunda recusa cria um atendimento em **Não tratados**. Uma escolha com capacidade ausente também encaminha ao consultor, sem aplicação. O consultor pode orientar novo plano, usar o editor validado ou concluir o encaminhamento. A classificação contempla Férias, Folha/demonstrativo, Ponto, Admissão, Benefícios e Outros. Os parâmetros existentes de Férias, Controle de Ponto, Admissão, Folha demonstrativa e Benefícios possuem conectores. Requisitos que não constem dos catálogos são registrados em Não tratados, sem parâmetros fictícios.

A lista de módulos que o classificador conhece vem do HCM a cada fluxo, com rótulo e descrição; não há lista fixa no ERP. Cada interpretação informa o nível que pretende alterar e descreve lacunas de capacidade com módulo e descrição, que aparecem na fila Não tratados. Um plano rejeitado pelo validador volta uma vez ao modelo com o motivo antes de ir ao consultor. Cada agente registra sua própria versão de prompt nas métricas.

`interpretation_memories` guarda intenção confirmada por consultor, empresa, módulo e versão do catálogo. Essa memória é consultada como contexto, exige confirmação em cada novo pedido e não comprova solução funcional. Pode ser desativada em **Conhecimento**. Casos aplicados, testados e aprovados continuam em uma busca separada. Chaves de API e tokens do HCM nunca entram no checkpoint nem no prompt.

A criação e retomada usam revisão otimista e bloqueio de linha no PostgreSQL. Um erro pode ser retomado com o mesmo checkpoint. A confirmação de interpretação não autoriza escrita: a execução permanece sujeita à aprovação do plano exato e à verificação de contexto no HCM.

Aceitação real: `python scripts/live_orchestration.py --execute --pause-after-guide`, reinicie o backend, depois `python scripts/live_orchestration.py --execute --resume`. O teste chama o modelo real, aplica uma alteração reversível, verifica, aprova, reverte e desativa a memória técnica. Artefato em `artifacts/live-orchestration.json`. Execute testes automatizados dentro do container para manter dependências isoladas: `docker compose exec -T -e DATABASE_URL=sqlite:// backend python -m pytest -q`.

## Execução, testes e reversão

A conexão usa duas credenciais aleatórias distintas: consulta e execução. No HCM ficam somente seus hashes; no ERP AI, segredos do backend. O modelo não recebe essas credenciais.

Cada aprovação inclui hash do plano e do contexto (versões, grupos, empresa, ambiente e vigência). O HCM confere novamente dentro de transação e bloqueia contexto alterado. Bloqueio de empresa serializa escritas de férias. Cada operação tem UUID idempotente: resposta perdida pode ser retomada sem duplicar versões. O ERP AI persiste o identificador antes da chamada remota.

O HCM verifica valores/agendamentos, dependências, precedência por grupo, limites de parcelas, solicitante, adiantamento, antecedência e saldo em cenários sintéticos; produção deve permanecer preservada. O motor de política/datas é compartilhado com a simulação operacional. Falhas de teste desfazem a transação inteira. Não são criadas solicitações reais de férias pelos testes.

A reversão retira somente as versões criadas pela operação, preservando histórico, auditoria e numeração monotônica. Regras de grupo recém-criadas voltam a herdar o valor anterior. Só é permitida se o estado ainda corresponder ao resultado da operação.

Após sucesso, a aprovação humana do resultado libera o caso para memória. Os testes verificam o comportamento implementado; a adequação ao requisito de negócio ainda é revisada pelo especialista.

## Instalação e atualização

Requisitos: Docker Desktop/Compose; Python 3.12 para preparar o acesso; HCM com o adaptador incluído neste trabalho.

```powershell
python scripts/bootstrap.py --hcm-path C:\projects\HCM
docker compose -f C:\projects\HCM\compose.yaml up -d --no-deps backend
docker compose up -d --build
```

O bootstrap preserva os acessos existentes e prepara credenciais de consulta/execução sem imprimi-las. O HCM aplica a migração 0009 ao iniciar. ERP AI usa banco/volume próprios. Interface 3001; backend 8001, ambos restritos a localhost no Compose do novo sistema. Banco sem porta pública.

Configuração de modelo pela tela fica em `secrets/model.json`, ignorado pelo versionamento e montado no backend; prevalece sobre `.env`. Não há criptografia própria de disco. O token do HCM e as senhas ficam nos arquivos locais ignorados. Proteja esses arquivos e não os compartilhe.

## Modelo e custos

21 modelos de texto compatíveis, com preços Standard verificados em 31/10/2026 nas páginas oficiais. A API de modelos informa listagem da conta, não preço ou garantia de acesso. Modelos com retirada anunciada exibem a data e saem do seletor no desligamento. O padrão sugerido para novas configurações é Luna; escolha existente não é trocada automaticamente. Sem escalada automática para um modelo mais caro.

O adaptador usa Chat Completions, JSON Schema strict, `store=false`, limite de saída e raciocínio apropriado ao modelo. Campos, grupos e fontes são restritos ao catálogo. Chave, quota, acesso, recusa, timeout e saída inválida geram mensagens distintas, sem expor respostas sensíveis. Cada geração registra modelo, versão do prompt, hash do esquema, duração, tokens e estimativa de custo. Preços/contexto/cache/impostos podem afetar a cobrança real; estimativa não é fatura. Não há testes pagos automáticos de todos os modelos.

## Testes

```powershell
cd backend
python -m pytest -q
cd ..\frontend
npm ci
npm run build
```

No HCM: execute `python -m pytest -q` em `backend`. Há testes de regras, isolamento, permissões, execução atômica, repetição idempotente, contexto alterado, vigência futura, grupos e reversão. No ERP AI, também são testados recuperação de resposta perdida, aprovação vinculada, confirmação sem evidências manuais e exclusão de revertidos da memória.

Teste integrado real opcional, que custa uma geração Luna e aplica uma alteração temporária exclusivamente em homologação:

```powershell
python scripts/live_acceptance.py --execute
```

Há também `scripts/live_named_config.py --execute`, que cria uma configuração nomeada temporária, atribui a um grupo, confere que a regra efetiva do grupo passa a vir da atribuição, confirma e reverte, sem chamada ao modelo. Atendimentos analisados antes do contrato atual exibem um aviso e precisam de nova análise antes de qualquer aceite.

Esse teste registra um atendimento técnico, executa o fluxo pela API, confere os resultados e reverte exatamente as configurações. Não modifica o modelo salvo nem publica produção. Seu relatório fica em `artifacts/live-acceptance.json`; o caso revertido não vira experiência aprovada. Testes com mocks não comprovam qualidade do modelo; os cenários em `evaluation/` ainda exigem revisão do especialista e comparação funcional.

## Operação e limites

`docker compose stop/start` pausa/retoma. `docker compose down` preserva o volume; não remova volumes para atualizar. Backup: pg_dump do banco próprio, restauração verificada em banco separado, sem usar reset_demo do HCM.

Rotacionar/remover o hash de execução no HCM revoga escrita sem revogar consulta. Rotacionar SESSION_SECRET encerra sessões do ERP AI. Sessão de oito horas, cookie HttpOnly/SameSite e verificação de origem. Primeiro uso local de um especialista e uma instância do backend; implantação pública requer TLS, cookie Secure, identidade individual, limites compartilhados e gestão externa de segredos.

Este sistema parametriza Férias para as empresas cadastradas: não calcula folha legal, não agenda períodos separados por parcela e não publica produção automaticamente. Quando um módulo ou requisito não possui capacidade disponível no catálogo/conector do HCM, o atendimento entra em Não tratados para revisão do consultor. O modelo usa memória externa, sem treinamento automático de pesos. Execução remota não é transação distribuída: estado pendente é retomado com a mesma chave idempotente. A aprovação final é humana e reversível enquanto não houver mudanças posteriores.

A busca de empresas consulta o cadastro oficial do HCM, ignora diferenças de acento e maiúsculas e apresenta homônimos para confirmação. Todas as empresas encontradas podem receber configurações disponíveis. O código da empresa identificada acompanha consulta, memória, proposta, aplicação, verificação e reversão. Não há substituição por empresa padrão.

## Fluxo simplificado

A análise identifica automaticamente empresa e módulo e pausa diretamente nas três interpretações. Só há esclarecimento se a empresa estiver ausente, ambígua, não encontrada ou não autorizada. Não há confirmação separada de empresa ou módulo. Uma primeira recusa refaz classificação, busca da empresa e interpretação usando a correção; a segunda encaminha ao consultor. Refazer a proposta inicia uma nova entrada e desativa a memória de intenção anterior do atendimento. Após a aplicação, a tela principal mostra uma confirmação e o botão Aprovar e guardar experiência. Relatório de testes, contexto e histórico permanecem disponíveis em detalhes recolhidos.

## Empresas e Não tratados

A integração opera para qualquer empresa cadastrada. Não há lista fixa de empresas, códigos ou exigência de status Ativa para parametrizar. A empresa precisa existir, os grupos precisam pertencer a ela, e o plano deve corresponder à aprovação e à vigência; a publicação em produção continua fora do fluxo aprovado. Operações e reversões usam a empresa gravada na própria operação, inclusive em retomadas de operações antigas. Não há empresa padrão para novas consultas ou novos atendimentos.

A fila Não tratados (`/api/unhandled`, com compatibilidade para `/api/consultant-queue`) inclui capacidades ausentes, módulos sem conector, revisões de interpretação, propostas inválidas e falhas de execução/integração. Cada item mostra empresa, módulo, pedido, motivo e categoria. Requisitos sem nenhuma interpretação atendível são encaminhados automaticamente, sem exigir escolha de uma alternativa impossível. O consultor pode revisar a regra, preparar um plano validado ou encerrar com conclusão; casos resolvidos saem da fila. A fila não envia mensagens externas.

## Parâmetros existentes dos módulos de RH

O catálogo e o executor incluem Férias, Controle de Ponto, Admissão, Folha demonstrativa e Benefícios. Nos quatro módulos de RH, os parâmetros são lidos do contrato real `hr_api.DEFAULTS` e verificados pelo validador oficial `validate_config`. O código não inventa parâmetros para atender pedidos fora do catálogo. Operações são vinculadas à empresa e ao módulo, com versões auditadas, idempotência, verificação e retirada reversível da versão (`0010_hr_integration`). No Controle de Ponto, cenários sintéticos passam pelo mesmo `time_evaluation` usado no simulador operacional, sem criar registros de funcionários. A alteração de horas extras é suportada pelos parâmetros existentes `allow_overtime` e `max_overtime_hours`.

## Escopo e entradas não relacionadas ao HCM

O grafo verifica o escopo antes de buscar empresa ou catálogo. Uma verificação rápida bloqueia padrões explícitos de substituição de instruções e cálculos gerais; a classificação semântica também distingue parametrização, esclarecimento pertinente e perguntas fora do objetivo. A última mensagem é avaliada, inclusive quando o histórico inicial é legítimo. Entradas fora do escopo recebem aviso próprio, não entram em Não tratados nem viram memória de solução. O usuário pode reformular o pedido no mesmo atendimento; o texto antigo fica apenas no histórico, sem contaminar o novo pedido. Esclarecimentos repetidos têm confirmação visível e limpeza do campo. Essas verificações complementam os validadores, o isolamento por empresa/módulo e a aprovação do plano; não representam uma garantia contra toda forma de prompt injection.
