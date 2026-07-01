import urllib.request

TEMPLATE_HEADER = "🎯 Find your link below, Thank you for choosing us!!!!!"


def sync_to_paste_rs(destinations: list[dict]) -> str:
    lines = [TEMPLATE_HEADER, ""]
    for d in destinations:
        lines.append(d["url"])
    content = "\n".join(lines) + "\n"
    data = content.encode("utf-8")
    req = urllib.request.Request("https://paste.rs/", data=data, method="POST")
    with urllib.request.urlopen(req) as resp:
        return resp.read().decode().strip()
