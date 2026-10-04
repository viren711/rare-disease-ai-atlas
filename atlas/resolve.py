"""One search box: char-ngram TF-IDF over every synonym (MONDO, HPO, gene symbols/names, Reactome, orgs)
plus exact / prefix boosts. Tolerates typos ("Krabe"), punctuation ("X-ALD", "Niemann Pick C") and plurals.
Loads only synonyms.parquet + node labels (not the full graph), so it is fast on first use.
"""
from __future__ import annotations

import re
import threading
from pathlib import Path

import numpy as np
import pandas as pd
import yaml
from sklearn.feature_extraction.text import TfidfVectorizer

ROOT = Path(__file__).resolve().parents[1]
CFG = yaml.safe_load((ROOT / "config/atlas.yaml").read_text())
GDIR = ROOT / CFG["paths"]["graph"]

_LOCK = threading.Lock()
_IDX = None
TYPE_BOOST = {"Disease": 0.06, "Gene": 0.03, "Phenotype": 0.0, "Pathway": -0.02, "PatientOrg": -0.02}


def norm(s: str) -> str:
    s = (s or "").lower().replace("'s ", " ").replace("’s ", " ")
    return re.sub(r"[^a-z0-9]+", " ", s).strip()


class Index:
    def __init__(self):
        syn = pd.read_parquet(GDIR / "synonyms.parquet")
        nodes = pd.read_parquet(GDIR / "nodes.parquet", columns=["id", "type", "label", "group"])
        nodes = nodes[nodes.id.isin(set(syn.node_id))]
        self.label = dict(zip(nodes.id, nodes.label))
        self.group = dict(zip(nodes.id, nodes.group.fillna("")))
        syn = syn[syn.node_id.isin(self.label.keys())].reset_index(drop=True)
        syn["norm"] = syn.text.map(norm)
        syn = syn[syn.norm.str.len() > 0].reset_index(drop=True)
        self.syn = syn
        self.texts = syn.text.tolist()
        self.norms = syn.norm.tolist()
        self.node_ids = syn.node_id.tolist()
        self.types = syn.type.tolist()
        self.primary = syn.is_primary.astype(bool).to_numpy()
        self.vec = TfidfVectorizer(analyzer="char_wb", ngram_range=(2, 4), sublinear_tf=True, dtype=np.float32)
        self.X = self.vec.fit_transform([f" {n} " for n in self.norms])
        self.exact = {}
        for i, n in enumerate(self.norms):
            self.exact.setdefault(n, []).append(i)

    def search(self, q: str, types=None, k: int = 10) -> list[dict]:
        qn = norm(q)
        if not qn:
            return []
        qv = self.vec.transform([f" {qn} "])
        sims = (self.X @ qv.T).toarray().ravel()
        cand = set(np.argpartition(-sims, min(300, len(sims) - 1))[:300].tolist())
        cand |= set(self.exact.get(qn, []))
        best = {}
        qtoks = qn.split()
        for i in cand:
            s = float(sims[i])
            n = self.norms[i]
            if n == qn:
                s += 1.0
            elif n.startswith(qn + " ") or n.startswith(qn):
                s += 0.3
            elif all(any(w.startswith(t) for w in n.split()) for t in qtoks):
                s += 0.15
            if self.primary[i]:
                s += 0.03
            s += TYPE_BOOST.get(self.types[i], 0.0)
            # very long synonyms are rarely what someone typed
            s -= 0.002 * max(0, len(n) - 40)
            nid = self.node_ids[i]
            if types and self.types[i] not in types:
                continue
            if s > best.get(nid, (-1, None))[0]:
                best[nid] = (s, i)
        ranked = sorted(best.items(), key=lambda x: (-x[1][0], self.label[x[0]]))[:k]
        out = []
        for nid, (s, i) in ranked:
            if s < 0.25:
                continue
            out.append({"id": nid, "type": self.types[i], "label": self.label[nid], "matched_text": self.texts[i],
                        "score": round(s, 3), "group": self.group.get(nid, "")})
        return out


def index() -> Index:
    global _IDX
    if _IDX is None:
        with _LOCK:
            if _IDX is None:
                _IDX = Index()
    return _IDX


def search(q: str, types: list[str] | None = None, k: int = 10) -> list[dict]:
    return index().search(q, types=types, k=k)
