#!/usr/bin/env bash
# Atualiza o ERP AI no VPS com a última versão da main e reconstrói os contêineres.
# Chamado pelo GitHub Actions (chave SSH restrita a este script) ou manualmente.
# Tudo fica dentro de main() para o bash ler o arquivo inteiro antes do git reset trocá-lo.
# Os logs do Actions de um repositório público são públicos: o detalhe vai para um arquivo
# só do root no VPS, e a saída deste script traz apenas a versão e o resultado.
set -Eeuo pipefail

main() {
  local app_dir=/opt/erp-ai
  local log_file=/var/log/erp-ai-deploy.log
  cd "$app_dir"
  exec 9>/tmp/erp-ai-deploy.lock
  flock 9

  touch "$log_file"
  chmod 600 "$log_file"
  trap 'echo "O deploy falhou. Detalhes em $log_file no VPS." >&2' ERR
  echo "====== Deploy $(date -u '+%Y-%m-%dT%H:%M:%SZ') ======" >>"$log_file"

  git fetch --quiet origin main >>"$log_file" 2>&1
  git reset --quiet --hard origin/main >>"$log_file" 2>&1
  echo "Versão: $(git log -1 --format='%h %s')"

  # A configuração do modelo feita na interface fica aqui, fora do Git e só para o root.
  install -d -m 700 secrets >>"$log_file" 2>&1
  docker compose -p erp-ai -f compose.prod.yaml up -d --build --remove-orphans >>"$log_file" 2>&1
  docker image prune -f >>"$log_file" 2>&1

  local port
  port=$(grep -E '^BACKEND_PORT=' .env | cut -d= -f2 || true)
  for _ in $(seq 1 60); do
    if curl -fsS "http://127.0.0.1:${port:-8091}/health" >/dev/null 2>&1; then
      echo "Deploy concluído."
      return 0
    fi
    sleep 2
  done
  docker compose -p erp-ai -f compose.prod.yaml logs --tail 50 backend >>"$log_file" 2>&1 || true
  echo "O backend não respondeu em /health. Detalhes em $log_file no VPS." >&2
  trap - ERR
  return 1
}

main "$@"
exit
