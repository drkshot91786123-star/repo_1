import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import streamlit as st
from app import check_password
from lib.supabase_client import get_destinations, get_locker_links, insert_locker_link
from lib.paste_rs import sync_to_paste_rs

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
import importlib.util as _ilu
_spec = _ilu.spec_from_file_location(
    "create_locker",
    os.path.join(ROOT, "services/admaven/scripts/create_locker.py")
)
_mod = _ilu.module_from_spec(_spec)
_spec.loader.exec_module(_mod)
create_locker = _mod.create_locker

st.set_page_config(page_title="Generate", layout="wide")
if not check_password():
    st.stop()

st.title("⚙️ Generate Locker Links")

active = get_destinations(active_only=True)

if not active:
    st.warning("No active destinations — go to Link Manager and activate some links first.")
    st.stop()

st.subheader(f"Active destinations ({len(active)})")
for d in active:
    st.code(d["url"], language=None)

st.divider()

# ── Sync to paste.rs ──────────────────────────────────────────────────────────
if st.button("📋 Sync to paste.rs"):
    with st.spinner("Posting to paste.rs..."):
        paste_url = sync_to_paste_rs(active)
    st.success(f"Created: {paste_url}")
    st.session_state["paste_url"] = paste_url

paste_url = st.session_state.get("paste_url")
if paste_url:
    st.info(f"paste.rs URL: `{paste_url}`")

    if st.button("🔗 Generate Locker Link"):
        with st.spinner("Generating locker link..."):
            locker_url = create_locker(paste_url.rstrip("/") + ".txt")
        if locker_url:
            insert_locker_link(locker_url, paste_url)
            st.success(f"Generated: {locker_url}")
            st.session_state["paste_url"] = None
            st.rerun()
        else:
            st.error("Locker generation failed")

st.divider()

# ── Existing locker links ─────────────────────────────────────────────────────
st.subheader("Generated locker links")
links = get_locker_links()
if not links:
    st.info("No locker links yet")
else:
    for lk in links:
        col1, col2 = st.columns([5, 1])
        with col1:
            st.code(lk["locker_url"], language=None)
        with col2:
            st.caption(lk["created_at"][:10])
