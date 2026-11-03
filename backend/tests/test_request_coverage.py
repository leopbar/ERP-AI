"""One suggestion per request, item by item. The interpreter lists every rule the request asks for and how to do each one,
the suggested reading first; a doubt becomes a question the user answers; what the HCM cannot do stays out of the plan and
is registered in Não tratados. Every catalog the request needs is read, and the plan does each accepted item and nothing else.
Reproduces case 103: the three options became three pieces of the request, the catalogs of two items were never read,
choosing option 1 silently dropped them, the drawer of the assistants was guessed and the plan added a change nobody asked for."""
import copy
import hashlib
import json
from sqlalchemy import select
from test_workflow import setup, PROPOSAL  # noqa: F401
from test_orchestration import agents, post  # noqa: F401
from test_multi_module import MultiHCM, ModuleFake, ASSIGN_CATALOG
from app import orchestration as flow
from app import provider
from app.contracts import Proposal
from app.hcm import get_hcm
from app.main import app
from app.models import Case, Lesson

def catalog(module, label, field, schema):
    return {**ASSIGN_CATALOG, "version": f"{module}-1", "module": module, "module_label": label, "rules": [],
            "contract_hash": hashlib.sha256(f"{module}-1-test-contract".encode()).hexdigest(),
            "fields": [{"id": field, "label": field, "type": "array", "schema": schema}]}

WORKFLOW = catalog("workflow", "Fluxos de aprovação e SLA", "value_entry", {"type": "array", "items": {"type": "string", "enum": ["direct_manager", "hr"]}})
FIELDS = catalog("process_fields", "Informações adicionais dos processos", "movement", {"type": "array", "items": {"type": "object"}})
REQUEST = "Na ABC Ltda: 1) férias em até três parcelas; 2) lançamento de valores aprovado pelo gestor e depois pelo RH; 3) motivo obrigatório na movimentação."
CHANGES = {"R1": {"module": "vacations", "group_id": None, "parameter": "max_installments", "value": 3, "reason": "Três parcelas", "requirements": ["R1"]},
           "R2": {"module": "workflow", "parameter": "value_entry", "value": ["direct_manager", "hr"], "reason": "Gestor e depois RH", "requirements": ["R2"]},
           "R3": {"module": "process_fields", "parameter": "movement", "value": [{"name": "motivo", "type": "select", "required": True}], "reason": "Motivo obrigatório", "requirements": ["R3"]}}

def reading(label, status="change", modules=("vacations",), how="", warning=""):
    return {"label": label, "status": status, "modules": list(modules), "how": how or f"{label}: configurado em {modules[0]}.", "warning": warning}

def item(ident, text, *readings, question="", candidates=()):
    return {"id": ident, "text": text, "candidates": list(candidates), "question": question, "readings": list(readings)}

def suggestion(*items, **extra):
    return {"explanation": "Sugestão para o pedido.", "items": list(items), **extra}

PARCELAS = item("R1", "Férias em até três parcelas", reading("Três parcelas na regra geral"))
FLUXO = item("R2", "Lançamento aprovado pelo gestor e depois pelo RH", reading("Gestor e depois RH", modules=("workflow",)))
MOTIVO = item("R3", "Motivo obrigatório na movimentação", reading("Motivo obrigatório", modules=("process_fields",)))
WHOLE = suggestion(PARCELAS, FLUXO, MOTIVO)
# What case 103 got wrong, in the new contract: a numbered item silently missing from the answer.
MISSING = suggestion(PARCELAS, FLUXO)

def hcm(agents):
    client, fake, factory, calls = agents
    multi = MultiHCM()
    multi.data = fake.data
    multi.extra["workflow"] = ModuleFake(multi, "workflow", WORKFLOW, {"value_entry": ["hr"]})
    multi.extra["process_fields"] = ModuleFake(multi, "process_fields", FIELDS, {"movement": []})
    entry = {"id": "process_fields", "label": "Informações adicionais dos processos", "description": "Campos extras por processo.", "scopes": ["company"]}
    multi.modules = lambda: [*MultiHCM.modules(multi), entry]
    app.dependency_overrides[get_hcm] = lambda: multi
    return multi

def agents_answer(monkeypatch, interpret, module="vacations", related=()):
    """Classifier and interpreter; interpret(payload, round) builds the interpreter output of each call."""
    rounds = []
    def completion(config, messages, schema, *, name, **kwargs):
        payload = json.loads(messages[-1]["content"])
        if name == "module_classifier":
            value = {"module": module, "related_modules": list(related), "explanation": "Pedido com vários itens.", "company_query": "ABC Ltda", "scope": "parametrization"}
        else:
            rounds.append(payload)
            value = interpret(payload, len(rounds))
        return copy.deepcopy(value), {"mode": "model", "model": config["model"], "usage": {}, "duration_seconds": 0.1}
    monkeypatch.setattr(flow.provider, "completion", completion)
    return rounds

def planner(monkeypatch, *answers):
    """Planner answers in order, the last one repeating; records what each call received."""
    seen = []
    def generate(request, snapshot, sources, feedback, config=None, catalogs=None, snapshots=None, requirements=None):
        seen.append({"request": request, "feedback": feedback, "requirements": requirements, "catalogs": list(catalogs or {})})
        changes = answers[min(len(seen), len(answers)) - 1]
        return Proposal.model_validate({**PROPOSAL, "changes": copy.deepcopy(changes)}), {"mode": "model", "model": config["model"], "usage": {}, "duration_seconds": 0.1}
    monkeypatch.setattr(flow.provider, "generate", generate)
    return seen

def open_case(client, request=REQUEST):
    row = client.post("/api/cases", json={"request": request, "effective_from": "2026-10-31"}).json()
    return client.post(f"/api/cases/{row['id']}/orchestrate", json={"revision": row["revision"]})

def accept(client, row, **picks):
    return post(client, row, action="choose", choice="accept", readings=picks)

def test_case_103_every_item_of_the_request_is_in_the_suggestion_and_in_the_plan(agents, monkeypatch):
    client = agents[0]
    hcm(agents)
    rounds = agents_answer(monkeypatch, lambda payload, n: MISSING if n == 1 else WHOLE, related=["workflow", "process_fields"])
    seen = planner(monkeypatch, [CHANGES["R1"], CHANGES["R2"], CHANGES["R3"]])
    row = open_case(client).json()
    assert row["status"] == "awaiting_interpretation", row["workflow"].get("error")
    # Every catalog the request needs is read before the first analysis; no extension round is spent on them.
    assert set(rounds[0]["additional_catalogs"]) == {"workflow", "process_fields"} and row["workflow"]["extensions"] == []
    assert not any(e["type"] == "module_extended" for e in row["events"])
    # The answer that dropped a numbered item went back once, told so.
    assert len(rounds) == 2 and "suggestion_feedback" not in rounds[0]
    assert any("3 itens numerados, mas a sugestão tem 2" in p for p in rounds[1]["suggestion_feedback"])
    assert [m["role"] for m in row["workflow"]["metrics"]].count("interpretation_agent") == 2
    assert [entry["id"] for entry in row["workflow"]["suggestion"]["items"]] == ["R1", "R2", "R3"]
    row = accept(client, row)
    assert row["status"] == "review", row["workflow"].get("reason")
    assert seen[0]["requirements"] == ["R1", "R2", "R3"] and seen[0]["catalogs"] == ["vacations", "workflow", "process_fields"]
    assert [plan["module"] for plan in row["proposal"]["plans"]] == ["vacations", "workflow", "process_fields"]
    assert [(entry["id"], entry["status"], entry["changes"]) for entry in row["proposal"]["requirements"]] == [("R1", "change", 1), ("R2", "change", 1), ("R3", "change", 1)]
    assert row["proposal"]["not_configured"] == [] and row["approval_hash"]

def test_a_suggestion_that_keeps_leaving_items_out_is_never_shown(agents, monkeypatch):
    client = agents[0]
    hcm(agents)
    rounds = agents_answer(monkeypatch, lambda payload, n: MISSING, related=["workflow", "process_fields"])
    row = client.post("/api/cases", json={"request": REQUEST, "effective_from": "2026-10-31"}).json()
    response = client.post(f"/api/cases/{row['id']}/orchestrate", json={"revision": row["revision"]})
    assert response.status_code == 502 and "pedido inteiro" in response.json()["detail"]
    row = client.get(f"/api/cases/{row['id']}").json()
    assert row["status"] == "workflow_error" and not row["workflow"].get("suggestion") and len(rounds) == 2

def test_case_103_the_drawer_is_a_doubt_the_user_answers_and_the_plan_follows_the_answer(agents, monkeypatch):
    """'Horistas' fits the Horistas group and the job type Horário, which the Mensalistas drawer of the assistants also has.
    Case 103 guessed Horistas; here both drawers are readings and the user picks the one the assistants are in."""
    client = agents[0]
    drawer = item("R1", "Horistas: 2ª e 3ª parte das férias na folha mensal", question="Em qual gaveta estão os assistentes?", candidates=["Horistas + Horário", "Mensalistas + Horário"],
                  *[reading("Horistas + Horário", "already_in_place", how="A gaveta Horistas + Horário já manda a 2ª e a 3ª parte para a Folha mensal."),
                    reading("Mensalistas + Horário", how="A gaveta Mensalistas + Horário passa a mandar a 2ª e a 3ª parte para a Folha mensal.",
                            warning="O HCM não limita ao mesmo mês: vale sempre para a 2ª e a 3ª parte.")])
    gap = {"module": "vacations", "description": "O HCM não informa em qual gaveta estão os assistentes."}
    agents_answer(monkeypatch, lambda payload, n: suggestion(drawer, information_gaps=[gap]))
    seen = planner(monkeypatch, [{**CHANGES["R1"], "parameter": "max_installments"}])
    row = open_case(client, "Na ABC Ltda, a 2ª e a 3ª parte das férias dos horistas vão para a folha mensal.").json()
    assert row["status"] == "awaiting_interpretation" and row["workflow"]["information_gaps"] == [gap]
    row = accept(client, row, R1=1)
    assert row["status"] == "review", row["workflow"].get("reason")
    assert "Mensalistas + Horário" in seen[0]["request"] and "Horistas + Horário" not in seen[0]["request"]
    assert row["workflow"]["selected"]["readings"] == {"R1": 1} and row["workflow"]["selected"]["limitations"] == ["O HCM não limita ao mesmo mês: vale sempre para a 2ª e a 3ª parte."]
    assert [(entry["id"], entry["status"]) for entry in row["proposal"]["requirements"]] == [("R1", "change")]
    assert any(e["type"] == "interpretation_confirmed" and e["readings"] == {"R1": 1} for e in row["events"])

def test_an_item_that_may_target_several_structures_must_be_asked(agents, monkeypatch):
    client = agents[0]
    guessed = item("R1", "Horistas na folha mensal", reading("Horistas + Horário"), candidates=["Horistas + Horário", "Mensalistas + Horário"])
    asked = item("R1", "Horistas na folha mensal", reading("Horistas + Horário"), reading("Mensalistas + Horário"), question="Qual gaveta?", candidates=["Horistas + Horário", "Mensalistas + Horário"])
    rounds = agents_answer(monkeypatch, lambda payload, n: suggestion(guessed if n == 1 else asked))
    row = open_case(client, "Na ABC Ltda, horistas na folha mensal.").json()
    assert row["status"] == "awaiting_interpretation", row["workflow"].get("error")
    assert len(rounds) == 2 and any("pode valer para Horistas + Horário, Mensalistas + Horário" in p for p in rounds[1]["suggestion_feedback"])

def test_a_doubt_needs_distinct_readings_but_not_a_second_call_for_its_question(agents, monkeypatch):
    """Live run: refusing a doubt that came without its question cost a second call and a minute; the screen asks instead."""
    client = agents[0]
    vague = item("R1", "Quinze dias", reading("Pelo menos quinze"), reading("No máximo quinze", "gap"))
    rounds = agents_answer(monkeypatch, lambda payload, n: suggestion(vague))
    row = open_case(client, "Na ABC Ltda, quinze dias para pedir férias.").json()
    assert row["status"] == "awaiting_interpretation" and len(rounds) == 1
    assert flow.suggestion_problems([item("R1", "Item", reading("Igual"), reading("igual "), question="Qual?")], "") == ["O item R1 repete leituras; cada leitura precisa ser diferente."]

def test_extensions_are_counted_in_rounds_not_in_modules(agents, monkeypatch):
    """Case 103 stopped extending after its first round added two modules; a later round may still bring the next one."""
    client = agents[0]
    hcm(agents)
    selecao = item("R4", "Operacional abre pedidos só para subordinados", reading("Só subordinados diretos", modules=("assignments",)))
    def interpret(payload, n):
        loaded = set(payload["additional_catalogs"])
        if n == 1:
            return {**WHOLE, "additional_modules": ["workflow", "process_fields"]}
        assert n == 2 and loaded == {"workflow", "process_fields"} or n == 3 and loaded == {"workflow", "process_fields", "assignments"}
        return suggestion(PARCELAS, FLUXO, MOTIVO, selecao)
    rounds = agents_answer(monkeypatch, interpret)
    row = open_case(client).json()
    assert row["status"] == "awaiting_interpretation", row["workflow"].get("error")
    assert len(rounds) == 3 and row["workflow"]["extensions"] == ["workflow", "process_fields", "assignments"]
    assert row["workflow"]["modules_in_scope"] == ["vacations", "workflow", "process_fields", "assignments"]
    assert [e["modules"] for e in row["events"] if e["type"] == "module_extended"] == [["workflow", "process_fields", "assignments"]]

def test_a_reading_resting_on_a_catalog_the_case_did_not_read_is_left_out_and_said_why(agents, monkeypatch):
    client = agents[0]
    hcm(agents)
    monkeypatch.setattr(flow, "MAX_EXTENSION_ROUNDS", 0)
    agents_answer(monkeypatch, lambda payload, n: WHOLE)
    seen = planner(monkeypatch, [CHANGES["R1"]])
    row = open_case(client).json()
    assert row["status"] == "awaiting_interpretation", row["workflow"].get("error")
    items = {entry["id"]: entry["readings"][0] for entry in row["workflow"]["suggestion"]["items"]}
    assert items["R1"]["status"] == "change" and items["R2"]["status"] == "gap" and "Fluxos de aprovação e SLA, que não entrou neste atendimento" in items["R2"]["warning"]
    row = accept(client, row)
    assert row["status"] == "review" and [plan["module"] for plan in row["proposal"]["plans"]] == ["vacations"] and len(seen) == 1
    assert [entry["id"] for entry in row["proposal"]["not_configured"]] == ["R2", "R3"]

def test_a_gap_guessed_from_the_module_list_reads_that_catalog_first(agents, monkeypatch):
    """Case 103's last item was declared impossible from the module description; the catalog has the capability."""
    client = agents[0]
    hcm(agents)
    def interpret(payload, n):
        if "process_fields" not in payload["additional_catalogs"]:
            return suggestion(item("R1", "Motivo obrigatório na movimentação", reading("Sem campo obrigatório", "gap", ("process_fields",))))
        return suggestion(MOTIVO)
    rounds = agents_answer(monkeypatch, interpret)
    row = open_case(client, "Na ABC Ltda, quero motivo obrigatório na movimentação.").json()
    assert row["status"] == "awaiting_interpretation", row["workflow"].get("reason")
    assert len(rounds) == 2 and row["workflow"]["extensions"] == ["process_fields"] and "process_fields" in rounds[1]["additional_catalogs"]

def test_the_possible_part_is_planned_with_the_users_acceptance_and_the_rest_registered(agents, monkeypatch):
    """The user's decision: what the HCM cannot do does not block the rest; it is said before the acceptance and registered."""
    client = agents[0]
    yearly = item("R2", "Uma única solicitação de férias por ano", reading("Limite anual", "gap", how="O HCM não tem parâmetro para limitar solicitações por ano."))
    agents_answer(monkeypatch, lambda payload, n: suggestion(PARCELAS, yearly))
    seen = planner(monkeypatch, [CHANGES["R1"]])
    row = open_case(client, "Na ABC Ltda, três parcelas e uma única solicitação de férias por ano.").json()
    assert row["status"] == "awaiting_interpretation", row["workflow"].get("reason")
    row = accept(client, row)
    assert row["status"] == "review" and row["workflow"]["selected"]["support"] == "partial"
    assert any("Itens gap ficam fora do plano" in line for line in seen[0]["feedback"])
    assert [(entry["id"], entry["status"], entry["changes"]) for entry in row["proposal"]["requirements"]] == [("R1", "change", 1), ("R2", "gap", 0)]
    assert row["proposal"]["not_configured"] == [{"id": "R2", "text": "Uma única solicitação de férias por ano", "module": "vacations", "how": "O HCM não tem parâmetro para limitar solicitações por ano."}]
    listed = client.get("/api/unhandled/capabilities").json()
    assert [(entry["description"], entry["case_ids"], entry["open"]) for entry in listed] == [("O HCM não tem parâmetro para limitar solicitações por ano.", [row["id"]], 1)]
    assert row["id"] not in {entry["id"] for entry in client.get("/api/unhandled").json()}

def test_nothing_possible_goes_to_the_consultant_without_a_plan(agents, monkeypatch):
    client = agents[0]
    yearly = item("R1", "Uma única solicitação de férias por ano", reading("Limite anual", "gap", how="O HCM não tem parâmetro para limitar solicitações por ano."))
    agents_answer(monkeypatch, lambda payload, n: suggestion(yearly))
    seen = planner(monkeypatch, [CHANGES["R1"]])
    row = open_case(client, "Na ABC Ltda, uma única solicitação de férias por ano.").json()
    assert row["status"] == "consultant_queue" and row["workflow"]["category"] == "missing_capability" and not seen and not row["proposal"]
    assert row["workflow"]["capabilities"] == [{"module": "vacations", "description": "O HCM não tem parâmetro para limitar solicitações por ano."}]

def test_picking_only_readings_the_hcm_cannot_do_plans_nothing(agents, monkeypatch):
    client = agents[0]
    agents_answer(monkeypatch, lambda payload, n: suggestion(item("R1", "Quinze dias", reading("Pelo menos quinze"), reading("No máximo quinze", "gap", how="Sem antecedência máxima."), question="Como contar?")))
    seen = planner(monkeypatch, [CHANGES["R1"]])
    row = accept(client, open_case(client, "Na ABC Ltda, quinze dias para pedir férias.").json(), R1=1)
    assert row["status"] == "consultant_queue" and not seen and row["workflow"]["capabilities"] == [{"module": "vacations", "description": "Sem antecedência máxima."}]

def test_a_plan_that_leaves_an_item_out_is_not_applied(agents, monkeypatch):
    client, fake, factory, calls = agents
    agents_answer(monkeypatch, lambda payload, n: suggestion(item("R1", "Três parcelas", reading("Três parcelas")), item("R2", "Antecedência de 45 dias", reading("45 dias na regra geral"))))
    seen = planner(monkeypatch, [CHANGES["R1"]])
    before = copy.deepcopy(fake.data)
    row = accept(client, open_case(client, "Na ABC Ltda, três parcelas e 45 dias de antecedência.").json())
    assert row["status"] == "consultant_queue" and row["workflow"]["category"] == "plan_incomplete" and not row["proposal"]
    assert len(seen) == 2 and any("não seguiu a interpretação confirmada" in line and "R2" in line for line in seen[1]["feedback"])
    assert row["workflow"]["handoff_summary"]["blocked_by"].startswith("O plano gerado deixou itens do pedido de fora")
    assert fake.data == before
    with factory() as db:
        assert "plan_incomplete" in {lesson.kind for lesson in db.scalars(select(Lesson)).all()}

def test_a_change_the_accepted_suggestion_does_not_foresee_goes_back_to_the_planner(agents, monkeypatch):
    """Case 103 also raised CFG-GESTORES, in another module, for an item the user meant only for the vacation notice."""
    client = agents[0]
    hcm(agents)
    agents_answer(monkeypatch, lambda payload, n: suggestion(PARCELAS), related=["workflow"])
    seen = planner(monkeypatch, [CHANGES["R1"], {**CHANGES["R2"], "requirements": ["R1"]}], [CHANGES["R1"]])
    row = accept(client, open_case(client, "Na ABC Ltda, quero férias em até três parcelas.").json())
    assert row["status"] == "review", row["workflow"].get("reason")
    assert len(seen) == 2 and any("value_entry (workflow) não faz parte" in line for line in seen[1]["feedback"])
    assert [plan["module"] for plan in row["proposal"]["plans"]] == ["vacations"]
    assert row["proposal"]["requirements"] == [{"id": "R1", "text": "Férias em até três parcelas", "status": "change", "how": "Três parcelas na regra geral: configurado em vacations.", "changes": 1}]

def test_items_already_in_place_need_no_change_and_are_listed_in_the_plan(agents, monkeypatch):
    client = agents[0]
    agents_answer(monkeypatch, lambda payload, n: suggestion(item("R1", "Três parcelas", reading("Três parcelas")), item("R2", "Aprovação do RH", reading("Já é obrigatória", "already_in_place"))))
    planner(monkeypatch, [CHANGES["R1"]])
    row = accept(client, open_case(client, "Na ABC Ltda, três parcelas com aprovação do RH.").json())
    assert row["status"] == "review", row["workflow"].get("reason")
    assert [(entry["id"], entry["status"], entry["changes"]) for entry in row["proposal"]["requirements"]] == [("R1", "change", 1), ("R2", "already_in_place", 0)]

def test_information_the_hcm_does_not_publish_is_registered_without_stopping_the_case(agents, monkeypatch):
    """Case 103: the HCM shows the calculation groups and job types, not who is in each; the case goes on and the gap is listed."""
    client = agents[0]
    hcm(agents)
    gap = {"module": "vacation_policy", "description": "O HCM não informa em qual grupo de cálculo e tipo de cargo estão os assistentes."}
    agents_answer(monkeypatch, lambda payload, n: {**WHOLE, "information_gaps": [gap]}, related=["workflow", "process_fields"])
    row = open_case(client).json()
    assert row["status"] == "awaiting_interpretation" and row["workflow"]["information_gaps"] == [gap]
    listed = client.get("/api/unhandled/capabilities").json()
    assert [(entry["module"], entry["description"], entry["case_ids"], entry["open"]) for entry in listed] == [(gap["module"], gap["description"], [row["id"]], 0)]
    assert row["id"] not in {entry["id"] for entry in client.get("/api/unhandled").json()}

def test_a_redirect_keeps_the_other_modules_of_the_case(agents, monkeypatch):
    client = agents[0]
    hcm(agents)
    def interpret(payload, n):
        if n == 1:
            return suggestion(item("R1", "Férias em até três parcelas", reading("Não há parcelas aqui", "gap")), suggested_module="vacations")
        return suggestion(PARCELAS)
    agents_answer(monkeypatch, interpret, module="assignments", related=["workflow"])
    row = open_case(client, "Na ABC Ltda, quero férias em até três parcelas.").json()
    assert row["status"] == "awaiting_interpretation", row["workflow"].get("reason")
    assert row["workflow"]["redirects"] == ["assignments"] and row["workflow"]["modules_in_scope"] == ["vacations", "workflow"]

def test_a_case_paused_on_the_old_three_options_asks_for_a_new_analysis(agents, monkeypatch):
    client, fake, factory, calls = agents
    agents_answer(monkeypatch, lambda payload, n: suggestion(PARCELAS))
    row = open_case(client, "Na ABC Ltda, quero férias em até três parcelas.").json()
    with factory() as db:
        case = db.get(Case, row["id"])
        case.workflow = {**case.workflow, "suggestion": {}}
        db.commit()
    row = client.get(f"/api/cases/{row['id']}").json()
    response = client.post(f"/api/cases/{row['id']}/workflow", json={"revision": row["revision"], "action": "choose", "choice": "accept"})
    assert response.status_code == 409 and "formato anterior" in response.json()["detail"]

def test_item_ids_written_with_their_text_are_read_back(agents, monkeypatch):
    """Live run: with the id capped at 8 characters, a model writing 'R1 — a aprovação...' was cut into 'R1 — a审批' and refused."""
    ids = ["R1", "R2", "R3"]
    assert [flow.item_id(v, ids) for v in ("R1 — a审批", "r2", "3", "R2 – texto", "R9 — outro")] == ["R1", "R2", "R3", "R2", "R9 — outro"]
    assert flow.item_id("R1 — Para horistas, a segunda parte") == "R1" and flow.item_id("Item A", ["Item A"]) == "Item A"
    client = agents[0]
    hcm(agents)
    noisy = suggestion(*[{**entry, "id": f"{entry['id']} — {entry['text']}"} for entry in WHOLE["items"]])
    rounds = agents_answer(monkeypatch, lambda payload, n: noisy, related=["workflow", "process_fields"])
    row = open_case(client).json()
    assert row["status"] == "awaiting_interpretation" and len(rounds) == 1, row["workflow"].get("error")
    assert [entry["id"] for entry in row["workflow"]["suggestion"]["items"]] == ["R1", "R2", "R3"]

def test_list_dimensions_show_every_field_that_tells_items_apart():
    """Live run: 'horistas' matched the Horistas group only; the index also puts the Horário job type, which the drawer
    of the assistants (Mensalistas + Horário) has, in front of the interpreter."""
    entry = lambda group, kinds, sheet: {"calculation_group": group, "job_types": kinds, "sheet_occurrence_2": sheet, "sheet_normal": "Folha de férias", "flag": True, "simulation_sheet_1": ""}
    rules = {"notice_min_days": 45, "general_configs": [entry("Mensalistas", ["Mensal"], "Folha complementar"), entry("Horistas", ["Horário"], "Folha mensal"), entry("Mensalistas", ["Horário"], "Folha complementar")]}
    assert flow.list_dimensions({"effective": {"homologation": [{"configuration": rules}]}}) == {
        "general_configs": {"calculation_group": ["Horistas", "Mensalistas"], "job_types": ["Horário", "Mensal"], "sheet_occurrence_2": ["Folha complementar", "Folha mensal"]}}
    assert flow.list_dimensions({"effective": {"homologation": [{"configuration": {"max_installments": 3}}]}}) == {}

def test_what_the_user_accepts_becomes_the_reading_the_plan_follows():
    items = [item("R1", "Gaveta", reading("Horistas", "already_in_place"), reading("Mensalistas"), question="Qual?"),
             item("R2", "Motivo", reading("Movimentação", modules=("process_fields", "workflow"))),
             item("R3", "Uma por ano", reading("Limite anual", "gap", how="Sem limite anual."))]
    chosen = flow.accepted(items, {"R1": 1, "R2": 7, "R9": 0})
    assert chosen["readings"] == {"R1": 1, "R2": 0, "R3": 0} and chosen["support"] == "partial"
    assert chosen["coverage"] == [{"requirement": "R1", "status": "change", "module": "vacations", "how": "Mensalistas: configurado em vacations."},
                                  {"requirement": "R2", "status": "change", "module": "process_fields", "how": "Movimentação: configurado em process_fields."},
                                  {"requirement": "R2", "status": "change", "module": "workflow", "how": "Movimentação: configurado em process_fields."},
                                  {"requirement": "R3", "status": "gap", "module": "vacations", "how": "Sem limite anual."}]
    assert chosen["not_configured"] == [{"id": "R3", "text": "Uma por ano", "module": "vacations", "how": "Sem limite anual."}]
    assert flow.accepted(items[2:], {})["support"] == "unsupported" and flow.accepted(items[:2], {})["support"] == "supported"

def test_readings_the_case_cannot_do_become_gaps_that_say_why():
    label = {"workflow": "Fluxos de aprovação e SLA", "timekeeping": "Controle de ponto"}.get
    items = [item("R1", "Fluxo", reading("Gestor e RH", modules=("workflow",))), item("R2", "Ponto", reading("Sem horas extras", modules=("timekeeping",))),
             item("R3", "Outro", reading("Nada", modules=("other",))), item("R4", "Férias", reading("Três parcelas"))]
    flow.settle_readings(items, {"vacations"}, {"vacations", "timekeeping"}, label)
    assert [(entry["id"], entry["readings"][0]["status"], entry["readings"][0]["warning"]) for entry in items] == [
        ("R1", "gap", "Depende de Fluxos de aprovação e SLA, que não entrou neste atendimento."), ("R2", "gap", "Controle de ponto ainda não possui conector de parametrização."),
        ("R3", "gap", "Nenhum módulo do HCM configura isso."), ("R4", "change", "")]

def test_numbered_items_are_counted_once():
    assert flow.numbered_items("Quero:\n1. a\n2. b\n\n3. c") == {"1", "2", "3"}
    assert flow.numbered_items("Na ABC: 1) a; 2) b; 3) c; 4) d; 5) e.") == {"1", "2", "3", "4", "5"}
    assert flow.numbered_items("Aviso de 45 dias (art. 135) e até 15 dias antes; R$ 50.000,00.") == set()

def test_coverage_checks():
    coverage = [{"requirement": "R1", "status": "change", "module": "vacations", "how": "Três parcelas."}, {"requirement": "R2", "status": "already_in_place", "module": "vacations", "how": "Já é assim."}]
    requirements = [{"id": "R1", "text": "Férias em até três parcelas"}, {"id": "R2", "text": "Aprovação"}]
    unscoped = {"parameter": "max_installments", "value": 3, "requirements": ["R1"]}  # no module: the case's primary module
    assert flow.plan_coverage_problems([unscoped], requirements, coverage, "vacations") == []
    assert flow.plan_coverage_problems([{**unscoped, "requirements": ["R2"]}], requirements, coverage, "vacations") == [
        "A alteração de max_installments (vacations) não faz parte do que a interpretação confirmada prevê para R2; retire-a.",
        "O item R1 (Férias em até três parcelas) ficou sem alteração no plano."]

def test_planner_prompt_has_no_hr_rule_and_cites_items_only_when_asked():
    assert "somente aprovação pelo RH" not in provider.SYSTEM_PROMPT and "gestor imediato" not in provider.SYSTEM_PROMPT
    from test_workflow import CATALOG, base_snapshot
    sources = [{"id": "catalog:vacations-2", "content": CATALOG}]
    variants = provider.proposal_schema(base_snapshot(), sources, requirements=["R1", "R2"])["properties"]["changes"]["items"]["anyOf"]
    assert all(v["properties"]["requirements"] == {"type": "array", "items": {"type": "string", "enum": ["R1", "R2"]}} and "requirements" in v["required"] for v in variants)
    plain = provider.proposal_schema(base_snapshot(), sources)["properties"]["changes"]["items"]["anyOf"]
    assert all("requirements" not in v["properties"] for v in plain)
