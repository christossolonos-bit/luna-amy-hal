"""Chooses the communicative intent for a turn and the propositions to express."""

from reasoning.proofs import Answer

ACKNOWLEDGE, ANSWER, CLARIFY, DESCRIBE = "acknowledge", "answer", "clarify", "describe"


def plan_answer(answer: Answer) -> dict:
    query = answer.query
    intent = DESCRIBE if query is not None and query.relation == "describe" else ANSWER
    many = intent == DESCRIBE or (query is not None and query.slot is not None)
    return {
        "intent": intent,
        "status": answer.status,
        "slot": query.slot if query else None,
        "support": [p.steps for p in (answer.support if many else answer.support[:1])],
        "against": [p.steps for p in answer.against[:1]],
        "gaps": answer.missing,
    }
