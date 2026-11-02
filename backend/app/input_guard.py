"""Fast checks supplement semantic scope classification; never authorize writes.
The ERP keeps no HR vocabulary of its own: a subject is recognized by what the HCM publishes about its modules."""
import re
import unicodedata
from app.knowledge import terms

SCOPE_MESSAGE = "Este atendimento é para parametrizar o HCM. Perguntas gerais e comandos para ignorar as instruções do sistema estão fora desse objetivo. Descreva a empresa e a regra que deseja configurar."
QUESTION = r"(?:quanto|calcule|calcular|resolva|what is|calculate)"
PURPOSE = r"\bparametriza\w*"  # the assistant's own task, not an HR subject

def normalized(text):
    return " ".join(unicodedata.normalize("NFKD", text.casefold()).encode("ascii", "ignore").decode().split())

def subjects(text):
    """Stems that may name a subject; a number never does."""
    return {term for term in terms(text) if not term.isdigit()}

def vocabulary(modules):
    """Label, description and parameter labels of each module the HCM publishes."""
    return subjects(" ".join(" ".join([item.get("label", ""), item.get("description", ""), *map(str, item.get("parameters", []))]) for item in modules))

def fast_rejection(text, published):
    """published() returns the HCM's modules; it is called only for a calculation question that mentions something."""
    plain = normalized(text)
    override = r"^(?:[\s\"'`]*)(?:(?:por favor|please)\s+)?(?:ignore|desconsidere|esqueca|forget|disregard)\b.{0,140}\b(?:comandos?|instrucoes|instrucao|instructions?|commands?|prompt|mensagens? do sistema|system messages?)\b"
    if re.search(override, plain):
        return "instruction_override"
    if re.search(QUESTION + r".{0,60}\d+\s*[+*/=\-]\s*\d+", plain) and not re.search(PURPOSE, plain):
        # The question words are not a subject: what remains must match a word the HCM publishes.
        mentioned = subjects(re.sub(QUESTION + r"\w*", " ", plain))
        if not mentioned or not mentioned & vocabulary(published()):
            return "unrelated_calculation"
    return None
