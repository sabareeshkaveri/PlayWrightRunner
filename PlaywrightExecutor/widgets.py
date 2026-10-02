from __future__ import annotations

from html import escape


def error_page(message: str) -> bytes:
    safe_message = escape(message)
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>PlayRunner error</title>
  <style>
    body {{ margin:0; padding:48px 20px; background:#f4f6fc; color:#20243a;
      font:16px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif; }}
    main {{ max-width:620px; margin:auto; padding:28px; border:1px solid #e2e5f0;
      border-radius:16px; background:white; box-shadow:0 14px 38px #303b6812; }}
    h1 {{ margin-top:0; color:#d34b60; font-size:22px; }}
    p {{ overflow-wrap:anywhere; }}
  </style>
</head>
<body><main><h1>PlayRunner could not start</h1><p>{safe_message}</p></main></body>
</html>""".encode("utf-8")
