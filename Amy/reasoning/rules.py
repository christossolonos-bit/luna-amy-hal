"""Named inference rules. Seeded into the `rules` table so traces can cite them."""

RULES: dict[str, str] = {
    "stated": "The claim was stated directly.",
    "identity": "Every kind is trivially a kind of itself.",
    "subclass-transitivity": "If A is a kind of B and B is a kind of C, then A is a kind of C.",
    "instance-inheritance": "If x is an A and A is a kind of B, then x is a B.",
    "ownership-by-kind": "If x owns y and y is an A, then x owns an A.",
    "relation-by-kind": "If x VERBs y and y is an A, then x VERBs an A.",
    "negative-kind-inheritance": "If x VERBs no B and A is a kind of B, then x VERBs no A.",
    "disjointness": "If A is not a B, nothing that is an A is a B (and vice versa).",
    "learned-rule": "An if-then rule taught in conversation, applied by backward chaining.",
    "generic-inheritance": "What holds of a kind generally holds of its members and subkinds "
                           "(defeasible: answers using it are marked as probable).",
}
