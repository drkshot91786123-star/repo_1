import streamlit as st
from supabase import create_client, Client


def get_client() -> Client:
    url = st.secrets["supabase"]["url"]
    key = st.secrets["supabase"]["key"]
    return create_client(url, key)


def get_destinations(active_only: bool = False) -> list[dict]:
    q = get_client().table("destinations").select("*").order("created_at", desc=True)
    if active_only:
        q = q.eq("active", True)
    return q.execute().data


def upsert_destination(url: str, category: str) -> dict:
    return get_client().table("destinations").upsert(
        {"url": url, "category": category},
        on_conflict="url"
    ).execute().data[0]


def set_destination_active(id: str, active: bool) -> None:
    get_client().table("destinations").update({"active": active}).eq("id", id).execute()


def delete_destination(id: str) -> None:
    get_client().table("destinations").delete().eq("id", id).execute()


def get_locker_links() -> list[dict]:
    return get_client().table("locker_links").select("*").order("created_at", desc=True).execute().data


def insert_locker_link(locker_url: str, paste_rs_url: str) -> dict:
    return get_client().table("locker_links").insert(
        {"locker_url": locker_url, "paste_rs_url": paste_rs_url}
    ).execute().data[0]


def upsert_run_logs(logs: list[dict]) -> None:
    if not logs:
        return
    get_client().table("run_logs").upsert(logs, on_conflict="id").execute()


def get_run_logs(filters: dict | None = None) -> list[dict]:
    q = get_client().table("run_logs").select("*").order("created_at", desc=True).limit(5000)
    if filters:
        if filters.get("source"):
            q = q.eq("source", filters["source"])
        if filters.get("success") is not None:
            q = q.eq("success", filters["success"])
    return q.execute().data
