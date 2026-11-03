"""What happens inside each stage of the journey, for the read-only stage view.

A stop groups recorded activities into a step a person can follow. A decision is one
LangGraph conditional edge that leaves the stage line, written as a yes/no question;
the line itself follows MAIN. Tests compare this map with the compiled graph and with
progress.ACTIVITIES, so a new route or activity cannot stay out of the interface.
Labels describe the ERP's own flow: no HR rule, prompt or business data lives here.
"""
from langgraph.graph import END

# Where the line continues after each node.
MAIN = {"guard_input": "classify", "classify": "preview", "preview": "context", "context": "alternatives", "context_expert": "plan",
        "alternatives": "choice", "choice": "remember", "remember": "plan", "plan": END}

# Pauses reached through a decision; their own routes are shown once per stage.
DETOURS = {
    "clarify": {"label": "Pergunta para você", "routes": {"guard_input": "responde e volta ao Escopo", "consultant": "ou pede o consultor"}},
    "consultant": {"label": "Consultor", "routes": {"guard_input": "resolve e reanalisa", END: "ou encerra o atendimento"}},
}


def stop(id, label, icon, tech, node=None, activities=(), steps=(), case_events=(), optional=False, caption=None):
    """caption: a recorded value shown under the label, such as "model" for the model the call used."""
    return {"type": "stop", "id": id, "label": label, "icon": icon, "tech": list(tech), "node": node, "activities": list(activities),
            "steps": list(steps), "case_events": list(case_events), "optional": optional, "caption": caption}


def model_call(node, agent, icon):
    """One model call followed step by step: the agent's question, the form sent, the wait and the check of the answer."""
    return [stop("agent", agent + " pergunta", icon, ["rules"], node, ["context_prepare"]),
            stop("form", "Formulário da chamada", "request", ["model"], node, ["model_prepare"], caption="model"),
            stop("wait", "Aguardando o modelo", "waiting", ["model"], node, ["model_request", "model_response", "output_retry"]),
            stop("answer", "Conferência da resposta", "check", ["rules"], node, ["model_decode", "output_validate"])]


def decision(node, question, answer, to, label, note, internal=False):
    """internal: a loop inside the node, not a graph edge (the plan's single automatic repair)."""
    return {"type": "decision", "node": node, "question": question, "answer": answer, "to": to, "label": label, "note": note, "internal": internal}


STAGES = [
    {"id": "request", "nodes": [], "items": [
        stop("opened", "Pedido registrado", "request", ["person"], case_events=["opened"]),
        stop("rephrased", "Pedido reformulado", "request", ["person"], case_events=["request_rephrased"], optional=True)]},
    {"id": "scope", "nodes": ["guard_input"], "exit": "guard_input", "items": [
        stop("modules", "Módulos do HCM", "modules", ["hcm"], "guard_input", ["modules_read"], optional=True),
        stop("scope_check", "Conferir o escopo", "scope", ["rules"], "guard_input", ["scope_check"]),
        decision("guard_input", "Fora do escopo?", "sim", END, "Fim do atendimento", "pedido recusado")]},
    {"id": "classification", "nodes": ["classify"], "exit": "classify", "items": [
        stop("modules", "Módulos do HCM", "modules", ["hcm"], "classify", ["modules_read"], optional=True),
        *model_call("classify", "Agente classificador", "agent:classifier"),
        decision("classify", "Fora do escopo?", "sim", END, "Fim do atendimento", "pedido recusado"),
        decision("classify", "Retomada do consultor?", "sim", "context_expert", "Contexto atualizado", "e segue direto ao plano")]},
    {"id": "context", "nodes": ["preview", "clarify", "context", "context_expert"], "exit": "context", "items": [
        stop("company", "Empresa", "company", ["hcm"], "preview", ["company_lookup"]),
        decision("preview", "Empresa identificada?", "não", "clarify", "Pergunta para você", "qual é a empresa"),
        stop("catalog", "Catálogo oficial", "catalog", ["hcm"], "context", ["catalog_read"]),
        stop("snapshot", "Situação atual", "snapshot", ["hcm"], "context", ["snapshot_read", "reference_read"]),
        stop("knowledge", "Conhecimento", "knowledge", ["database"], "context", ["knowledge_retrieve"]),
        decision("context", "Conector disponível?", "não", "consultant", "Consultor", "sem conector no HCM")]},
    {"id": "interpretations", "nodes": ["alternatives"], "exit": "alternatives", "items": [
        *model_call("alternatives", "Agente intérprete", "agent:interpreter"),
        stop("options", "Sugestão por item", "options", ["rules"], "alternatives", ["alternatives_organize", "routing"]),
        decision("alternatives", "Falta outro módulo?", "sim", "context", "Volta ao Contexto HCM", "com o módulo que faltava"),
        decision("alternatives", "Algum item possível?", "não", "consultant", "Consultor", "nada é possível no HCM")]},
    {"id": "choice", "nodes": ["choice", "remember"], "exit": "remember", "items": [
        stop("choice", "Sua escolha", "person", ["person"], "choice", steps=["choice"], case_events=["interpretation_confirmed", "interpretation_rejected"]),
        decision("choice", "Recusou a sugestão?", "sim, 1ª vez", "guard_input", "Nova análise", "desde o Escopo"),
        decision("choice", "Recusou de novo?", "sim", "consultant", "Consultor", "após duas recusas"),
        stop("intent", "Intenção registrada", "intent", ["database"], "remember", ["intent_confirm"], steps=["intent"]),
        decision("remember", "Algo a configurar?", "não", "consultant", "Consultor", "nada configurável no HCM")]},
    {"id": "configuration", "nodes": ["plan"], "items": [
        *model_call("plan", "Agente configurador", "agent:configurator")]},
    {"id": "validation", "nodes": ["plan"], "exit": "plan", "items": [
        stop("sources", "Fontes do plano", "sources", ["rules"], "plan", ["sources_validate"]),
        stop("coverage", "Itens do pedido", "options", ["rules"], "plan", ["coverage_validate"]),
        stop("official", "Validador oficial", "validator", ["hcm"], "plan", ["official_validation"]),
        decision("plan", "Rejeitado na 1ª vez?", "sim", "repair", "Correção automática", "volta ao Agente configurador", internal=True),
        decision("plan", "Plano pronto?", "não", "consultant", "Consultor", "pendências ou nova rejeição")]},
    {"id": "approval", "nodes": [], "items": [
        stop("approval", "Sua aprovação", "person", ["person"], steps=["approval"], case_events=["execution_approved"])]},
    {"id": "apply", "nodes": [], "items": [
        stop("check", "Contexto conferido", "check", ["hcm"], activities=["context_check"]),
        stop("apply", "Aplicação no HCM", "apply", ["hcm"], activities=["hcm_apply"]),
        stop("rollback", "Reversão", "rollback", ["hcm"], activities=["hcm_rollback"], optional=True)]},
    {"id": "tests", "nodes": [], "items": [
        stop("verify", "Nova consulta ao HCM", "report", ["hcm"], activities=["hcm_verify"], optional=True),
        stop("compare", "Resultado comparado", "compare", ["rules"], activities=["result_compare"])]},
    {"id": "experience", "nodes": [], "items": [
        stop("check", "Conferência final", "check", ["hcm"], activities=["context_check"]),
        stop("approval", "Sua aprovação", "person", ["person"], steps=["experience"], case_events=["validated"])]},
]


def stage_map():
    return {"stages": STAGES, "main": MAIN, "detours": DETOURS}
