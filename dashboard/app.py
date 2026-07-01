import streamlit as st

st.set_page_config(page_title="Automation Dashboard", layout="wide", page_icon="⚡")


def check_password() -> bool:
    if st.session_state.get("authenticated"):
        return True
    st.title("⚡ Automation Dashboard")
    pwd = st.text_input("Password", type="password")
    if st.button("Login"):
        if pwd == st.secrets["password"]:
            st.session_state["authenticated"] = True
            st.rerun()
        else:
            st.error("Wrong password")
    return False


if not check_password():
    st.stop()

st.sidebar.title("⚡ Dashboard")
st.sidebar.success("Logged in")
st.write("## Welcome — use the sidebar to navigate.")
