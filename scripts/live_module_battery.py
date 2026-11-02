"""Live classification battery (real model, no HCM writes). Usage: python scripts/live_module_battery.py [round2] [module,module]

Runs one realistic request per HCM module through the real agents (classification + interpretations only),
records the chosen module, redirects and supported interpretations, and rejects each technical case."""
import json, sys, pathlib, http.cookiejar, urllib.request, time
root = pathlib.Path(__file__).resolve().parents[1]
lines = (root / ".local-access.txt").read_text(encoding="utf-8").splitlines()
user = next(l.split(": ", 1)[1] for l in lines if l.startswith("Usuário:"))
password = next(l.split(": ", 1)[1] for l in lines if l.startswith("Senha:"))
base = "http://127.0.0.1:8001"
opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
def call(path, payload=None, timeout=300):
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(base + path, data=data, headers={"Origin": "http://localhost:3001", "Content-Type": "application/json"}, method="POST" if data is not None else "GET")
    try:
        with opener.open(req, timeout=timeout) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        return {"error": e.code, "detail": e.read().decode("utf-8", "replace")}

BATTERY = [
    ("vacations", "Na ABC Tecnologia, exigir antecedência mínima de 45 dias para pedir férias."),
    ("vacations", "Permitir que os funcionários da ABC Tecnologia peçam adiantamento do 13º junto com as férias."),
    ("vacation_policy", "Na ABC Tecnologia, não permitir a venda de dias de férias (abono pecuniário)."),
    ("vacation_policy", "Na ABC Tecnologia, exigir que o colaborador dê ciência do aviso de férias."),
    ("workflow", "Na ABC Tecnologia, as férias devem ser aprovadas primeiro pelo gestor imediato e depois pelo RH, com prazo de 8 horas úteis em cada etapa."),
    ("workflow", "Na ABC Tecnologia, o prazo de SLA para aprovar afastamentos deve ser de 24 horas úteis."),
    ("timekeeping", "Na ABC Tecnologia, aumentar a tolerância de atraso no ponto para 15 minutos."),
    ("timekeeping", "Na ABC Tecnologia, exigir aprovação do RH para registros de ponto."),
    ("admission", "Na ABC Tecnologia, permitir admissões em finais de semana."),
    ("admission", "Na ABC Tecnologia, a admissão deve ser solicitada com pelo menos 10 dias de antecedência."),
    ("payroll", "Na ABC Tecnologia, deixar de exibir o aviso de folha demonstrativa."),
    ("benefits", "Na ABC Tecnologia, não permitir que o próprio funcionário solicite benefícios."),
    ("termination", "Na ABC Tecnologia, o aviso prévio base deve ser de 30 dias com 3 dias adicionais por ano trabalhado, até 60 dias."),
    ("transport", "Na ABC Tecnologia, o desconto do vale-transporte deve ser de 4% do salário."),
    ("dependents", "Na ABC Tecnologia, limitar a 5 dependentes por funcionário e exigir CPF do dependente."),
    ("personal_data", "Na ABC Tecnologia, o funcionário não pode alterar o próprio endereço pelo portal."),
    ("leave", "Na ABC Tecnologia, afastamentos podem ser lançados com até 60 dias retroativos."),
    ("movement", "Na ABC Tecnologia, movimentações não podem dar aumento salarial acima de 20%."),
    ("requisition", "Na ABC Tecnologia, requisições de pessoal não precisam respeitar o quadro de vagas."),
    ("recess", "Na ABC Tecnologia, estagiários devem pedir recesso com 20 dias de antecedência."),
    ("value_entry", "Na ABC Tecnologia, limitar o lançamento de valores a 50 mil reais por lançamento."),
    ("recruitment", "Na ABC Tecnologia, exigir requisição aprovada antes de abrir uma vaga e reter dados de candidatos por 180 dias."),
    ("contracting", "Na ABC Tecnologia, o contrato de experiência deve ser de 60 dias, prorrogável por mais 30."),
    ("work_schedule", "Na ABC Tecnologia, a jornada prevista é das 8h às 17h, de segunda a sexta."),
    ("assignments", "Na ABC Tecnologia, gestores só podem abrir solicitações para si mesmos e para subordinados diretos."),
    ("config_assignments", "Na ABC Tecnologia, atribuir a configuração nomeada CFG-GESTORES também ao grupo Administrativo em Férias."),
    ("process_fields", "Na ABC Tecnologia, incluir um campo obrigatório 'centro de custo' nas solicitações de afastamento."),
    ("payroll_setup", "Na ABC Tecnologia, criar o grupo de cálculo 'Aprendizes' na folha."),
]
ROUND2 = [
    ("workflow", "As férias do grupo Gestores da ABC Tecnologia precisam de uma etapa extra de aprovação pelo administrador."),
    ("workflow", "Requisições de pessoal na ABC Tecnologia devem ser aprovadas só pelo administrador, com 48 horas úteis."),
    ("vacations", "Quero que o grupo Administrativo da ABC Tecnologia possa pedir férias com 15 dias de antecedência."),
    ("vacations", "Bloquear o processo de férias na ABC Tecnologia por enquanto."),
    ("vacation_policy", "Férias na ABC Tecnologia não podem começar nos dois dias antes de feriado."),
    ("timekeeping", "Proibir horas extras na ABC Tecnologia."),
    ("benefits", "Desligar o processo de benefícios na ABC Tecnologia."),
    ("admission", "Na ABC Tecnologia não exigir documentos completos na admissão."),
    ("recruitment", "Na ABC Tecnologia, as etapas do processo seletivo devem ser Triagem, Entrevista com gestor e Proposta."),
    ("config_assignments", "Na ABC Tecnologia, atribuir a configuração nomeada CFG-GESTORES também ao grupo Administrativo em Férias."),
]
if len(sys.argv) > 1 and sys.argv[1] == "round2":
    BATTERY = ROUND2
    sys.argv = sys.argv[:1]
only = set(sys.argv[1].split(",")) if len(sys.argv) > 1 else None
call("/api/login", {"username": user, "password": password})
results = []
for expected, text in BATTERY:
    if only and expected not in only:
        continue
    started = time.monotonic()
    row = call("/api/cases", {"request": text, "effective_from": "2026-11-02"})
    c = call(f"/api/cases/{row['id']}/orchestrate", {"revision": row["revision"]})
    record = {"expected": expected, "request": text, "case": row["id"], "seconds": round(time.monotonic() - started, 1)}
    if "workflow" not in c:
        record.update(status="ERRO", detail=str(c)[:200])
    else:
        w = c["workflow"]
        supported = [o for o in w.get("options", []) if o["support"] == "supported"]
        record.update(status=c["status"], module=c.get("module"), redirects=w.get("redirects") or [], supported=len(supported), options=len(w.get("options", [])),
                      first=(w.get("options") or [{}])[0].get("title"), level=(w.get("options") or [{}])[0].get("target_level"), reason=(w.get("reason") or "")[:160], category=w.get("category") if c["status"] == "consultant_queue" else "")
        latest = call(f"/api/cases/{row['id']}")
        if latest["status"] not in ("rejected",):
            call(f"/api/cases/{row['id']}/review", {"revision": latest["revision"], "decision": "reject", "comment": "Bateria técnica de classificação por módulo; sem aplicação."})
    results.append(record)
    ok = record.get("module") == expected and record.get("status") == "awaiting_interpretation"
    print(f"{'OK ' if ok else 'XX '} {expected:18} -> {record.get('module')!s:18} {record.get('status')!s:24} sup={record.get('supported')} redir={record.get('redirects')} | {record.get('first')!s:60} | {record.get('reason')[:110] if record.get('reason') else ''}")
call("/api/logout", {})
(root / "artifacts").mkdir(exist_ok=True)
out = root / "artifacts" / "module-battery.json"
out.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
good = sum(1 for r in results if r.get("module") == r["expected"] and r.get("status") == "awaiting_interpretation")
print(f"\n{good}/{len(results)} pedidos chegaram a interpretações no módulo esperado. Relatório: {out}")
