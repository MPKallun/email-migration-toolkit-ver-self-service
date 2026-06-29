#!/usr/bin/env python3
"""
EXPERIMENTAL — CalDAV / VTODO probe + backup for GoDaddy secureserver.net
=========================================================================
GoDaddy's secureserver.net runs a NON-STANDARD CalDAV server (no CardDAV at all,
calendar only, known to break non-Apple clients). This is a *diagnostic first,
backup second*: it talks raw CalDAV with the stdlib, logs every request/status,
lists whatever calendars/task-lists it can find, downloads the .ics objects, and
sorts them into Calendar/ (VEVENT) and Tasks/ (VTODO) by looking inside each file.

Run it, read the output, and we'll learn what GoDaddy actually allows before we
wire any of this into the main backup tool.

AUTH: your email address + password (or app password if 2-step is on), Basic auth.

USAGE
  # easiest + most reliable on GoDaddy — copy your CalDAV URL from webmail:
  #   Webmail → Calendar → (menu next to the calendar) → Properties → copy CalDAV URL
  IMAP_PASSWORD='pw' python3 caldav_backup.py --user me@equgruppo.com \
      --dest "/Volumes/Backup" --url "https://caldav.secureserver.net:8443/principals/users/me@equgruppo.com/"

  # or let it try to discover (may fail on GoDaddy's non-standard server):
  IMAP_PASSWORD='pw' python3 caldav_backup.py --user me@equgruppo.com --dest "/Volumes/Backup"

Nothing here writes to the server — PROPFIND/REPORT/GET are read-only.
"""
import argparse, base64, os, re, ssl, sys, getpass
import urllib.request, urllib.error
from urllib.parse import urljoin, urlparse
import xml.etree.ElementTree as ET
from utils import translate_error

NS = {"d": "DAV:", "c": "urn:ietf:params:xml:ns:caldav", "a": "urn:ietf:params:xml:ns:carddav"}
VERBOSE = True
SSL_CTX = None   # set to an unverified context by --insecure
UA = "macOS/14.4 (23E214) CalendarAgent/954.4"   # look like Apple's client (UA filtering)

_SINK = None
def set_log(fn):
    global _SINK
    _SINK = fn

def log(s=""):
    if _SINK:
        _SINK(s)
    else:
        print(s, flush=True)

def vlog(s):
    if VERBOSE:
        log(s)

# ---- raw WebDAV request with Basic auth + manual redirect handling ----------
def dav(method, url, user, pw, body=None, depth=None, timeout=30, _hops=0):
    data = body.encode("utf-8") if body else None
    req = urllib.request.Request(url, data=data, method=method)
    tok = base64.b64encode(("%s:%s" % (user, pw)).encode()).decode()
    req.add_header("Authorization", "Basic " + tok)
    req.add_header("User-Agent", UA)
    if depth is not None:
        req.add_header("Depth", str(depth))
    if body:
        req.add_header("Content-Type", 'application/xml; charset="utf-8"')
    ctx = SSL_CTX if SSL_CTX is not None else ssl.create_default_context()
    try:
        with urllib.request.urlopen(req, context=ctx, timeout=timeout) as r:
            return r.status, dict(r.headers), r.read().decode("utf-8", "replace"), r.geturl()
    except urllib.error.HTTPError as e:
        # follow redirects manually (urllib won't redirect non-GET reliably)
        if e.code in (301, 302, 307, 308) and _hops < 5:
            loc = e.headers.get("Location")
            if loc:
                return dav(method, urljoin(url, loc), user, pw, body, depth, timeout, _hops + 1)
        return e.code, dict(e.headers or {}), (e.read().decode("utf-8", "replace") if e.fp else ""), url
    except Exception as e:
        return -1, {}, "ERROR: %s" % translate_error(e), url

# ---- XML helpers ------------------------------------------------------------
def parse_multistatus(xml_text):
    """Yield (href, response_element) for each <response> in a 207 multistatus."""
    try:
        root = ET.fromstring(xml_text)
    except Exception as e:
        vlog("    [xml parse error] %s" % e)
        return
    for resp in root.findall("d:response", NS):
        href_el = resp.find("d:href", NS)
        href = href_el.text.strip() if href_el is not None and href_el.text else ""
        yield href, resp

def first_href(xml_text, tag):
    """Return the first href under the given prop tag (e.g. current-user-principal)."""
    try:
        root = ET.fromstring(xml_text)
    except Exception:
        return None
    el = root.find(".//%s/d:href" % tag, NS)
    return el.text.strip() if el is not None and el.text else None

# ---- PROPFIND bodies --------------------------------------------------------
BODY_PRINCIPAL = ('<d:propfind xmlns:d="DAV:"><d:prop>'
                  '<d:current-user-principal/></d:prop></d:propfind>')
BODY_HOME = ('<d:propfind xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav">'
             '<d:prop><c:calendar-home-set/></d:prop></d:propfind>')
BODY_LIST = ('<d:propfind xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav">'
             '<d:prop><d:resourcetype/><d:displayname/>'
             '<c:supported-calendar-component-set/></d:prop></d:propfind>')
BODY_RESOURCES = ('<d:propfind xmlns:d="DAV:"><d:prop>'
                  '<d:getcontenttype/><d:getetag/></d:prop></d:propfind>')
BODY_ADDR_HOME = ('<d:propfind xmlns:d="DAV:" xmlns:a="urn:ietf:params:xml:ns:carddav">'
                  '<d:prop><a:addressbook-home-set/></d:prop></d:propfind>')

# ---- discovery --------------------------------------------------------------
def discover_calendar_home(user, pw, explicit_url=None):
    """Return a list of base URLs to enumerate for calendars."""
    if explicit_url:
        # explicit_url may be the server root, a /.well-known/caldav URL, or a principal.
        # Full RFC 6764 chain: current-user-principal -> calendar-home-set. The server
        # returns THIS user\'s principal (their numeric id) automatically -- no id needed.
        log("Discovering from: %s" % explicit_url)
        st, _, txt, _ = dav("PROPFIND", explicit_url, user, pw, BODY_PRINCIPAL, depth=0)
        log("  current-user-principal -> HTTP %s" % st)
        principal = first_href(txt, "d:current-user-principal") if st == 207 else None
        principal = urljoin(explicit_url, principal) if principal else explicit_url
        log("  principal -> %s" % principal)
        st, _, txt, _ = dav("PROPFIND", principal, user, pw, BODY_HOME, depth=0)
        log("  calendar-home-set -> HTTP %s" % st)
        home = first_href(txt, "c:calendar-home-set") if st == 207 else None
        if home:
            home = urljoin(principal, home)
            log("  calendar-home -> %s" % home)
            return [home]
        log("  (no calendar-home-set; enumerating the principal directly)")
        return [principal]

    host = "caldav.secureserver.net"
    candidates = [
        "https://%s:8443/principals/users/%s/" % (host, user),
        "https://%s:8443/" % host,
        "https://%s/" % host,
        "https://%s/.well-known/caldav" % host,
        "https://%s/dav/%s/" % (host, user),
    ]
    homes = []
    for base in candidates:
        log("\n# PROPFIND current-user-principal  ->  %s" % base)
        st, hdrs, txt, final = dav("PROPFIND", base, user, pw, BODY_PRINCIPAL, depth=0)
        log("  HTTP %s" % st)
        vlog("  " + txt[:400].replace("\n", "\n  "))
        if st == 207:
            principal = first_href(txt, "d:current-user-principal") or final
            principal = urljoin(base, principal)
            log("  principal -> %s" % principal)
            st2, _, txt2, _ = dav("PROPFIND", principal, user, pw, BODY_HOME, depth=0)
            log("  PROPFIND calendar-home-set -> HTTP %s" % st2)
            vlog("  " + txt2[:400].replace("\n", "\n  "))
            home = first_href(txt2, "c:calendar-home-set")
            if home:
                home = urljoin(principal, home)
                log("  calendar-home -> %s" % home)
                homes.append(home)
                break
            else:
                homes.append(principal)   # try enumerating the principal itself
                break
    if not homes:
        log("\n! Discovery found no principal. If you have your CalDAV URL from webmail, "
            "re-run with  --url \"<that url>\".")
    return homes

# ---- enumerate + download ---------------------------------------------------
def backup_caldav(user, pw, dest, explicit_url=None, progress=None):
    box = os.path.join(dest, user)
    cal_dir = os.path.join(box, "Calendar")
    task_dir = os.path.join(box, "Tasks")

    homes = discover_calendar_home(user, pw, explicit_url)
    if not homes:
        return 2

    saved = {"VEVENT": 0, "VTODO": 0, "other": 0, "fail": 0}
    collections = []

    for home in homes:
        log("\n# PROPFIND Depth:1 (list collections)  ->  %s" % home)
        st, _, txt, _ = dav("PROPFIND", home, user, pw, BODY_LIST, depth=1)
        log("  HTTP %s" % st)
        if st != 207:
            vlog("  " + txt[:600].replace("\n", "\n  "))
            # maybe `home` is itself a calendar collection — try it directly
            collections.append(home)
            continue
        for href, resp in parse_multistatus(txt):
            rt = resp.find(".//d:resourcetype", NS)
            is_cal = rt is not None and rt.find("c:calendar", NS) is not None
            name_el = resp.find(".//d:displayname", NS)
            name = (name_el.text if name_el is not None and name_el.text else "").strip()
            comps = [c.get("name") for c in resp.findall(".//c:comp", NS)]
            if is_cal:
                full = urljoin(home, href)
                collections.append(full)
                log("  calendar collection: %-30s comps=%s" % (name or href, comps or "?"))
    if not collections:
        log("\n! No calendar collections found to enumerate.")
        return 2

    # First, gather ALL objects to be fetched across all collections for progress tracking
    all_to_fetch = []
    for coll in collections:
        st, _, txt, _ = dav("PROPFIND", coll, user, pw, BODY_RESOURCES, depth=1)
        if st == 207:
            for href, resp in parse_multistatus(txt):
                ctype_el = resp.find(".//d:getcontenttype", NS)
                ctype = (ctype_el.text or "") if ctype_el is not None else ""
                if href.lower().endswith(".ics") or "calendar" in ctype.lower():
                    all_to_fetch.append((coll, href))

    total = len(all_to_fetch)
    log("  %d total object(s) to fetch" % total)

    for i, (coll, href) in enumerate(all_to_fetch, 1):
        url = urljoin(coll, href)
        st, _, body, _ = dav("GET", url, user, pw)
        if st != 200 or not body.strip():
            saved["fail"] += 1
            vlog("    GET %s -> HTTP %s" % (href, st))
        else:
            if "BEGIN:VTODO" in body:
                outdir, kind = task_dir, "VTODO"
            elif "BEGIN:VEVENT" in body:
                outdir, kind = cal_dir, "VEVENT"
            else:
                outdir, kind = cal_dir, "other"
            os.makedirs(outdir, exist_ok=True)
            fn = re.sub(r'[\\/:*?"<>|]+', "_", os.path.basename(href.rstrip("/"))) or ("%d.ics" % saved[kind])
            if not fn.lower().endswith(".ics"):
                fn += ".ics"
            with open(os.path.join(outdir, fn), "w", encoding="utf-8") as fh:
                fh.write(body)
            saved[kind] += 1
        
        if progress:
            progress(i, total)

    log("\n================ RESULT ================")
    log("  Calendar events (VEVENT): %d  -> %s" % (saved["VEVENT"], cal_dir))
    log("  Tasks         (VTODO):    %d  -> %s" % (saved["VTODO"], task_dir))
    if saved["other"]:
        log("  Uncategorised objects:    %d" % saved["other"])
    if saved["fail"]:
        log("  Failed fetches:           %d" % saved["fail"])
    if saved["VEVENT"] == 0 and saved["VTODO"] == 0:
        log("\n  Nothing pulled - discovery reached the server but found no objects.")
    return saved

def _hdr(h, k):
    for kk in h:
        if kk.lower() == k.lower():
            return h[kk]
    return "-"

def diagnose(user, pw, url):
    """Loud, read-only inspection of one endpoint: what is it, and how do we talk to it?"""
    pr = urlparse(url)
    host = "%s://%s" % (pr.scheme, pr.netloc)

    log("\n=== OPTIONS  %s ===" % url)
    st, h, b, _ = dav("OPTIONS", url, user, pw)
    log("  HTTP %s" % st)
    log("  DAV:   %s" % _hdr(h, "DAV"))          # 'calendar-access' here = real CalDAV
    log("  Allow: %s" % _hdr(h, "Allow"))

    log("\n=== GET  %s ===" % url)
    st, h, b, _ = dav("GET", url, user, pw)
    log("  HTTP %s   Content-Type: %s" % (st, _hdr(h, "Content-Type")))
    if "BEGIN:VCALENDAR" in b:
        nev, ntodo = b.count("BEGIN:VEVENT"), b.count("BEGIN:VTODO")
        log("  >> THIS IS AN ICS FEED (%d bytes): %d VEVENT, %d VTODO." % (len(b), nev, ntodo))
        log("  >> A plain GET backs up this whole calendar in one file. Easiest path.")
    elif st == 200:
        log("  first 200 chars: %s" % b[:200].replace("\n", " "))

    log("\n=== PROPFIND (current-user-principal)  %s ===" % url)
    st, h, b, _ = dav("PROPFIND", url, user, pw, BODY_PRINCIPAL, depth=0)
    log("  HTTP %s" % st)
    if VERBOSE and b:
        log("  " + b[:400].replace("\n", "\n  "))

    log("\n=== well-known discovery on  %s ===" % host)
    for path in ("/.well-known/caldav", "/.well-known/carddav", "/"):
        u = host + path
        st, h, b, _ = dav("PROPFIND", u, user, pw, BODY_PRINCIPAL, depth=0)
        extra = ""
        if st == 207:
            extra = "  principal=" + str(first_href(b, "d:current-user-principal"))
        log("  PROPFIND %-42s HTTP %s%s" % (u, st, extra))

    log("\n(Read the DAV: line and the GET result above — that tells us what this endpoint really is.)")


def backup_carddav(user, pw, dest, host, progress=None):
    """Best-effort CardDAV contacts pull -> <dest>/<user>/Address book/*.vcf."""
    box = os.path.join(dest, user)
    addr_dir = os.path.join(box, "Address book")
    wk = host + "/.well-known/carddav"
    log("\n# CardDAV discovery  ->  %s" % wk)
    st, _, txt, _ = dav("PROPFIND", wk, user, pw, BODY_PRINCIPAL, depth=0)
    log("  current-user-principal -> HTTP %s" % st)
    principal = first_href(txt, "d:current-user-principal") if st == 207 else None
    if not principal:
        log("  no CardDAV principal — skipping contacts.")
        return 0
    principal = urljoin(wk, principal)
    st, _, txt, _ = dav("PROPFIND", principal, user, pw, BODY_ADDR_HOME, depth=0)
    home = first_href(txt, "a:addressbook-home-set") if st == 207 else None
    log("  addressbook-home-set -> HTTP %s" % st)
    if not home:
        log("  no addressbook-home — skipping contacts.")
        return 0
    home = urljoin(principal, home)
    st, _, txt, _ = dav("PROPFIND", home, user, pw, BODY_LIST, depth=1)
    collections = []
    if st == 207:
        for href, resp in parse_multistatus(txt):
            rt = resp.find(".//d:resourcetype", NS)
            if rt is not None and rt.find("a:addressbook", NS) is not None:
                collections.append(urljoin(home, href))
    
    # Gather all contacts to be fetched for progress tracking
    all_to_fetch = []
    for coll in collections:
        st, _, txt, _ = dav("PROPFIND", coll, user, pw, BODY_RESOURCES, depth=1)
        if st == 207:
            coll_path = urlparse(coll).path.rstrip("/")
            for href, resp in parse_multistatus(txt):
                if href.endswith("/") or urlparse(urljoin(coll, href)).path.rstrip("/") == coll_path:
                    continue
                all_to_fetch.append((coll, href))
    
    total = len(all_to_fetch)
    log("  %d contact(s) to fetch" % total)
    saved = 0
    for i, (coll, href) in enumerate(all_to_fetch, 1):
        u = urljoin(coll, href)
        st2, _, body, _ = dav("GET", u, user, pw)
        if st2 == 200 and "BEGIN:VCARD" in body:
            os.makedirs(addr_dir, exist_ok=True)
            fn = re.sub(r'[\\/:*?"<>|]+', "_", os.path.basename(href.rstrip("/"))) or ("%d.vcf" % saved)
            if not fn.lower().endswith(".vcf"):
                fn += ".vcf"
            with open(os.path.join(addr_dir, fn), "w", encoding="utf-8") as fh:
                fh.write(body)
            saved += 1
        
        if progress:
            progress(i, total)

    log("  Contacts (vCard): %d  -> %s" % (saved, addr_dir))
    return saved


def backup_dav(user, pw, dest, caldav_host="am1.myprofessionalmail.com",
               do_contacts=True, insecure=False, log_fn=None, progress=None):
    """One call for the GUI: CalDAV (calendar + tasks) + CardDAV (contacts) via
    RFC 6764 well-known discovery. The server returns the authenticated user
    principal automatically -- only email + password needed. Returns a summary."""
    global SSL_CTX
    if log_fn:
        set_log(log_fn)
    if insecure:
        SSL_CTX = ssl._create_unverified_context()
    base = "https://%s/.well-known/caldav" % caldav_host
    
    # We can't easily combine progress across both calls into one 0-100% bar 
    # without pre-counting everything, so we just let them each use the bar.
    # The bar will reset for contacts.
    cal = backup_caldav(user, pw, dest, base, progress=progress)
    contacts = 0
    if do_contacts:
        contacts = backup_carddav(user, pw, dest, "https://%s" % caldav_host, progress=progress)
    return {"events": cal.get("VEVENT", 0) if isinstance(cal, dict) else 0, 
            "tasks": cal.get("VTODO", 0) if isinstance(cal, dict) else 0,
            "contacts": contacts, "fail": cal.get("fail", 0) if isinstance(cal, dict) else 0}


def main():
    global VERBOSE
    ap = argparse.ArgumentParser(description="Experimental GoDaddy CalDAV/VTODO probe + backup")
    ap.add_argument("--user", required=True, help="email address (CalDAV username)")
    ap.add_argument("--dest", required=True, help="backup root; saved to <dest>/<user>/Calendar|Tasks")
    ap.add_argument("--url", help="exact CalDAV URL from webmail (skips discovery — most reliable)")
    ap.add_argument("--password", help="prefer $IMAP_PASSWORD instead")
    ap.add_argument("--quiet", action="store_true", help="less HTTP detail")
    ap.add_argument("--insecure", action="store_true",
                    help="skip TLS certificate verification (testing only — fixes the macOS \"local issuer\" error)")
    ap.add_argument("--diagnose", action="store_true",
                    help="inspect the endpoint (OPTIONS/GET/PROPFIND/well-known) instead of backing up")
    ap.add_argument("--no-contacts", action="store_true", help="skip the CardDAV contacts pass")
    args = ap.parse_args()
    VERBOSE = not args.quiet
    if args.insecure:
        global SSL_CTX
        SSL_CTX = ssl._create_unverified_context()
        log("[!] TLS certificate verification DISABLED (--insecure) — diagnostic use only.")

    pw = os.environ.get("IMAP_PASSWORD") or args.password
    if not pw:
        pw = getpass.getpass("Password for %s: " % args.user) if sys.stdin.isatty() else None
    if not pw:
        sys.exit("No password: set $IMAP_PASSWORD or pass --password.")

    log("EAHI CalDAV probe — read-only. Target user: %s" % args.user)
    if args.diagnose:
        if not args.url:
            sys.exit("--diagnose needs --url (the CalDAV/calendar URL from webmail).")
        diagnose(args.user, pw, args.url)
        sys.exit(0)
    saved = backup_caldav(args.user, pw, args.dest, args.url)
    if not args.no_contacts and args.url:
        pr = urlparse(args.url)
        host = "%s://%s" % (pr.scheme, pr.netloc)
        backup_carddav(args.user, pw, args.dest, host)
    ok = (saved.get("VEVENT") or saved.get("VTODO")) and not saved.get("fail")
    sys.exit(0 if ok else 1)

if __name__ == "__main__":
    main()
