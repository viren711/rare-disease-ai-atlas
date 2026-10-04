"""AI Atlas for Rare Diseases -- the app entrypoint.

    streamlit run app/streamlit_app.py            # real atlas, demo data where it is not built yet
    ATLAS_FAKE=1 streamlit run app/streamlit_app.py   # demo data only

Pages (top navigation bar, every control in the main panel):
  Home              app/views/home.py         one search box, three ways in, coverage
  Explore           app/views/explore.py      disease card, similar diseases, map, edge inspector
  Action plan       app/views/action_plan.py  disease A + neighbour B -> shared action or honest gap
  Ask the literature app/views/ask.py         cited answers from PubMed abstracts
  How it works      app/views/about.py        the pipeline, data sources and freshness, limits

Theme: a visible Auto / Light / Dark switch in the top strip of every page (app/theme.py).

The views talk to the atlas only through app/backend.py, which calls atlas.api (the contract)
and falls back to app/fixtures.py with a visible "Demo data" badge.
"""
from __future__ import annotations

import sys
from pathlib import Path

import streamlit as st

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

st.set_page_config(page_title="Rare Disease Atlas", page_icon=":material/hub:", layout="wide")

PAGES = [
    st.Page("views/home.py", title="Home", icon=":material/home:", default=True),
    st.Page("views/explore.py", title="Explore", icon=":material/travel_explore:", url_path="explore"),
    st.Page("views/action_plan.py", title="Action plan", icon=":material/route:", url_path="plan"),
    st.Page("views/ask.py", title="Ask the literature", icon=":material/menu_book:", url_path="ask"),
    st.Page("views/about.py", title="How it works", icon=":material/info:", url_path="about"),
]

page = st.navigation(PAGES, position="top")
page.run()
