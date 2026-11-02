# Regras de construção do ERP AI

## O HCM é intocável

- **Proibido alterar qualquer coisa no HCM** (`C:\projects\HCM`): código, testes, documentação, configuração, dados. Nem "só o texto", nem "só a descrição", nem "só o adaptador de integração".
- O HCM é um sistema pronto, em funcionamento, que só nos oferece uma API de comunicação (`/api/integration/v2`). Tratamos esse sistema exatamente como trataríamos o produto de um fornecedor: usamos o que a API publica, do jeito que ela publica.
- Toda limitação encontrada se resolve **no ERP**: no cliente HCM, nos agentes, na validação, na interface ou na documentação. Se a API do HCM não oferece algo, o ERP registra a lacuna em Não tratados e segue; não se abre exceção.
- Se uma mudança no HCM parecer indispensável, pare e explique ao usuário o que falta na API e por quê; a decisão é dele e não é tomada por padrão.

## Por que

Em 02/11/2026 o classificador escolheu o módulo errado por causa de uma palavra na descrição que o HCM publica, e a primeira reação foi reescrever essa descrição no HCM. Isso esconde uma fraqueza do ERP que voltaria com qualquer fornecedor real. O ERP precisa ser robusto ao que recebe.

## Outras regras

- Os agentes não carregam conhecimento de regras de RH no código ou nos prompts: módulos, campos, esquemas, escopos, precedência e regras vêm do catálogo publicado pela API.
- Nenhuma escrita no HCM sem aprovação humana do plano exato, hash de aprovação e verificação de contexto; tudo em homologação; produção nunca.
- Segredos (chaves de API, tokens, senhas) nunca entram em prompts, checkpoints, logs ou respostas.
- Testes: `docker compose exec -T -e DATABASE_URL=sqlite:// backend python -m pytest -q`; aceitação real com os scripts em `scripts/`, sempre reversíveis.
- Commits só com autorização explícita do usuário, sem trailer de coautoria.
