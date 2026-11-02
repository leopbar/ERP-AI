# Planejamento consolidado — ERP AI

Estado em 31/10/2026: fluxo de parametrização automática implementado para o piloto ABC/Férias/homologação. A execução exige aprovação do consultor; não publica produção. O produto é independente do HCM e usa exclusivamente sua API.

## Objetivo e uso
Receber requisito em linguagem natural, consultar a configuração atual, interpretar dependências e escopos, propor alterações mínimas, receber aprovação, aplicar no HCM, testar, apresentar resultados e registrar experiência revisada. O consultor usa o ERP AI para todo esse fluxo. Não precisa abrir o HCM para configurar ou transcrever evidências. A publicação, se desejada, continua no processo humano do HCM.

## Arquitetura implementada
Next.js/React na interface; Python/FastAPI para atendimento, modelo e integração; PostgreSQL próprio e Alembic. Docker Compose separado (3001/8001). Um workflow explícito, sem necessidade atual de n8n, LangGraph ou múltiplos agentes. Adaptador do provedor separado do adaptador do ERP. Um consultor local, autenticação e sessão próprias. Catálogo de modelos/preços versionado; escolha do usuário preservada; sem escalada automática de custo.

## Conhecimento e aprendizagem
Três fontes: catálogo funcional versionado, estado atual pela API e casos históricos aprovados. Busca lexical com filtros por empresa, catálogo e estado; embeddings são uma evolução condicionada aos resultados de avaliação. O modelo generalista interpreta e propõe; não determina as regras obrigatórias.

Captura automática: pedido, vigência, versões, escopos, proposta, fontes, revisões, aprovação, hash do plano, operação HCM, valores antes/depois, verificações e reversão. O especialista pode explicar correções e aprova o resultado. Só casos validados entram como soluções recuperáveis; rejeitados, revertidos e obsoletos não entram.

Memória externa e RAG não alteram pesos do modelo. Fine-tuning não está implementado nem é necessário para iniciar: avaliar somente após medir erros persistentes de comportamento que boas instruções, recuperação e ferramentas não resolvam. Usar dados curados, entradas com contexto, saídas corrigidas, separação por família e teste cego. Comparar custo total e generalização antes de escolher serviço de treinamento ou modelo aberto com LoRA/QLoRA. Dimensionar GPU somente após definir modelo, sequência e volume. Nenhuma correção treina pesos automaticamente.

## Contrato com o HCM
Escopo ABC/Férias. Consulta lê homologação e produção com credencial própria. Execução exige segredo distinto, aprovação ligada a configurações e snapshot, empresa, grupos, ambiente, vigência e versões. Modelo não recebe credenciais nem nomes/e-mails/salários de funcionários.

Operação atômica com bloqueio da empresa, validação determinística, registro de idempotência e testes dentro da transação. Falha de teste desfaz alterações. Resposta de rede perdida é retomada com o mesmo UUID, sem duplicar versões. O ERP AI persiste o identificador antes da chamada. Alteração posterior bloqueia aplicação ou reversão. A reversão preserva auditoria e restaura herança retirando as versões específicas criadas pela operação.

## Estados e aprovação
Solicitação → consulta/análise → esclarecimento ou revisão → aprovação da proposta → aplicação/testes → aprovação do resultado → memória validada. Estados explícitos para execução de resultado desconhecido, falha, verificação divergente, reversão e obsolescência. Aprovação de proposta não é aprovação do resultado de negócio. Operação pendente deve ser resolvida antes de outra análise. Alteração de revisão exige nova aprovação. Sessões concorrentes não sobrescrevem silenciosamente o atendimento.

## Testes e evidências
Testes automáticos conferem planos/agendamentos, produção preservada, valores efetivos por grupo e vigência, dependências, limites, solicitante, adiantamento, antecedência e saldo sintético. Reutilizam a política e a lógica de datas das simulações operacionais. Não criam solicitações reais ou efetuam cálculos trabalhistas. O relatório diz a origem de cada evidência. Não há formulário obrigatório de evidências manuais. O consultor revisa se o resultado atende ao requisito antes de admiti-lo na memória.

A suíte técnica cobre permissões, contexto alterado, plano adulterado, repetição, resposta perdida, rollback da transação, herança, vigências futuras e reversão bloqueada por mudança posterior. Teste integrado real usa uma geração Luna, uma alteração temporária em homologação e reversão exata; não troca modelo salvo nem publica produção. Casos técnicos revertidos não contaminam memória aprovada. Relatório em artifacts/live-acceptance.json após execução bem-sucedida.

## Avaliação funcional e evolução
Os 25 roteiros em evaluation/ são gabaritos preliminares para revisão do especialista. Não presumir que 25 casos comprovam confiabilidade ampla. Comparar o mesmo modelo com catálogo/dados e com documentação/casos recuperados; separar famílias de problemas e paráfrases entre memória e teste. Medir acerto completo, alterações indevidas, dependências, esclarecimentos, intervenções, tempo humano, latência, tokens e custo por caso aprovado. Não permitir violações de escopo ou parâmetros inventados. Comparar modelos econômicos e mais capazes nos mesmos cenários antes de recomendar com base em evidência empírica.

A entrega atual cobre o fluxo completo desse piloto. Novas empresas, módulos, ERPs, implantação pública, multiusuário, publicação automática e fine-tuning são expansões que exigem escopo e validação próprios. Não são capacidades implicitamente prometidas pela generalização da IA.

## Operação
Uso local de um especialista e uma instância do backend; segredos fora do versionamento, sessão HttpOnly/SameSite e origem verificada. Para uso público, exigir TLS, cookies Secure, identidades individuais, gestão externa de segredos, retenção e backup/restauração testados. Banco próprio preservado em volume. Revogar consulta e execução separadamente no HCM. Não usar reset_demo para atualizar. As limitações e comandos de operação atualizados ficam no README.
