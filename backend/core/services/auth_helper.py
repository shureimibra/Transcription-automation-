import json
import logging
from typing import Dict, Optional

logger = logging.getLogger("AIPipeline")


def _looks_like_json(text: str) -> bool:
    return text.startswith("{") and text.endswith("}")


def _looks_like_header_lines(text: str) -> bool:
    """True if every non-empty line has a 'Key: Value' shape."""
    lines = [line for line in text.splitlines() if line.strip()]
    return bool(lines) and all(":" in line for line in lines)


def _parse_cookie_string(text: str) -> Dict[str, str]:
    cookies: Dict[str, str] = {}
    for part in text.split(";"):
        part = part.strip()
        if "=" in part:
            key, value = part.split("=", 1)
            cookies[key.strip()] = value.strip()
    return cookies


def _parse_netscape_cookies(content: str) -> Dict[str, str]:
    """Parses a Netscape-format cookies.txt into a flat {name: value} dict
    (domain/path/expiry are ignored -- this is for a single job/URL the
    user is explicitly providing credentials for, not a general-purpose
    multi-domain cookie store)."""
    cookies: Dict[str, str] = {}
    for line in content.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split("\t")
        if len(parts) >= 7:
            name, value = parts[5], parts[6]
            cookies[name] = value
    if cookies:
        return cookies
    # Confirmed live: not every "cookies.txt" export tool actually
    # produces genuine tab-separated Netscape format -- one popular
    # extension's export was a flat `name=value; name=value; ...` string
    # instead (the same shape as a raw pasted cookie string, just
    # uploaded as a .txt file). The loop above finds zero tab-separated
    # fields for that shape and silently returns nothing, even though
    # the file plainly contains real cookie data -- which made a
    # correctly-uploaded, genuinely valid auth cookie behave exactly
    # like no auth had been provided at all, with no error or warning
    # anywhere. Falling back to the plain cookie-string parser here
    # means that shape is still recognized instead of being silently
    # discarded.
    return _parse_cookie_string(content)


def parse_auth_input(pasted_text: Optional[str] = None, cookies_txt_content: Optional[str] = None) -> Dict[str, str]:
    """
    Parses user-supplied authentication into a flat dict of extra HTTP
    headers to attach to every request for one job/discovery task. Accepts,
    in any combination:
      - An uploaded Netscape cookies.txt file (cookies_txt_content)
      - Pasted headers as a JSON object: {"Cookie": "...", "Authorization": "..."}
      - Pasted raw header lines: "Cookie: foo=bar\\nAuthorization: Bearer xyz"
      - A pasted raw cookie string: "foo=bar; baz=qux"

    All cookie-bearing sources are merged into a single Cookie header rather
    than one silently overwriting another (e.g. a cookies.txt upload plus a
    pasted extra cookie both survive). Never raises -- unrecognized input is
    treated as a raw cookie string (the most common paste), and anything
    that still parses to nothing just means no auth is attached, exactly
    like today's unauthenticated behavior.
    """
    cookies: Dict[str, str] = {}
    headers: Dict[str, str] = {}

    if cookies_txt_content:
        cookies.update(_parse_netscape_cookies(cookies_txt_content))

    pasted_text = (pasted_text or "").strip()
    if pasted_text:
        if _looks_like_json(pasted_text):
            try:
                parsed = json.loads(pasted_text)
            except json.JSONDecodeError:
                parsed = None
            if isinstance(parsed, dict):
                for key, value in parsed.items():
                    if str(key).lower() == "cookie":
                        cookies.update(_parse_cookie_string(str(value)))
                    else:
                        headers[str(key)] = str(value)
        elif _looks_like_header_lines(pasted_text):
            for line in pasted_text.splitlines():
                if ":" not in line:
                    continue
                key, value = line.split(":", 1)
                key, value = key.strip(), value.strip()
                if not key:
                    continue
                if key.lower() == "cookie":
                    cookies.update(_parse_cookie_string(value))
                else:
                    headers[key] = value
        else:
            cookies.update(_parse_cookie_string(pasted_text))

    if cookies:
        headers["Cookie"] = "; ".join(f"{k}={v}" for k, v in cookies.items())

    return headers
