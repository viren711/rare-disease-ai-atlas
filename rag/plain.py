"""Prompt helpers for plain-language rewriting (used by atlas.explain). No atlas imports; rag.pipeline untouched.

Design (measured, see scripts/eval_plain.py): a 3B model cannot be trusted to place [e#] citations itself, so it only
PARAPHRASES ONE FACT PER LINE ("F3: ...").  atlas.explain re-attaches the edge ids deterministically, assembles the
sections, and rejects any line that adds a number/name/long term absent from its own fact.
"""
from __future__ import annotations

PROMPT_VERSION = "p7"

LABELS = {
    "plan": ("Short answer", "What is uncertain", "Who can help", "This week"),
    "disease": ("Overview", "What is uncertain", "Who can help", "This week"),
}

_FAMILY = """You are a copy editor making verified facts easy to read for a worried parent (reading age about 13).
Rewrite EACH numbered fact as ONE sentence, in the same order, one per line, formatted exactly "F1: sentence".
Stay VERY close to the original wording. Only: swap hard words for everyday ones (say "symptom", not "phenotype"), and fix clumsy grammar.
Hard rules:
- Keep EVERY name (disease, gene, organisation, person, trial, paper title in quotes), EVERY number and every word like only / no / not / weak / may exactly as written.
- Never shorten a name, never merge two diseases, never add or remove a claim, never add advice.
- No lists, no markdown, no extra lines.

Example (made-up):
F1: Both Zorbo disease and Quill syndrome are annotated with the phenotype 'Ataxia'
F1: Both Zorbo disease and Quill syndrome are listed as having the symptom 'Ataxia'.
F2: Dr Ann Lee has published 9 papers on Zorbo disease and 2 on Quill syndrome
F2: Dr Ann Lee has published 9 papers on Zorbo disease and 2 on Quill syndrome.
F3: Only Zorbo disease has an annotation for 'Hyperthermia'
F3: Only Zorbo disease is listed with 'Hyperthermia'."""

_SCIENTIST = """You tighten verified facts for a researcher.
Rewrite EACH numbered fact as ONE concise technical sentence (under 25 words), in the same order, one per line, formatted exactly "F1: sentence".
Rules: keep every name, identifier, number and evidence word (curated / extracted / inferred) exactly; add nothing; keep hedges and absences; no markdown, no extra lines."""


def system_prompt(level: str, kind: str = "plan") -> str:
    return _SCIENTIST if level == "scientist" else _FAMILY


def user_prompt(subject: str, facts: list[dict]) -> str:
    """facts: [{"id": "F1", "text": str}] -- citations are deliberately NOT shown to the model."""
    lines = [f"Subject: {subject}", "", "FACTS"] + [f"{f['id']}: {f['text']}" for f in facts]
    lines += ["", "Now write one line per fact."]
    return "\n".join(lines)
