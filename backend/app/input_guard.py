"""Fast checks supplement semantic scope classification; never authorize writes."""
import re
import unicodedata

SCOPE_MESSAGE = "Este atendimento é para parametrizar o HCM. Perguntas gerais e comandos para ignorar as instruções do sistema estão fora desse objetivo. Descreva a empresa e a regra que deseja configurar."

def normalized(text):
    return " ".join(unicodedata.normalize("NFKD", text.casefold()).encode("ascii", "ignore").decode().split())

def fast_rejection(text):
    plain = normalized(text)
    override = r"^(?:[\s\"'`]*)(?:(?:por favor|please)\s+)?(?:ignore|desconsidere|esqueca|forget|disregard)\b.{0,140}\b(?:comandos?|instrucoes|instrucao|instructions?|commands?|prompt|mensagens? do sistema|system messages?)\b"
    if re.search(override, plain):
        return "instruction_override"
    domain = r"\b(?:ferias|folha|ponto|admissao|beneficios?|horas? extras?|jornada|intervalo|antecedencia|parametriza\w*)\b"
    math = r"(?:quanto|calcule|calcular|resolva|what is|calculate).{0,60}\d+\s*[+*/=\-]\s*\d+"
    if re.search(math, plain) and not re.search(domain, plain):
        return "unrelated_calculation"
    return None
