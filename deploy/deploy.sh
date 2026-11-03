#!/usr/bin/env bash
# Atualiza o ERP AI no VPS com a última versão da main e reconstrói os contêineres.
# Chamado pelo GitHub Actions (chave SSH restrita a este script) ou manualmente.
# Tudo fica dentro de main() para o bash ler o arquivo inteiro antes do git reset trocá-lo.
set -euo pipefail

main() {
  local app_dir=/opt/erp-ai
  cd "$app_dir"
  exec 9>/tmp/erp-ai-deploy.lock
  flock 9

  git fetch --quiet origin main
  git reset --quiet --hard origin/main
  echo "Versão: $(git log -1 --format='%h %s')"

  # A configuração do modelo feita na interface fica aqui, fora do Git e só para o root.
  install -d -m 700 secrets
  docker compose -p erp-ai -f compose.prod.yaml up -d --build --remove-orphans
  docker image prune -f >/dev/null

  local port
  port=$(grep -E '^BACKEND_PORT=' .env | cut -d= -f2 || true)
  for _ in $(seq 1 60); do
    if curl -fsS "http://127.0.0.1:${port:-8091}/health" >/dev/null 2>&1; then
      echo "Deploy concluído."
      return 0
    fi
    sleep 2
  done
  echo "O backend não respondeu em /health." >&2
  docker compose -p erp-ai -f compose.prod.yaml logs --tail 50 backend >&2
  return 1
}

main "$@"
exit
