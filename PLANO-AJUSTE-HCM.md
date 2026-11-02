# Plano de ajuste do ERP AI ao HCM atual

Estado em 01/11/2026. O HCM Lab evoluiu nas migrações 0011 a 0013 (configurações nomeadas de férias, Atribuições de Configurações, participação em vários grupos, Guia Férias, workflow/SLA e cerca de vinte módulos parametrizáveis). A integração `/api/integration/v1` e o ERP AI continuam no contrato v1: Férias por empresa/grupo e quatro módulos de RH por empresa, todos com valores escalares. Este plano fecha essa lacuna sem abrir mão das garantias atuais (aprovação humana, hash de plano, idempotência, testes na transação, reversão exata, produção intocada).

Princípio geral: o ERP AI não conhece regras de RH. Tudo o que descreve módulos, campos, escopos, limites e precedência vem do catálogo do HCM. Onde hoje há listas fixas no ERP, passam a existir dados do catálogo.

## Diagnóstico resumido

| Item | HCM hoje | Integração v1 | ERP AI hoje |
| --- | --- | --- | --- |
| Módulos parametrizáveis | 21 em `policies.DEFAULTS` + Férias | 5 | 5 + "other" fixos em 4 lugares |
| Escopos de Férias | atribuição > grupo > empresa > sistema | grupo > empresa > sistema | `group_id` ou nulo |
| Configurações nomeadas | `config_code`, `name`, `simulation_text` | removidas do snapshot; apply recusa | desconhecidas |
| Grupos | principal + participações adicionais | só grupo principal (`employee_count`) | prompt afirma snapshot completo |
| Tipos de valor | bool, int, str, listas, objetos aninhados | bool, int, str | `Change.value` só escalar |
| Versão do catálogo | `vacations-1`, `<módulo>-1`, nunca alterada | idem | usada para obsolescência e memória |
| Testes automáticos de Férias | `test_result` com precedência de 3 níveis | idem | confia no relatório |

Riscos concretos do estado atual:

- O ERP pode aplicar regra de grupo que uma atribuição ordenada sobrepõe. Os testes passam e a tela mostra regra efetiva que nenhum colaborador recebe.
- Memórias e casos validados continuam "compatíveis" porque `catalog_version` não mudou, embora a semântica tenha mudado.
- Pedidos sobre aprovação, SLA, aviso prévio, abono, vale-transporte etc. caem em Não tratados como "other".

## Decisões de arquitetura a tomar antes da Fase 1

1. **Contrato v2 por baixo do mesmo prefixo.** Manter `/api/integration/v1` servindo o contrato antigo até o ERP migrar e expor `/api/integration/v2`. Evita janela em que os dois lados discordam. Remover v1 só depois da aceitação da Fase 4.
2. **Versão semântica do catálogo + hash de contrato.** O HCM passa a devolver `version: "vacations-2"` e um `contract_hash` (SHA-256 de campos, escopos, regras e capacidades). O ERP grava o hash no atendimento e trata divergência como obsolescência, independentemente de o HCM lembrar de mudar a versão.
3. **Esquema por campo, não por tipo.** Cada campo do catálogo traz um JSON Schema (`schema`) do seu valor. O ERP monta o esquema estrito do modelo e valida a proposta a partir dele. Isso substitui o trio bool/int/str e permite listas e objetos.
4. **Escopo explícito.** Toda alteração carrega `scope: {type: "company" | "group" | "config", id}`. `group_id` continua aceito como atalho em v1 e é convertido.
5. **Precedência vem do HCM.** A função que resolve a regra efetiva (hoje `resolve_effective_config`, por colaborador) ganha uma versão pura sobre dados do snapshot, usada tanto no snapshot da integração quanto no `test_result`. O ERP nunca reimplementa precedência.

## Fase 1 · HCM: contrato v2 de Férias e módulos genéricos

**Estado em 01/11/2026: implementada, sem commit.** Arquivos novos: `backend/app/integration_v2.py` e `backend/tests/test_integration_v2.py`. Alterados: `vacations.py` (função pura `resolve_rule`, usada por `resolve_effective_config`), `policies.py` (`field_schema` e `SPECIAL_SCHEMAS`), `integration_execution.py` (`policy_checks` compartilhado; rotas v1 ignoram operações v2), `integration_hr.py` (idem), `main.py` (registro da rota v2), `README.md`. Suíte do HCM: 104 testes verdes no container. Exemplo real do contrato capturado em `evaluation/contract-v2-example.json` deste repositório, para servir de fixture à Fase 2.

Diferenças em relação ao previsto abaixo: `/v2/vacations/validate` recebe o plano inteiro (configurações e atribuições), não uma configuração isolada; o snapshot v2 já traz `fingerprint`, então o ERP não precisa recalculá-lo; `created_config_ids` virou lista de `{kind, id}` para a reversão distinguir versões de Férias e de atribuições; todos os módulos de `DEFAULTS` entraram no v2, inclusive `payroll_setup`.

Arquivos: `backend/app/integration_api.py`, `integration_execution.py`, `integration_hr.py`, `vacations.py`, `policies.py`, novo `integration_v2.py` (ou subpasta), testes em `backend/tests/test_integration*.py`.

1.1 **Catálogo v2 de Férias** (`GET /v2/catalog?module=vacations`)
- `version: "vacations-2"`, `contract_hash`, `module`, `module_label`.
- `fields`: os dez campos atuais, cada um com `schema` JSON, `label`, `help`.
- `scopes`: `[{type:"company"}, {type:"group", source:"snapshot.groups"}, {type:"config", source:"snapshot.named_configurations", fields:["config_code","name","simulation_text"]}]`.
- `precedence`: descrição legível e ordenada: atribuição ordenada por grupo, regra própria de grupo, regra da empresa, padrão do sistema. Informar que atribuição é resolvida por colaborador e que colaboradores podem estar em vários grupos.
- `rules` atualizadas (remover "não há herança por campo" como regra universal; manter dependências de parcelamento/processo).
- `related_modules`: `config_assignments`, `vacation_policy`, `workflow.vacations` com uma frase sobre o que cada um controla, para o intérprete saber redirecionar.
- `available_modules`: derivado de `policies.LABELS`, não mais lista manual.

1.2 **Snapshot v2 de Férias** (`GET /v2/vacations/snapshot`)
- `schedules.homologation/production`: incluir linhas com `config_code` (hoje filtradas).
- `named_configurations`: última versão por código, por ambiente.
- `assignments`: `config_assignments` vigente (`vacations` e `vacations_simulation`) com `group`, `config`, `priority`.
- `groups`: `id`, `name`, `primary_count`, `member_count` (inclui participações adicionais), `overlap_count` (colaboradores também em outros grupos).
- `effective`: por grupo, resolvido pela função pura de precedência para "colaborador apenas neste grupo", com `origin` (`assignment`/`group`/`company`/`system`) e aviso quando `overlap_count > 0`.
- `fingerprint` passa a cobrir também nomeadas e atribuições (o ERP só reflete o que o HCM manda, então a mudança é automática desde que entrem em `schedules`/novo campo incluído no `snapshot_hash`).

1.3 **Validate/apply/rollback v2 de Férias**
- Aceitar `configurations` com `config_code` (escopo config). Validar `check_named` já existente.
- Aceitar bloco opcional `assignments` no mesmo plano (nova versão de `config_assignments` em `hr_module_configurations`), aplicado na mesma transação, com `created_ids` registrando a origem (`vacation_configurations` ou `hr_module_configurations`) para a reversão revogar ambos.
- `test_result`: trocar a resolução interna pela função pura de precedência (1.5). Adicionar checagem "atribuições referenciam grupos e códigos existentes" e sondas por grupo com e sem sobreposição.
- Mensagens de 403/409/422 preservadas; `operation_out` ganha `contract_hash`.

1.4 **Módulos genéricos v2** (`/v2/modules/{module}/...`)
- `check_module` aceita qualquer chave de `DEFAULTS`, exceto as marcadas como internas (`payroll_setup` se for considerado dado mestre; decidir caso a caso em uma lista `INTEGRATION_MODULES`).
- Catálogo: campos a partir de `policies.field_catalog` com `schema` JSON gerado da estrutura de `DEFAULTS` (listas de inteiros, listas de strings, objetos com chaves conhecidas como `general_configs`, `processes`, `rules`). Para os módulos com validação estrutural em `validate_config`, expor o esquema mais restritivo possível e deixar o resto para o validador.
- Snapshot: `flat()` deve lidar com valores não escalares e aplicar `normalize` para módulos novos (hoje pulado só para legados).
- Apply: manter uma configuração por operação e todas as chaves presentes (`values_from`), agora validando pelo `schema` e por `validate_config` + `cross_errors` (o apply atual não chama `cross_errors`; passar a chamar).
- `report_for`: checagens genéricas iguais às atuais; sondas específicas onde houver motor reutilizável (`time_evaluation` já existe; para `workflow` usar `processes` para simular aprovadores de um pedido sintético, se houver função pura disponível).

1.5 **Função pura de precedência** (`vacations.py`)
- Extrair de `resolve_effective_config` uma `resolve_from_snapshot(snapshot_rows, assignments, groups_of_employee, as_of)` sem banco. `resolve_effective_config` passa a usá-la. `integration` e `test_result` também.

1.6 **Testes HCM**
- Snapshot v2 reflete nomeada atribuída; apply v2 com `config_code` + `assignments`; rollback restaura ambos; `test_result` reprova quando atribuição anula a regra de grupo planejada; módulo genérico (`workflow`) com validate/apply/rollback; v1 continua passando sem alteração.
- Atualizar README do HCM: seção "Integração com o ERP-AI" ainda fala em piloto ABC/EMP001.

Critério de aceite da fase: `pytest` do HCM verde; `curl` em v2 devolve nomeadas e atribuições para uma empresa de teste; v1 inalterado.

## Fase 2 · ERP: camada de contrato dirigida por catálogo

**Estado em 01/11/2026: implementada.** Novos: `backend/app/schema_tools.py` (conversão entre o JSON Schema do catálogo e a forma estrita aceita pelo modelo, mais validação determinística) e `migrations/versions/0003_contract_v2.py`. Alterados: `hcm.py` (cliente v2, `canonical_module`, lista de módulos do HCM), `models.py` (`module` e `contract_hash`), `contracts.py` (`config_code`, `named_configurations`, `assignments`, valor livre validado por esquema), `workflow.py` (payloads por tipo de escopo, hash de aprovação com atribuições, obsolescência por hash, `case_catalog`), `provider.py` (esquema estrito gerado do catálogo, prompt `hcm-consultant-4`), `knowledge.py`, `main.py`, `execution_api.py`, `orchestration.py`, `orchestration_api.py`, frontend (`page.tsx`, `ExecutionPanel.tsx`). Suíte: 68 testes verdes. Aceitação real contra o HCM v2: `live_orchestration.py` (Férias) e `live_hr_acceptance.py` (Controle de Ponto) passaram com reversão exata.

Diferenças em relação ao previsto: o escopo é expresso por `group_id`/`config_code` em vez de um objeto `scope`, para ficar simples para o modelo e para o editor manual; uma configuração nomeada nova parte da regra da empresa planejada ou vigente, não exige todos os campos; mapas do catálogo (como `group_overrides`) viram listas chave/valor na saída do modelo e voltam a mapas no ERP, porque o modo estrito não aceita objetos abertos. A lista fixa de módulos da classificação continua no código até a Fase 3; o cliente e a rota de conexão já consultam `GET /v2/modules`.

Arquivos: `backend/app/hcm.py`, `contracts.py`, `workflow.py`, `provider.py`, `models.py`, nova migração `0003_contract_v2.py`, `main.py`, `execution_api.py`, testes.

2.1 **Cliente HCM**
- `HCMClient` aponta para `/v2`; `module` validado contra `available_modules` do catálogo (com cache curto), não contra `MODULES` fixo. O alias `time` → `timekeeping` fica em um único lugar (`canonical_module()`) e é removido dos demais (`orchestration.context`, `main.connection`).
- Novo método `contract()` que devolve catálogo e `contract_hash`.

2.2 **Modelo de dados**
- `Case`: colunas `module` (string) e `contract_hash`. Hoje o módulo vive dentro de `workflow` JSON e o `get_hcm` o extrai de lá.
- `InterpretationMemory`: coluna `contract_hash`. Filtro de memória passa a usar `module + contract_hash`.
- Migração `0003_contract_v2`: adiciona colunas, preenche `module` a partir de `workflow.module` e `contract_hash` vazio (casos antigos ficam obsoletos para memória automaticamente).

2.3 **Contratos da proposta**
- `Change`: `scope: Scope` (`type`, `id: int | str | None`) + `parameter` + `value: Any` + `reason`. Validador que aceita `group_id` legado e converte.
- `Proposal`: campo opcional `assignments` (lista de `{group, config}` por processo) e `named_configurations` (metadados `config_code`, `name`, `simulation_text` quando o escopo é config).
- `provider.proposal_schema`: monta `parameter` enum, `scope` enum a partir de `catalog.scopes` + snapshot, e `value` como `oneOf` dos `schema` dos campos. Como o modo estrito da OpenAI exige `additionalProperties: false` e todas as chaves `required`, gerar uma variante de `Change` por campo (`anyOf` de objetos `{parameter: const, value: <schema>}`) e manter o esquema abaixo do limite de tamanho; medir com os módulos maiores (`workflow`, `vacation_policy`).

2.4 **Validação determinística** (`workflow.checked_payloads`)
- Validar cada valor com `jsonschema` contra o `schema` do campo (nova dependência `jsonschema`).
- Completar a regra por escopo a partir do `effective` do snapshot, como hoje, mas por tipo de escopo. Para `config`, o ponto de partida é a última versão da nomeada; para nova nomeada, exigir todos os campos e os metadados.
- Montar payloads v2 e chamar `validate` v2. Manter a rejeição de "valor já atendido" e duplicidade por escopo.
- `compare_result`/`collect_result`: usar os campos do catálogo gravado no atendimento; remover a tupla fixa de campos de Férias em `execution_api.py`.

2.5 **Obsolescência**
- `fresh()` e `check-result` comparam `contract_hash` além de `catalog_version`. Mensagem própria: "O contrato de integração mudou; analise novamente".

2.6 **Testes ERP**
- `FakeHCM` v2 com nomeadas, atribuições e grupos sobrepostos. Casos: plano com escopo config; plano com atribuição; valor em lista rejeitado por esquema; módulo `workflow` fim a fim com executor fake; `contract_hash` divergente bloqueia aceite; atendimento antigo sem hash não entra na memória.

Critério de aceite: suíte ERP verde; `live_orchestration.py` e `live_hr_acceptance.py` passam contra v2 sem alterar expectativas de negócio.

## Fase 3 · ERP: agentes dirigidos pelo catálogo

Arquivos: `backend/app/orchestration.py`, `orchestration_api.py`, `provider.py`, `frontend/app/*.tsx`.

3.1 **Classificação dinâmica**
- `Classification.module` deixa de ser `Literal` fixo. Construir o modelo com `pydantic.create_model` a partir de `available_modules` + `other` no início de cada fluxo, e gravar a lista usada no estado para a retomada não mudar o contrato no meio do caminho.
- `MODULES` e `EXAMPLES` vêm do catálogo (`module_label`, `example`); o HCM fornece `example` em `LABELS`/catálogo ou o ERP usa um genérico.
- O classificador recebe `related_modules` para distinguir, por exemplo, "parcelas" (Férias) de "etapas de aprovação de férias" (`workflow`).

3.2 **Intérprete**
- Recebe `catalog.scopes`, `precedence`, `named_configurations`, `assignments` e `groups` com `overlap_count`. Instrução nova: quando há sobreposição de grupos ou atribuições, a interpretação deve dizer qual nível efetivo pretende alterar.
- `compact_context` passa a ser usado também aqui; incluir só a última versão por escopo e as nomeadas referenciadas.

3.3 **Planejador**
- `SYSTEM_PROMPT` deixa de afirmar fatos de Férias. Essas frases vão para `rules` do catálogo e entram como fonte. O prompt fica genérico sobre contrato, escopo, mínimo de alteração, perguntas vs. mudanças.
- Laço de reparo: se `checked_payloads` ou `validate` rejeitar, uma segunda chamada com os erros como feedback antes de encaminhar ao consultor (limite de 1). Hoje o fluxo orquestrado encaminha direto.
- Versão de prompt por papel (`PROMPT_VERSIONS = {"module_classifier": ..., "interpretation_agent": ..., "configuration_agent": ...}`) registrada nas métricas; hoje todos reportam o mesmo `PROMPT_VERSION`.
- Esforço de raciocínio decidido por um atributo do catálogo de modelos (`supports_reasoning_effort`) em vez de prefixo `gpt-6`/`gpt-5`.

3.4 **Frontend**
- Rótulos de módulo, exemplos e suporte vêm do atendimento/catálogo, não do mapa fixo em `OrchestrationPanel.tsx` e `page.tsx`.
- "Situação atual do HCM": tabela por grupo com coluna de origem e aviso de sobreposição; seção de nomeadas e atribuições.
- Proposta: coluna "Escopo" mostra `Regra geral`, `Grupo X` ou `Configuração CÓDIGO`. Valores estruturados exibidos como lista/JSON legível.
- Editor manual: seletor de escopo com tipo; para campos com `schema` de lista/objeto, editor JSON com validação no cliente; demais campos como hoje.

3.5 **Testes**
- Classificador com lista dinâmica (mock devolve módulo novo `workflow`); retomada após mudança de `available_modules` não quebra; reparo automático consome uma chamada e segue para revisão; segunda falha vai ao consultor.

## Fase 4 · Aprendizado e observabilidade

Arquivos: `knowledge.py`, `orchestration.py`, novo `metrics_api.py`, `evaluation/`.

4.1 **Recuperação**
- Tokenização com radicalização simples em português (remoção de plural e sufixos comuns) antes de pensar em embeddings; medir com os 25 cenários.
- Recuperação entre empresas em segundo nível: casos validados de outras empresas no mesmo módulo e `contract_hash`, com nome, código e `group_id` redigidos. Marcados como "experiência de outra empresa" na fonte. Pode ser desligado em Conhecimento.

4.2 **Contraexemplos**
- Persistir recusas (`interpretation_rejected` com comentário), erros do validador e resoluções do consultor numa tabela `lessons` com `module`, `contract_hash`, `company_code`, `kind`, `text`. Recuperar até 3 por pedido e passar ao intérprete e ao planejador como "evitar". Opt-out na tela Conhecimento, como a memória de intenção.

4.3 **Métricas agregadas**
- `GET /api/metrics`: por modelo e papel, chamadas, custo estimado, duração média; por módulo, taxa de escolha na primeira rodada, escalações, encaminhamentos, aplicações, reversões, casos validados; custo por caso validado. Painel simples em Conhecimento.

4.4 **Lacunas de capacidade estruturadas**
- `Interpretation.limitations` ganha `missing_capabilities: list[{module, description}]`. Fila Não tratados agrupa por descrição normalizada. Exportação CSV para o roadmap do HCM.

4.5 **Harness de avaliação**
- `scripts/evaluate.py`: roda `evaluation/scenarios.json` contra `FakeHCM` (estado inicial do cenário) com o modelo real opcional, registra acerto completo, alterações indevidas, perguntas, tokens e custo em `artifacts/evaluation-<data>.json`. Comparação A/B com e sem memória, como o README de `evaluation/` já descreve. Adicionar cenários de nomeadas, atribuições, sobreposição de grupos e módulos novos.

## Fase 5 · Transição e desligamento do v1

- Script `scripts/live_named_config.py --execute`: cria nomeada reversível, atribui a um grupo, verifica `effective` e sondas, reverte, confere baseline. Artefato em `artifacts/`.
- Marcar atendimentos abertos sem `contract_hash` como "analisar novamente" na interface.
- Depois da aceitação, remover rotas v1 do HCM e os aliases legados do ERP; atualizar README e PLANEJAMENTO dos dois projetos.

## Ordem, dependências e tamanho

| Fase | Depende de | Tamanho | Risco principal |
| --- | --- | --- | --- |
| Decisões | nenhuma | P | discordar do formato de `schema` por campo tarde demais |
| 1 HCM v2 | decisões | G | precedência por colaborador vs. por grupo; refatoração de `resolve_effective_config` |
| 2 ERP contrato | 1.1, 1.2, 1.4 publicados em ambiente local | G | tamanho do esquema estrito para módulos grandes; migração de casos antigos |
| 3 ERP agentes | 2 | M | regressão de qualidade com prompts mais genéricos; medir com o harness |
| 4 aprendizado | 2 (hash), 3 (métricas por papel) | M | vazamento de dados entre empresas na recuperação cruzada; exige redação testada |
| 5 transição | 1 a 4 | P | apagar v1 antes de todos os atendimentos abertos migrarem |

Fases 1 e 2 podem começar em paralelo se o formato do catálogo v2 for fixado por escrito primeiro (um exemplo JSON de catálogo e de snapshot v2 em `evaluation/contract-v2-example.json`, usado como fixture pelos dois lados). Fases 3 e 4 são independentes entre si após a 2.

## O que não muda

- Aprovação humana da interpretação e do plano exato; nenhuma escrita sem `approval_hash` válido.
- Duas credenciais, sem segredos em prompt ou checkpoint.
- Testes dentro da transação, idempotência por UUID, reversão guardada por fingerprint.
- Produção nunca escrita pela integração.
- Memória externa; nenhum treinamento de pesos.
