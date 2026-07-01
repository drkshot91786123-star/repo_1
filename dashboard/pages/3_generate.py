import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import streamlit as st
from app import check_password
st.set_page_config(page_title="Generate", layout="wide")
if not check_password(): st.stop()
st.title("⚙️ Generate Locker Links")
st.info("Coming soon")
