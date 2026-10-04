"""Sections shared by Explore and the Action plan: epidemiology, funding, variants, collaborators, assets.

Data comes from atlas.api through app/backend.py; a missing or empty piece shows a plain empty state.
Keys relied on (real atlas.api shapes, mirrored in app/fixtures.py):

  card/plan epidemiology   {"prevalence": str, "onset": [str], "inheritance": [str], "source", "source_url"}
                           (plan: {"a": {...}, "b": {...}})
  funding(id)              {"grants": [{"id","title","pi","org","years","amount","url","active","edge_id"}],
                            "total", "total_active", "gap": bool, "gap_note", "broader_class_grants": [...], "source"}
  plan["funding"]          {"a": {"n_grants","n_active","gap","label","top": [grant]}, "b": {...}|None,
                            "shared_grants": [grant], "shared_pis": [..], "gap_a", "gap_b", "gap_notes": [str]}
  variants(id)             {"counts": {"pathogenic","vus","benign","conflicting","total"}, "genes": [{"label",...}],
                            "top": [{"label"|"hgvs","gene","significance","stars","type","url","conditions"}],
                            "has_data", "source"}
  researchers_for(id)      [{"id","label","score","papers","grants","active_grants","trials","affiliation","orcid",
                            "why": [str], "contact": {"orcid_url","note"}, "identity", "homonym_risk"}]
  assets_for(id)           {"trials": [], "registries": [], "natural_history": [], "observational": [], "models": []}
                           rows {"id","label","status","sponsor","url","stopped","why_stopped","broad"}
"""
from __future__ import annotations

import streamlit as st

from app import backend, ui


def _link(text: str, url: str | None) -> str:
    t = ui.esc(text)
    return f'<a href="{ui.esc(url)}" target="_blank" rel="noopener">{t}</a>' if url else t


def _join(v) -> str:
    return ", ".join(str(x) for x in v) if isinstance(v, list) else str(v or "")


# ---------------------------------------------------------------------------
# Epidemiology
# ---------------------------------------------------------------------------


def epi_fields(d: dict | None) -> list[tuple[str, str]]:
    if not isinstance(d, dict):
        return []
    out = []
    for label, key in (("Prevalence", "prevalence"), ("Typical onset", "onset"), ("Inheritance", "inheritance")):
        v = _join(d.get(key))
        if v:
            out.append((label, v))
    return out


def _epi_source(d: dict | None) -> str:
    if isinstance(d, dict) and d.get("source"):
        return f"Source: {_link(d['source'], d.get('source_url'))}"
    return ""


def epidemiology_block(did: str, epi: dict | None = None) -> None:
    """Three stat tiles on the disease card: how common, when it starts, how it is inherited."""
    fields = epi_fields(epi)
    if not fields:
        return
    for col, (k, v) in zip(st.columns(len(fields), gap="small"), fields):
        col.markdown(f"<div class='ra-stat'><div class='ra-k'>{ui.gloss(ui.esc(k))}</div>"
                     f"<div class='ra-v' style='font-size:1rem'>{ui.esc(v)}</div></div>", unsafe_allow_html=True)
    src = _epi_source(epi)
    if src:
        st.markdown(f"<span class='ra-sub'>{src}</span>", unsafe_allow_html=True)


def epidemiology_compare(epi, a_label: str, b_label: str | None) -> None:
    """Side-by-side prevalence / onset / inheritance for A and B on the action plan."""
    side_a = epi.get("a") if isinstance(epi, dict) and isinstance(epi.get("a"), dict) else epi
    side_b = epi.get("b") if isinstance(epi, dict) and isinstance(epi.get("b"), dict) else {}
    fa, fb = dict(epi_fields(side_a)), dict(epi_fields(side_b))
    labels = [k for k in ("Prevalence", "Typical onset", "Inheritance") if k in fa or k in fb]
    if not labels:
        ui.empty_state("No epidemiology recorded for these diseases.",
                       "Prevalence, onset and inheritance appear here when the atlas has them.")
        return
    heads = ["", a_label] + ([b_label] if b_label else [])
    rows = [[f"<b>{ui.gloss(ui.esc(k))}</b>", ui.esc(fa.get(k, "not recorded"))]
            + ([ui.esc(fb.get(k, "not recorded"))] if b_label else []) for k in labels]
    st.markdown(ui.html_table(heads, rows), unsafe_allow_html=True)
    srcs = " · ".join(x for x in (_epi_source(side_a), _epi_source(side_b)) if x)
    if srcs:
        st.markdown(f"<span class='ra-sub'>{srcs}</span>", unsafe_allow_html=True)


# ---------------------------------------------------------------------------
# Funding
# ---------------------------------------------------------------------------


def _unavailable(fn: str, what: str) -> None:
    if backend.has_api(fn):
        ui.empty_state(f"No {what} found for this disease in the atlas.",
                       "That is a gap in what is recorded, not proof there is none.")
    else:
        ui.empty_state(f"{what.capitalize()} are not available in this build of the atlas yet.",
                       "The rest of the page works; this section fills in when the data is added.")


def _grant_table(grants: list[dict], limit: int = 12) -> None:
    rows = [[_link(g.get("title") or "Untitled grant", g.get("url")), ui.esc(g.get("pi") or ""),
             ui.esc(g.get("org") or ""), ui.esc(g.get("years") or ""), ui.money(g.get("amount"), "USD"),
             ui.plain_chip("active", "good") if g.get("active") else ui.plain_chip("ended", "muted")]
            for g in grants[:limit]]
    st.markdown(ui.html_table(["Grant", "Lead", "Organisation", "Years", "Amount", "Status"], rows),
                unsafe_allow_html=True)
    if len(grants) > limit:
        st.caption(f"... and {len(grants) - limit} more grants")


def funding_section(did: str, data: dict | None = None) -> None:
    """NIH grants for one disease, with the funding gap said out loud."""
    data = backend.funding(did) if data is None else data
    grants = ui.as_list(data, "grants")
    gap = isinstance(data, dict) and data.get("gap")
    if not grants and not gap:
        _unavailable("funding", "grants")
        return
    c1, c2, c3 = st.columns(3, gap="small")
    total = data.get("total", len(grants))
    c1.markdown(f"<div class='ra-stat'><div class='ra-k'>Grants found</div><div class='ra-v'>{total}</div></div>",
                unsafe_allow_html=True)
    c2.markdown(f"<div class='ra-stat'><div class='ra-k'>Active now</div><div class='ra-v'>"
                f"{data.get('total_active', 0)}</div></div>", unsafe_allow_html=True)
    amt = sum(g.get("amount") or 0 for g in grants if g.get("active"))
    c3.markdown(f"<div class='ra-stat'><div class='ra-k'>Active awards (shown)</div><div class='ra-v'>"
                f"{ui.money(amt, 'USD')}</div></div>", unsafe_allow_html=True)
    if gap:
        ui.banner("warn", "Funding gap", ui.esc(data.get("gap_note") or "No active grant found for this disease."))
    if grants:
        _grant_table(sorted(grants, key=lambda g: (not g.get("active"), -(g.get("amount") or 0))))
    broader = ui.as_list(data, "broader_class_grants")
    if broader:
        with st.expander(f"{len(broader)} grants fund the broader disease family, not this disease by name"):
            _grant_table(broader, 8)
    if data.get("source"):
        st.caption(f"Source: {data['source']}")


def plan_funding(fund: dict | None, a_id: str) -> None:
    """Funding on the action plan: both diseases side by side, shared grants and investigators, gaps."""
    if not isinstance(fund, dict) or not isinstance(fund.get("a"), dict):
        funding_section(a_id)
        return
    sides = [s for s in (fund.get("a"), fund.get("b")) if isinstance(s, dict)]
    for col, side in zip(st.columns(len(sides), gap="small"), sides):
        col.markdown(f"<div class='ra-stat'><div class='ra-k'>{ui.esc(side.get('label') or '')}</div>"
                     f"<div class='ra-v'>{side.get('n_grants', 0)} grants, {side.get('n_active', 0)} active</div>"
                     + (f"<div>{ui.plain_chip('funding gap', 'warn')}</div>" if side.get("gap") else "")
                     + "</div>", unsafe_allow_html=True)
    for note in fund.get("gap_notes") or []:
        ui.banner("warn", "Funding gap", ui.esc(note))
    if fund.get("shared_grants"):
        st.markdown("**Grants that cover both diseases**")
        _grant_table(fund["shared_grants"])
    if fund.get("shared_pis"):
        def _pi_name(x):
            if isinstance(x, dict):
                aff = (x.get("affiliation") or "").split(",")[0].strip()
                return f"{x.get('label') or x.get('id')}" + (f" ({aff})" if aff else "")
            return str(x)
        st.markdown("**Investigators funded on both:** " + ui.esc("; ".join(_pi_name(x) for x in fund["shared_pis"][:6])))
    for side in sides:
        if side.get("top"):
            with st.expander(f"Top grants: {side.get('label')}"):
                _grant_table(side["top"], 8)


# ---------------------------------------------------------------------------
# Variants
# ---------------------------------------------------------------------------


def variants_section(did: str, data: dict | None = None) -> None:
    """ClinVar variants for the disease's genes: counts by class, genes, and the most reliable variants."""
    data = backend.variants(did) if data is None else data
    top = ui.as_list(data, "top", "variants")
    counts = data.get("counts") if isinstance(data, dict) else None
    if not top and not (counts or {}).get("total"):
        _unavailable("variants", "variants")
        return
    counts = counts or {}
    tiles = [("pathogenic", "Pathogenic", "bad"), ("vus", "Uncertain (VUS)", "warn"),
             ("conflicting", "Conflicting", "warn"), ("benign", "Benign", "good")]
    for col, (k, lbl, _) in zip(st.columns(4, gap="small"), tiles):
        col.markdown(f"<div class='ra-stat'><div class='ra-k'>{lbl}</div><div class='ra-v'>"
                     f"{counts.get(k, 0):,}</div></div>", unsafe_allow_html=True)
    genes = ui.as_list(data, "genes")
    if genes:
        st.markdown(ui.html_table(
            ["Gene", "Pathogenic", "VUS", "Benign", "All variants"],
            [[f"<b>{ui.esc(g.get('label'))}</b>", f"{g.get('n_pathogenic', 0):,}", f"{g.get('n_vus', 0):,}",
              f"{g.get('n_benign', 0):,}", f"{g.get('n_variants_total', g.get('n_pathogenic', 0)):,}"]
             for g in genes[:8]]), unsafe_allow_html=True)
    if top:
        st.markdown("**Best-reviewed variants**")
        rows = []
        for v in top[:15]:
            sig = str(v.get("significance") or "")
            low = sig.lower()
            tone = "bad" if "pathogenic" in low and "uncertain" not in low else "warn" if sig else "neutral"
            stars = int(v.get("stars") or 0)
            rows.append([_link(v.get("label") or v.get("hgvs") or "variant", v.get("url")),
                         ui.esc(v.get("gene") or ""), ui.plain_chip(sig, tone) if sig else "",
                         f"<span title='ClinVar review stars'>{'★' * stars}{'☆' * (4 - stars)}</span>",
                         ui.esc(v.get("type") or "")])
        st.markdown(ui.html_table(["Variant", "Gene", "Clinical significance", "Review", "Type"], rows),
                    unsafe_allow_html=True)
    if isinstance(data, dict) and data.get("source"):
        st.caption(f"Source: {data['source']}. Variant classes are the submitters' and are not a diagnosis.")


# ---------------------------------------------------------------------------
# Collaborators
# ---------------------------------------------------------------------------


def collaborators_section(did: str, data: list | None = None) -> None:
    people = ui.as_list(backend.researchers_for(did) if data is None else data, "researchers")
    if not people:
        _unavailable("researchers_for", "collaborators")
        return
    st.caption("Ranked by papers, grants and studies on this disease. The reason is shown with each person.")
    for i, r in enumerate(people[:10]):
        with st.container(border=True):
            top, score = st.columns([4, 1.6], vertical_alignment="center")
            contact = r.get("contact") or {}
            orcid_url = contact.get("orcid_url") or (ui.orcid_url(r["orcid"]) if r.get("orcid") else None)
            top.markdown(f"<b>{i + 1}.</b> " + ui.type_chip("Researcher", r.get("label") or "?")
                         + (f" &nbsp;{_link('ORCID', orcid_url)}" if orcid_url else ""), unsafe_allow_html=True)
            if isinstance(r.get("score"), (int, float)):
                score.markdown(ui.conf_bar(r["score"], "Fit"), unsafe_allow_html=True)
            aff = r.get("affiliation") or contact.get("affiliation")
            if aff:
                st.markdown(f"<span class='ra-sub'>{ui.esc(aff)}</span>", unsafe_allow_html=True)
            why = r.get("why")
            why = "; ".join(map(str, why)) if isinstance(why, list) else why
            if why:
                st.markdown(f"**Why:** {ui.esc(why)}")
            tags = [f"{r[k]} {lbl}" for k, lbl in (("papers", "papers"), ("grants", "grants"), ("trials", "studies"))
                    if r.get(k)]
            chips = [ui.plain_chip(t, "neutral") for t in tags]
            if r.get("homonym_risk"):
                chips.append(ui.plain_chip("name shared with others: verify identity", "warn",
                                           "The atlas could not confirm this person by ORCID."))
            if chips:
                ui.chips(chips)
            if contact.get("note"):
                st.caption(contact["note"])


# ---------------------------------------------------------------------------
# Assets, grouped by kind
# ---------------------------------------------------------------------------

ASSET_GROUPS = [("trials", "Clinical trials"), ("registries", "Registries"),
                ("natural_history", "Natural-history studies"), ("observational", "Observational studies"),
                ("models", "Disease models")]


def assets_section(did: str, data: dict | None = None) -> None:
    data = backend.assets_for(did) if data is None else data
    if isinstance(data, list):  # tolerate a flat list
        grouped: dict[str, list] = {}
        for x in data:
            grouped.setdefault({"trial": "trials", "registry": "registries"}.get(x.get("kind"), x.get("kind") or "trials"),
                               []).append(x)
        data = grouped
    if not isinstance(data, dict) or not any(data.get(k) for k, _ in ASSET_GROUPS):
        _unavailable("assets_for", "studies and registries")
        return
    for key, title in ASSET_GROUPS:
        rows = data.get(key) or []
        if not rows:
            continue
        st.markdown(f"##### {title} ({len(rows)})")
        for x in rows[:15]:
            url = x.get("url") or ui.node_url(x.get("id"))
            line = (ui.type_chip("Trial", x.get("id") or "") + _link(x.get("label") or x.get("id"), url) + " "
                    + ui.plain_chip(str(x.get("status") or "status unknown").replace("_", " ").capitalize()))
            if x.get("stopped"):
                line += ui.plain_chip("stopped", "bad", x.get("why_stopped") or "Stopped early")
            if x.get("broad"):
                line += ui.plain_chip("broad condition list", "muted", "Lists many conditions; relevance is weaker.")
            st.markdown(line + (f"<br><span class='ra-sub'>{ui.esc(x['sponsor'])}</span>" if x.get("sponsor") else ""),
                        unsafe_allow_html=True)
        if len(rows) > 15:
            st.caption(f"... and {len(rows) - 15} more")
