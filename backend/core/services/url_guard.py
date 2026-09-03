"""
Refusing to fetch a URL that points back inside the network.

THE ATTACK THIS STOPS. This platform accepts a URL and fetches it
server-side. Without a check, "transcribe this" is also "make a request from
inside my network to anywhere you like, and show me what came back":

    http://169.254.169.254/latest/meta-data/iam/security-credentials/
    http://localhost:8002/api/v1/...          (our own admin surface)
    http://10.0.0.5/                          (whatever else is on the LAN)

The first of those is the one that matters on a cloud box: most providers
serve instance credentials from that address to anything that asks, and an
SSRF is the standard way to reach it.

WHY THIS MODULE EXISTS RATHER THAN THE CHECK THAT WAS ALREADY THERE.
`dynamic_discovery.candidates.is_blocked_host` implements almost exactly
this and is well tested -- and an audit found it is called from NOWHERE but
its own tests. A guard nothing invokes is documentation. This wraps it,
adds the parts it was missing, and gives every outbound fetch of a
user-supplied URL one function to call.

WHAT IT DOES NOT PROMISE. A hostname that resolves to a private address is
caught here by RESOLVING it, which the literal-only check could not do. That
still leaves DNS rebinding: a name that answers publicly now and privately a
moment later, when the fetch actually happens. Closing that needs the
connection itself pinned to the address that was checked, which is a
socket-level change. Named, not pretended away.
"""
import ipaddress
import socket
from urllib.parse import urlparse

#: Schemes worth fetching. Everything else -- file://, ftp://, gopher://,
#: data: -- either reads the disk or exists mainly as an SSRF vector.
ALLOWED_SCHEMES = ('http', 'https')


class UnsafeURL(ValueError):
    """Raised instead of fetching. The message is safe to show a user."""


def _addresses_for(host: str):
    """
    Every address this name resolves to, as ip_address objects.

    ALL of them, not the first: a name with one public and one private
    answer is the interesting case, and checking only the first would pass
    it.
    """
    try:
        infos = socket.getaddrinfo(host, None)
    except (socket.gaierror, UnicodeError, ValueError):
        return []
    found = []
    for info in infos:
        literal = info[4][0]
        try:
            found.append(ipaddress.ip_address(literal))
        except ValueError:
            continue
    return found


def _is_internal(address) -> bool:
    return (
        address.is_private or address.is_loopback or address.is_link_local
        or address.is_reserved or address.is_multicast or address.is_unspecified
    )


def check_url(url: str, allow_private: bool = False) -> str:
    """
    Returns the URL if it is safe to fetch, or raises UnsafeURL.

    `allow_private` is for a deployment whose media genuinely lives on an
    internal host -- an intranet app, a LAN media server. Off by default,
    because the safe posture has to be the one you get without reading this
    docstring.
    """
    text = (url or '').strip()
    if not text:
        raise UnsafeURL('No address was given.')

    try:
        parsed = urlparse(text)
    except Exception:
        raise UnsafeURL('That does not look like a web address.')

    scheme = (parsed.scheme or '').lower()
    if scheme not in ALLOWED_SCHEMES:
        raise UnsafeURL(
            f'Only http and https addresses can be fetched, not {scheme or "that"}.')

    host = (parsed.hostname or '').lower()
    if not host:
        raise UnsafeURL('That address has no host in it.')

    if allow_private:
        return text

    if host in ('localhost', 'localhost.localdomain') or host.endswith('.localhost'):
        raise UnsafeURL('That address points back at this server.')

    # A literal address needs no lookup, and must not get one: resolving an
    # IP is pointless and would let a slow DNS server stall the request.
    try:
        literal = ipaddress.ip_address(host.strip('[]'))
    except ValueError:
        literal = None

    if literal is not None:
        if _is_internal(literal):
            raise UnsafeURL('That address is inside a private network.')
        return text

    resolved = _addresses_for(host)
    if not resolved:
        # Unresolvable is not the same as unsafe. Let the fetch fail on its
        # own and report a real network error, rather than accusing the
        # person of something.
        return text
    if any(_is_internal(address) for address in resolved):
        raise UnsafeURL('That address resolves to a private network.')
    return text


def is_safe(url: str, allow_private: bool = False) -> bool:
    """Boolean form, for a caller that filters rather than validates."""
    try:
        check_url(url, allow_private=allow_private)
        return True
    except UnsafeURL:
        return False
