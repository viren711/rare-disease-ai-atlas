"""Prompt construction and answer generation (healthathon rag/generate.py, prompt rewritten
for patient-group leaders: plain language, cite every sentence, say what is unknown)."""

from __future__ import annotations

import re

from rag.config import load_config

# Each abstract is capped in the prompt: prompt prefill dominates latency on CPU.
MAX_SOURCE_WORDS = 230

SYSTEM_PROMPT = """You help leaders of rare-disease patient groups understand published \
research. Your reader is a parent or patient advocate, not a doctor: write in plain, \
everyday language, explain any medical term in a few words the first time you use it, \
and keep sentences short.

You answer ONLY from the numbered SOURCES in the user message. They are abstracts of \
research papers from PubMed about lysosomal and peroxisomal diseases. Do not add facts \
from memory, even if you are confident they are correct.

RULE 1 -- ANSWER THE QUESTION THAT WAS ASKED. Read the SOURCES, pick out what bears on \
the question, and explain it in your own words. Do not copy whole sentences from the \
abstracts. Leave out details that do not help answer the question.

RULE 2 -- CITE EVERY SENTENCE. Every sentence must end with the number of the source it \
comes from in square brackets, like [1], or [2][3] when two sources support it. A \
sentence without a bracket is deleted before anyone sees it. Never cite a number that is \
not in the SOURCES.

RULE 3 -- BE HONEST ABOUT EVIDENCE. Say how strong the evidence looks when the source \
says it (for example "in a study of 12 patients" or "in mice"). Do not call something a \
cure or say it works for everyone unless the source says so. If an important part of the \
question is not answered by the SOURCES, end with one short sentence naming that specific \
gap in your own words; otherwise do not add such a sentence.

RULE 4 -- Use this token ONLY when none of the SOURCES is about the subject of the \
question (for example the question asks for a price, a phone number or a future event):
{sentinel}
Reply with just the token in that case. If the SOURCES are about the subject, even if they \
answer only part of the question, answer from them instead and do not use the token.

RULE 5 -- Do not give medical advice for a specific person. Keep numbers (doses, ages, \
percentages, patient counts) exactly as the SOURCES give them.

RULE 6 -- LENGTH. Keep the whole answer under {max_words} words and finish your last \
sentence. A short question deserves a short answer; use a few short paragraphs or \
bullet points for bigger questions (every bullet ends with its [n])."""


USER_TEMPLATE = """SOURCES
{sources}

QUESTION
{question}

Write a plain-language answer to the QUESTION using what the SOURCES say. End EVERY \
sentence with its source number in square brackets, like [1] or [2][3]."""


def _clip_words(text: str, n: int) -> str:
    words = text.split()
    if len(words) <= n:
        return text.strip()
    return " ".join(words[:n]).rstrip(",;:") + " ..."


def format_sources(hits: list) -> str:
    blocks = []
    for i, hit in enumerate(hits, start=1):
        c = hit.chunk
        bits = [b for b in (c.get("journal"), str(c.get("year") or "")) if b]
        header = f"[{i}] {c.get('title', '').strip()} ({', '.join(bits)}; PMID {c.get('pmid')})"
        body = c["text"]
        title = (c.get("title") or "").strip()
        if title and body.startswith(title.rstrip(".")):
            body = body[len(title.rstrip(".")):].lstrip(". ")
        blocks.append(f"{header}\n{_clip_words(body, MAX_SOURCE_WORDS)}")
    return "\n\n".join(blocks)


def build_prompt(question: str, hits: list) -> tuple[str, str]:
    cfg = load_config()
    sentinel = cfg["guard"]["citation"]["sentinel"]
    system = SYSTEM_PROMPT.format(sentinel=sentinel,
                                  max_words=cfg["llm"].get("max_answer_words", 300))
    user = USER_TEMPLATE.format(sources=format_sources(hits), question=question.strip(),
                                sentinel=sentinel)
    return system, user


def generate(question: str, hits: list, client=None) -> str:
    from rag.llm import LLMClient

    client = client or LLMClient.from_config()
    system, user = build_prompt(question, hits)
    return client.chat(system, user)


_MULTI_CITE = re.compile(r"\[(\d{1,2}(?:\s*(?:,|;|-|–|and)\s*\d{1,2})+)\]")
_PMID_CITE = re.compile(r"[\[(]\s*PMID[:\s]*(\d{1,9})\s*[\])]", re.IGNORECASE)


_LEADING_CITES = re.compile(r"^(\s*(?:[-*\u2022]\s+)?)((?:\[\d{1,2}\]\s*)+)(\S.*)$")
_FIRST_STOP = re.compile(r"([.!?])(?=\s|$)")


def _move_leading_markers(line: str) -> str:
    """'[1] [2] Text one. Text two.' -> 'Text one [1][2]. Text two.'"""
    m = _LEADING_CITES.match(line)
    if not m:
        return line
    lead, marks, rest = m.groups()
    marks = "".join(re.findall(r"\[\d{1,2}\]", marks))
    stop = _FIRST_STOP.search(rest)
    if stop:
        return f"{lead}{rest[:stop.start()]} {marks}{rest[stop.start():]}"
    return f"{lead}{rest.rstrip()} {marks}"


def normalize_citations(text: str, hits: list) -> str:
    """Rewrite citation variants small models produce into the [n] form layer 3 checks:
    [1, 2] / [1-3] -> [1][2] / [1][2][3]; [PMID 12345] -> [n] when that PMID was supplied."""
    def multi(m: re.Match) -> str:
        body = m.group(1)
        nums: list[int] = []
        for part in re.split(r"\s*(?:,|;|and)\s*", body):
            if re.search(r"[-–]", part):
                a, b = (int(x) for x in re.split(r"\s*[-–]\s*", part)[:2])
                nums.extend(range(a, b + 1) if 0 < b - a < 10 else [a, b])
            elif part.strip().isdigit():
                nums.append(int(part))
        return "".join(f"[{n}]" for n in nums)

    text = _MULTI_CITE.sub(multi, text)
    text = "\n".join(_move_leading_markers(ln) for ln in text.split("\n"))
    by_pmid = {str(h.chunk.get("pmid")): i for i, h in enumerate(hits, start=1)}
    return _PMID_CITE.sub(lambda m: f"[{by_pmid[m.group(1)]}]" if m.group(1) in by_pmid else m.group(0),
                          text)


# --- length fitting (verbatim from healthathon)--------------------------------------------

# A sentence ends at .!? -- the citation marker may sit either side of the stop
# ("... border [1]." or "... border. [1]"), so the terminator swallows following markers.
_SENTENCE_END = re.compile(r"(?<=[.!?])[\"')\]]*(?:\s*\[\d{1,2}\])*(?=\s|$)")
_LIST_ITEM = re.compile(r"^\s*(?:[-*•–]|\d{1,2}[.)])\s+")


def _split_units(text: str) -> list[tuple[str, str]]:
    """Break an answer into (separator_before, unit) pieces it is safe to cut at:
    lines, with non-list lines further split into sentences."""
    units: list[tuple[str, str]] = []
    for i, line in enumerate(text.split("\n")):
        sep = "" if i == 0 else "\n"
        if not line.strip():
            units.append((sep, ""))
            continue
        if _LIST_ITEM.match(line):
            units.append((sep, line))
            continue
        pos, first = 0, True
        for m in _SENTENCE_END.finditer(line):
            piece = line[pos: m.end()]
            if piece.strip():
                units.append((sep if first else " ", piece.strip()))
                first = False
            pos = m.end()
        tail = line[pos:].strip()
        if tail:
            units.append((sep if first else " ", tail))
    return units


def fit_to_length(text: str, max_words: int) -> tuple[str, bool]:
    """Trim to `max_words`, cutting only at a sentence or bullet end. Returns (text, trimmed)."""
    if max_words <= 0 or not text.strip():
        return text, False
    if len(text.split()) <= max_words:
        return text, False
    kept: list[tuple[str, str]] = []
    words = 0
    for sep, unit in _split_units(text):
        n = len(unit.split())
        if kept and words + n > max_words:
            break
        kept.append((sep, unit))
        words += n
    while len(kept) > 1 and kept[-1][1].rstrip().endswith(":"):
        kept.pop()
    if not kept:
        return text, False
    out = "".join(sep + unit for sep, unit in kept).strip()
    if not _CITE_IN_ANSWER.search(out):
        return text, False
    return out, True


_CITE_IN_ANSWER = re.compile(r"\[\d{1,2}\]")
