# Segurança da comunicação ERP AI ↔ HCM em produção

Revisão de 03/11/2026, feita só com leitura: nada foi alterado no HCM.

## O caminho

O backend do ERP (contêiner em `/opt/erp-ai`) chama `https://hcm.lbai.dev/api/integration/v2`.
A chamada sai do contêiner, chega ao IP público da própria máquina, passa pelo nginx do host
(que termina o TLS) e entra no backend do HCM em `127.0.0.1:8090`. Os dois sistemas estão no
mesmo VPS: o tráfego não atravessa a internet, mas é cifrado do mesmo jeito, e o ERP usa o HCM
exatamente como qualquer outro cliente usaria.

## O que está certo

- TLS 1.2/1.3 com certificado Let's Encrypt. O cliente do ERP (`httpx`) valida o certificado e
  não segue redirecionamentos.
- Token aleatório de 48 bytes em `Authorization: Bearer` para consultas e uma chave de execução
  separada em `X-HCM-Execution-Key`, exigida só para aplicar e reverter. O HCM guarda apenas o
  SHA-256 de cada um e compara com `hmac.compare_digest`; com o hash vazio a integração fica
  desligada.
- Credenciais de produção distintas das locais. Ficam em `/opt/erp-ai/.env` (root, modo 600),
  fora do Git; o ERP não as escreve em prompts, eventos, logs ou respostas, e as mensagens de
  erro do cliente HCM não ecoam cabeçalhos.
- Backends do ERP e do HCM escutam só em `127.0.0.1`; os bancos não publicam porta.
- Do lado do ERP: cookie `Secure`/`HttpOnly`/`SameSite=Strict`, verificação de `Origin`,
  limite de login no nginx (dez por minuto por IP) e no backend (que lê o IP real pelo
  `X-Forwarded-For` do nginx), HSTS e cabeçalhos de segurança.

## Limites que não dependem do ERP

- Do lado do ERP, a proteção das credenciais vem do tamanho do token, de credenciais exclusivas
  de produção e da rotação: trocar o valor no `.env` do ERP e o hash correspondente no HCM.
- O nginx do HCM corta respostas acima de 120 s; o cliente do ERP espera 45 s, portanto é o
  mais restritivo dos dois.

## Registro no HCM e observações fora do escopo

- O HCM só reconhece o ERP quando `HCM_INTEGRATION_TOKEN_SHA256` e
  `HCM_EXECUTION_TOKEN_SHA256` constam no `.env` dele. É configuração do HCM, por isso só
  foi feita em 03/11/2026 com a decisão explícita do usuário: as duas linhas foram
  acrescentadas, apenas o backend do HCM foi recriado e a consulta do ERP aos módulos passou
  a responder 200. Qualquer troca das credenciais do ERP exige repetir esse registro, de
  novo só com a decisão do usuário.
