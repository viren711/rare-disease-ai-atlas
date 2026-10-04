"""Light / dark / auto theming for the whole app.

Streamlit's own theme (config.toml [theme.light] / [theme.dark]) follows the OS. The visible toggle in
the top bar adds a user override on top of it:

  auto   follow Streamlit's native theme (the OS setting)           -> palette only
  light  force the light palette                                    -> palette + widget overrides
  dark   force the dark palette                                     -> palette + widget overrides

Every custom colour in the app (node types, evidence types, chips, cards, banners, the graph canvas,
edge and label colours) comes from PALETTES below. They reach CSS as variables on :root (--ra-*) and
reach Python-built widgets (the agraph map) through `palette()`. The palettes reproduce the values in
.streamlit/config.toml so a forced theme and the native one look identical.

Colour-blind safety: node types use the Okabe-Ito hues AND a distinct shape (see ui.NODE_SHAPES);
evidence types use line style (solid / dashed / dotted) AND lightness, never hue alone.
"""
from __future__ import annotations

from pathlib import Path

import streamlit as st

MODES = ["Auto", "Light", "Dark"]
STATE_KEY = "theme_pref"  # persisted choice: "auto" | "light" | "dark"

PALETTES: dict[str, dict[str, str]] = {
    "light": {  # bright, warm white; ink-teal text
        "bg": "#FFFCF7", "surface": "#F7F2EA", "surface2": "#EFE8DC", "border": "#D9CFBF",
        "text": "#16282B", "muted": "#4F6064", "faint": "#586A6D",
        "accent": "#0F766E", "accent_hover": "#0B5F59", "on_accent": "#FFFFFF", "accent_soft": "#D5EEEA",
        "link": "#0B5F8F",
        "good": "#17703A", "warn": "#9A5B00", "bad": "#A52A2A", "info": "#0B5F8F",
        "good_bg": "#E3F3E8", "warn_bg": "#FBEDD3", "bad_bg": "#F8E0E0", "info_bg": "#DDEBF6",
        "canvas": "#FBF8F2", "canvas_border": "#D9CFBF", "node_border": "#16282B", "label": "#16282B",
        "label_stroke": "#FBF8F2",
        # node types (Okabe-Ito derived, darkened for a light ground)
        "t_Disease": "#0B5FA5", "t_Gene": "#B04A85", "t_Phenotype": "#A06400", "t_Pathway": "#00785A",
        "t_Trial": "#C24E00", "t_PatientOrg": "#6B4FB3", "t_Researcher": "#13789A", "t_Paper": "#6B7280",
        # evidence types: lightness + line style
        "e_curated": "#2A3D41", "e_extracted": "#5C7075", "e_inferred": "#8A5A9E",
    },
    "dark": {  # deep teal-ink ground, luminous accents
        "bg": "#0A1A1E", "surface": "#112429", "surface2": "#17303A", "border": "#2B4851",
        "text": "#E7F2F0", "muted": "#A5BDBD", "faint": "#8CA6A8",
        "accent": "#2DD4BF", "accent_hover": "#5EEAD4", "on_accent": "#06201F", "accent_soft": "#12403F",
        "link": "#7CC4F0",
        "good": "#5ED38C", "warn": "#F2B84B", "bad": "#FF8A8A", "info": "#7CC4F0",
        "good_bg": "#12301F", "warn_bg": "#3A2C0E", "bad_bg": "#3D1818", "info_bg": "#12304A",
        "canvas": "#0D2227", "canvas_border": "#2B4851", "node_border": "#E7F2F0", "label": "#E7F2F0",
        "label_stroke": "#0D2227",
        "t_Disease": "#5BA8F0", "t_Gene": "#E58CC0", "t_Phenotype": "#F0B429", "t_Pathway": "#34C89A",
        "t_Trial": "#F08A4B", "t_PatientOrg": "#A99BF0", "t_Researcher": "#5CC8EA", "t_Paper": "#9CA3AF",
        "e_curated": "#D5E6E4", "e_extracted": "#93AEB1", "e_inferred": "#D2A2E6",
    },
}

_CSS_FILE = Path(__file__).parent / "style.css"


def choice() -> str:
    """The user's pick: auto | light | dark."""
    v = st.session_state.get(STATE_KEY, "auto")
    return v if v in ("auto", "light", "dark") else "auto"


def native() -> str:
    """Streamlit's own current theme ('light' or 'dark'); light when it cannot be read."""
    try:
        t = st.context.theme.type
        return t if t in ("light", "dark") else "light"
    except Exception:  # noqa: BLE001 - outside a request / old Streamlit
        return "light"


def resolved() -> str:
    c = choice()
    return native() if c == "auto" else c


def palette(mode: str | None = None) -> dict[str, str]:
    return PALETTES[mode or resolved()]


def css_vars(p: dict[str, str]) -> str:
    return ";".join(f"--ra-{k.replace('_', '-')}:{v}" for k, v in p.items())


def stylesheet() -> str:
    """<style> content for the current run: palette variables, the app CSS, and (only when the chosen
    theme differs from Streamlit's native one) the widget overrides that repaint Streamlit's own chrome."""
    mode, nat = resolved(), native()
    p = PALETTES[mode]
    css = _CSS_FILE.read_text(encoding="utf-8")
    force = css.split("/*FORCE*/", 1)[1] if "/*FORCE*/" in css else ""
    base = css.split("/*FORCE*/", 1)[0]
    out = f":root{{{css_vars(p)};color-scheme:{mode}}}\niframe{{color-scheme:{nat}}}\n{base}"
    if mode != nat:
        out += "\n" + force
    return out


def _on_change() -> None:
    v = st.session_state.get("theme_widget")
    st.session_state[STATE_KEY] = (v or "Auto").lower()


def toggle() -> None:
    """The visible Auto / Light / Dark switch (lives in the top bar of every page)."""
    st.session_state.setdefault(STATE_KEY, "auto")
    st.session_state["theme_widget"] = choice().capitalize()
    st.segmented_control(
        "Theme", MODES, key="theme_widget", on_change=_on_change, label_visibility="collapsed", required=True,
        help="Colour theme: Auto follows your device, or force Light or Dark.",
    )
