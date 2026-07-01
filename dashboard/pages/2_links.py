import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import streamlit as st
from app import check_password
from lib.supabase_client import (
    get_destinations, upsert_destination,
    set_destination_active, delete_destination
)

st.set_page_config(page_title="Link Manager", layout="wide")
if not check_password():
    st.stop()

st.title("🔗 Link Manager")

# ── Add links ─────────────────────────────────────────────────────────────────
with st.expander("➕ Add new links", expanded=True):
    raw = st.text_area("Paste URLs (one per line)")
    category = st.selectbox("Category", ["entertainment", "soundy"])
    if st.button("Save links"):
        urls = [u.strip() for u in raw.splitlines() if u.strip().startswith("http")]
        if not urls:
            st.error("No valid URLs found")
        else:
            for url in urls:
                upsert_destination(url, category)
            st.success(f"Saved {len(urls)} links")
            st.rerun()

st.divider()

# ── Link table ────────────────────────────────────────────────────────────────
destinations = get_destinations()
if not destinations:
    st.info("No links yet — add some above.")
    st.stop()

for category in ["entertainment", "soundy"]:
    cat_links = [d for d in destinations if d["category"] == category]
    if not cat_links:
        continue

    st.subheader(f"{'🎬' if category == 'entertainment' else '🎵'} {category.capitalize()}")

    c1, c2 = st.columns([1, 1])
    with c1:
        if st.button(f"Select All ({category})", key=f"all_{category}"):
            for d in cat_links:
                set_destination_active(d["id"], True)
            st.rerun()
    with c2:
        if st.button(f"Deselect All ({category})", key=f"none_{category}"):
            for d in cat_links:
                set_destination_active(d["id"], False)
            st.rerun()

    for d in cat_links:
        col1, col2, col3 = st.columns([6, 1, 1])
        with col1:
            st.code(d["url"], language=None)
        with col2:
            active = st.checkbox("Active", value=d["active"], key=f"active_{d['id']}")
            if active != d["active"]:
                set_destination_active(d["id"], active)
                st.rerun()
        with col3:
            if st.button("🗑", key=f"del_{d['id']}"):
                delete_destination(d["id"])
                st.rerun()

    st.divider()
