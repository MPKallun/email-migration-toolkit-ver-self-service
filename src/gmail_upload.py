#!/usr/bin/env python3
"""
gmail_upload.py — Restore / upload-to-Gmail using Google OAuth2 and Gmail API
============================================================================
This module implements a pure Python standard library (no pip packages needed)
OAuth2 login flow and Gmail API upload.

HOW IT WORKS:
  1. Starts a temporary HTTP server on a local port.
  2. Opens the default browser to Google's official Sign-In consent screen.
  3. Google redirects the user back to the local server with an Authorization Code.
  4. Exchanges the code for an Access Token and Refresh Token.
  5. Uses the Access Token to upload emails directly via the Gmail REST API
     (which bypasses the restrictive 500 MB/day IMAP upload limit).

DEVELOPER INSTRUCTIONS:
  You must create an OAuth Client ID in your Google Cloud Platform (GCP) Console
  under "APIs & Services" -> "Credentials" (Desktop Application type).
  Put your Client ID and Client Secret in the placeholders below.
"""
import os
import sys
import json
import socket
import ssl
import time
import urllib.request
import urllib.parse
import webbrowser
import base64
from http.server import BaseHTTPRequestHandler, HTTPServer
from utils import translate_error, retry, log_debug, load_dotenv

def execute_urllib_request(req, log_fn, max_attempts=5, initial_delay=1.5, backoff=2.0):
    import urllib.error
    delay = initial_delay
    for attempt in range(1, max_attempts + 1):
        try:
            with urllib.request.urlopen(req) as response:
                return response.read()
        except urllib.error.HTTPError as e:
            status = e.code
            clean_err = translate_error(e)
            
            # Retry on 429 (Rate Limit) and 5xx (Server Errors)
            if status == 429 or status >= 500:
                if attempt == max_attempts:
                    log_fn(f"❌ HTTP request failed after {max_attempts} attempts: {clean_err}")
                    raise e
                log_fn(f"⚠ HTTP warning ({status}) in thread: {clean_err}. Retrying in {delay:.1f}s...")
                time.sleep(delay)
                delay *= backoff
            else:
                raise e
        except (socket.timeout, socket.error, ssl.SSLError) as e:
            clean_err = translate_error(e)
            if attempt == max_attempts:
                log_fn(f"❌ Connection failed after {max_attempts} attempts: {clean_err}")
                raise e
            log_fn(f"⚠ Connection failed: {clean_err}. Retrying in {delay:.1f}s...")
            time.sleep(delay)
            delay *= backoff

# Globally wrap urllib.request.urlopen to support dynamic SSL bypass
_orig_urlopen = urllib.request.urlopen
INSECURE_MODE = False

def _safe_urlopen(url, *args, **kwargs):
    if INSECURE_MODE:
        import ssl
        kwargs["context"] = ssl._create_unverified_context()
    return _orig_urlopen(url, *args, **kwargs)

urllib.request.urlopen = _safe_urlopen

# ============================================================================
# DEVELOPER CREDENTIALS
# Loaded from environment variables (see .env.example at the project root).
# Copy .env.example -> .env and fill in your GCP OAuth "Desktop app" Client ID
# / Secret. Once set, end-users never have to touch GCP -- they just click
# "Login with Google". Never commit the real .env (already in .gitignore).
# ============================================================================
load_dotenv()
CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID", "")
CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET", "")

OAUTH_SCOPE = "https://www.googleapis.com/auth/gmail.modify https://www.googleapis.com/auth/userinfo.email https://www.googleapis.com/auth/contacts https://www.googleapis.com/auth/calendar https://www.googleapis.com/auth/tasks"


# ---- Local Callback Server to capture Google's redirect code ----------------
class OAuthCallbackHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        # Prevent favicon noise
        if self.path.startswith("/favicon.ico"):
            self.send_response(404)
            self.end_headers()
            return

        parsed = urllib.parse.urlparse(self.path)
        params = urllib.parse.parse_qs(parsed.query)

        if "code" in params:
            self.server.auth_code = params["code"][0]
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(self.get_html_response(True))
        else:
            error_msg = params.get("error", ["No authorization code received"])[0]
            self.send_response(400)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(self.get_html_response(False, error_msg))

    def get_html_response(self, success, error_msg=None):
        if success:
            title = "Login Successful!"
            title_color = "#1A6B8A"
            desc = "You have successfully authenticated with Google."
            sub = "You can now close this tab and return to the Data Backup app."
        else:
            title = "Login Failed"
            title_color = "#B22222"
            desc = f"Google Authentication failed: {error_msg}"
            sub = "Please return to the Data Backup app and try signing in again."

        html = f"""
        <html>
        <head>
            <title>{title}</title>
        </head>
        <body style="font-family: Helvetica, Arial, sans-serif; text-align: center; padding-top: 100px; background-color: #F2F5FA;">
            <div style="background-color: white; display: inline-block; padding: 40px; border-radius: 12px; box-shadow: 0 4px 12px rgba(27,43,75,0.1); border: 1px solid #D5DCE6; max-width: 500px;">
                <h2 style="color: {title_color}; margin-top: 0; margin-bottom: 10px;">{title}</h2>
                <p style="color: #1B2B4B; font-weight: bold; margin-bottom: 8px;">{desc}</p>
                <p style="color: #5F6B7A; font-size: 13px; margin-bottom: 25px;">{sub}</p>
                
                <button onclick="attemptClose()" 
                        style="background-color: {title_color}; color: white; border: none; padding: 12px 24px; border-radius: 8px; font-weight: bold; cursor: pointer; font-size: 14px; box-shadow: 0 2px 4px rgba(0,0,0,0.1); transition: background-color 0.2s;">
                    Close Tab
                </button>
                
                <p id="timer-text" style="color: #8A9BB0; font-size: 11px; margin-top: 20px;">(This tab will try to close automatically in 3 seconds...)</p>
            </div>
            <script>
                function attemptClose() {{
                    window.open('', '_self', '');
                    window.close();
                    setTimeout(function() {{
                        document.getElementById('timer-text').innerHTML = "Auto-close blocked by browser security.<br>Please close this tab manually.";
                        document.getElementById('timer-text').style.color = "#B22222";
                        document.getElementById('timer-text').style.fontWeight = "bold";
                    }}, 250);
                }}

                var seconds = 3;
                var interval = setInterval(function() {{
                    seconds--;
                    if (seconds <= 0) {{
                        clearInterval(interval);
                        document.getElementById('timer-text').innerText = "(Closing tab...)";
                        attemptClose();
                    }} else {{
                        document.getElementById('timer-text').innerText = "(This tab will try to close automatically in " + seconds + " seconds...)";
                    }}
                }}, 1000);
            </script>
        </body>
        </html>
        """
        return html.encode("utf-8")

    def log_message(self, format, *args):
        pass  # Suppress standard logging output to keep the console clean


# ---- Start Local Server and execute OAuth browser redirect ------------------
def authenticate_google(log_fn=print, insecure=False):
    """Starts local server, opens browser, and returns (access_token, refresh_token, email)."""
    if not CLIENT_ID or not CLIENT_SECRET:
        log_fn("⚠  Google Client ID / Client Secret are not set.")
        log_fn("   Copy .env.example to .env at the project root and fill them in.")
        return None

    global INSECURE_MODE
    INSECURE_MODE = insecure
    if insecure:
        log_fn("🛡  Insecure mode enabled: Bypassing SSL certificate verification.")
        # Globally override default context for urllib.request
        ssl._create_default_https_context = ssl._create_unverified_context

    # Find a free port dynamically
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(('localhost', 0))
    port = s.getsockname()[1]
    s.close()

    redirect_uri = f"http://localhost:{port}"
    server = HTTPServer(('localhost', port), OAuthCallbackHandler)
    server.auth_code = None

    # Construct the authorization URL
    auth_params = {
        "client_id": CLIENT_ID,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": OAUTH_SCOPE,
        "access_type": "offline",
        "prompt": "consent"
    }
    auth_url = "https://accounts.google.com/o/oauth2/v2/auth?" + urllib.parse.urlencode(auth_params)

    log_fn("\nOpening your web browser for Google Secure Login...")
    webbrowser.open(auth_url)

    # Handle a single request then stop
    server.handle_request()
    
    if not server.auth_code:
        log_fn("❌ Authentication was cancelled or failed.")
        return None

    log_fn("Capturing authorization code. Exchanging for API tokens...")

    # Exchange authorization code for Access & Refresh tokens
    token_url = "https://oauth2.googleapis.com/token"
    token_data = {
        "code": server.auth_code,
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "redirect_uri": redirect_uri,
        "grant_type": "authorization_code"
    }
    
    req_body = urllib.parse.urlencode(token_data).encode("utf-8")
    req = urllib.request.Request(token_url, data=req_body, method="POST")
    req.add_header("Content-Type", "application/x-www-form-urlencoded")

    try:
        with urllib.request.urlopen(req) as response:
            res_data = json.loads(response.read().decode("utf-8"))
            access_token = res_data.get("access_token")
            refresh_token = res_data.get("refresh_token")
    except Exception as e:
        log_fn(f"❌ Failed to exchange authorization code: {e}")
        return None

    # Fetch user's email address using the token
    email = None
    try:
        userinfo_url = "https://www.googleapis.com/oauth2/v2/userinfo"
        req = urllib.request.Request(userinfo_url)
        req.add_header("Authorization", f"Bearer {access_token}")
        with urllib.request.urlopen(req) as response:
            user_data = json.loads(response.read().decode("utf-8"))
            email = user_data.get("email")
    except Exception as e:
        log_fn(f"⚠ Could not fetch your Google email: {e}")

    log_fn(f"✓ Authenticated successfully as: {email}")
    return access_token, refresh_token, email


# ---- Gmail API Helpers ------------------------------------------------------
def _refresh_token(refresh_token, log_fn):
    """Uses the refresh token to get a new access token."""
    token_url = "https://oauth2.googleapis.com/token"
    token_data = {
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "refresh_token": refresh_token,
        "grant_type": "refresh_token"
    }
    try:
        req_body = urllib.parse.urlencode(token_data).encode("utf-8")
        req = urllib.request.Request(token_url, data=req_body, method="POST")
        req.add_header("Content-Type", "application/x-www-form-urlencoded")
        res_bytes = execute_urllib_request(req, log_fn, max_attempts=3)
        res_data = json.loads(res_bytes.decode("utf-8"))
        return res_data.get("access_token")
    except Exception as e:
        log_fn(f"❌ Error refreshing access token: {translate_error(e)}")
        return None

def _upload_email(access_token, email_path, label_ids, log_fn):
    """Uploads a single EML file to Gmail using the API."""
    try:
        with open(email_path, "rb") as f:
            raw_content = f.read()
        
        # Gmail API expects base64url encoded RFC822 content
        encoded_raw = base64.urlsafe_b64encode(raw_content).decode("utf-8")
        
        url = "https://gmail.googleapis.com/gmail/v1/users/me/messages/import?neverMarkSpam=true&internalDateSource=dateHeader"
        
        body = {"raw": encoded_raw}
        if label_ids:
            body["labelIds"] = label_ids
        payload = json.dumps(body).encode("utf-8")
        
        req = urllib.request.Request(url, data=payload, method="POST")
        req.add_header("Authorization", f"Bearer {access_token}")
        req.add_header("Content-Type", "application/json")
        
        execute_urllib_request(req, log_fn)
        return True
    except Exception as e:
        log_fn(f"⚠ Failed to upload {os.path.basename(email_path)}: {translate_error(e)}")
        return False


FOLDER_MAP = {
    "inbox":  ["INBOX"],
    "sent":   ["SENT"],
    "drafts": ["Migrated/Drafts"],
    "junk":   ["Migrated/Junk"],
    "spam":   ["Migrated/Spam"],
}

def folder_of(path, src):
    """Determine the mail folder name from its path relative to the backup source."""
    eml_root = os.path.join(src, "E-Mails")
    if not os.path.exists(eml_root):
        eml_root = src
    rel = os.path.relpath(path, eml_root)
    parts = rel.split(os.sep)
    return parts[0] if len(parts) > 1 else "INBOX"

def labels_for(folder):
    """Map a local folder name to standard system labels or custom labels."""
    return FOLDER_MAP.get(folder.lower(), ["Migrated/" + folder])

def ensure_label(access_token, label_name, label_cache, log_fn):
    """Check if label_name exists, create it if not, and return its ID.
    System labels (like INBOX, SENT) are returned directly without API calls."""
    if label_name in ("INBOX", "SENT", "DRAFT", "SPAM", "TRASH", "UNREAD", "STARRED", "IMPORTANT"):
        return label_name
        
    if not label_cache:
        # Fetch all existing labels
        try:
            url = "https://gmail.googleapis.com/gmail/v1/users/me/labels"
            req = urllib.request.Request(url)
            req.add_header("Authorization", f"Bearer {access_token}")
            res_bytes = execute_urllib_request(req, log_fn, max_attempts=3)
            res_data = json.loads(res_bytes.decode("utf-8"))
            for l in res_data.get("labels", []):
                label_cache[l["name"]] = l["id"]
        except Exception as e:
            log_fn(f"⚠ Failed to list labels: {translate_error(e)}")
            
    if label_name in label_cache:
        return label_cache[label_name]
        
    # Create the custom label
    try:
        url = "https://gmail.googleapis.com/gmail/v1/users/me/labels"
        body = json.dumps({
            "name": label_name,
            "labelListVisibility": "labelShow",
            "messageListVisibility": "show"
        }).encode("utf-8")
        req = urllib.request.Request(url, data=body, method="POST")
        req.add_header("Authorization", f"Bearer {access_token}")
        req.add_header("Content-Type", "application/json")
        res_bytes = execute_urllib_request(req, log_fn, max_attempts=3)
        res_data = json.loads(res_bytes.decode("utf-8"))
        label_cache[label_name] = res_data["id"]
        return res_data["id"]
    except Exception as e:
        log_fn(f"⚠ Failed to create label '{label_name}': {translate_error(e)}")
        return label_name


# ---- Summary/discovery of backup -------------------------------------------
def discover_backup(src):
    """Summarise a backup folder so the GUI can show what would be uploaded."""
    counts = {"emails": 0, "contacts": 0, "calendar": 0, "tasks": 0}
    for root, _dirs, files in os.walk(src):
        low = root.lower()
        for f in files:
            fl = f.lower()
            if fl.endswith(".eml"):
                counts["emails"] += 1
            elif fl.endswith(".vcf"):
                counts["contacts"] += 1
            elif fl.endswith(".ics"):
                counts["tasks" if "task" in low else "calendar"] += 1
    return counts


# ---------------------------------------------------------------- tiny vCard/iCal reader (stdlib)
def _unfold(text):
    """RFC-style line unfolding: a line starting with space/tab continues the previous one."""
    out = []
    for raw in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if raw[:1] in (" ", "\t") and out:
            out[-1] += raw[1:]
        else:
            out.append(raw)
    return out

def _unescape(v):
    return v.replace("\\n", "\n").replace("\\N", "\n").replace("\\,", ",").replace("\\;", ";").replace("\\\\", "\\")

def _split_line(line):
    """'EMAIL;TYPE=work:a@b.com' -> ('EMAIL', {'TYPE':'work'}, 'a@b.com')"""
    if ":" not in line: return None
    head, value = line.split(":", 1)
    parts = head.split(";")
    name = parts[0].upper()
    params = {}
    for p in parts[1:]:
        if "=" in p:
            k, val = p.split("=", 1); params[k.upper()] = val
    return name, params, value

def _blocks(text, begin, end):
    cur = None
    for line in _unfold(text):
        u = line.strip().upper()
        if u == f"BEGIN:{begin}":
            cur = []
        elif u == f"END:{end}":
            if cur is not None: yield cur
            cur = None
        elif cur is not None:
            cur.append(line)

def _ical_when(params, value):
    value = value.strip()
    if params.get("VALUE") == "DATE" or (len(value) == 8 and value.isdigit()):
        return {"date": f"{value[0:4]}-{value[4:6]}-{value[6:8]}"}
    # datetime: YYYYMMDDTHHMMSS[Z]
    z = value.endswith("Z"); v = value.rstrip("Z")
    try:
        iso = f"{v[0:4]}-{v[4:6]}-{v[6:8]}T{v[9:11]}:{v[11:13]}:{v[13:15]}"
    except Exception:
        return {"dateTime": value}
    out = {"dateTime": iso + ("Z" if z else "")}
    if "TZID" in params: out["timeZone"] = params["TZID"]
    elif z: out["timeZone"] = "UTC"
    return out

def parse_vcards_list(files, log_fn):
    out = []
    for p in files:
        try:
            with open(p, encoding="utf-8", errors="replace") as fh:
                text = fh.read()
            for block in _blocks(text, "VCARD", "VCARD"):
                r = {"uid": None, "fn": "", "given": "", "family": "",
                     "emails": [], "tels": [], "org": "", "title": ""}
                for line in block:
                    parsed = _split_line(line)
                    if not parsed: continue
                    name, params, value = parsed
                    value = _unescape(value.strip())
                    if name == "UID":   r["uid"] = value
                    elif name == "FN":  r["fn"] = value
                    elif name == "N":
                        f = (value.split(";") + ["", ""])[:2]; r["family"], r["given"] = f[0], f[1]
                    elif name == "EMAIL" and value: r["emails"].append(value)
                    elif name == "TEL" and value:   r["tels"].append(value)
                    elif name == "ORG":   r["org"] = value.split(";")[0]
                    elif name == "TITLE": r["title"] = value
                r["uid"] = r["uid"] or (r["fn"] + "|" + (r["emails"][0] if r["emails"] else p))
                out.append((p, r))
        except Exception as e:
            log_fn(f"  ! could not parse contact {os.path.basename(p)}: {e}")
    return out

def parse_events_list(files, log_fn):
    out = []
    for p in files:
        try:
            with open(p, encoding="utf-8", errors="replace") as fh:
                text = fh.read()
            for block in _blocks(text, "VEVENT", "VEVENT"):
                ev = {"summary": "(no title)", "uid": None, "start": None, "end": None,
                      "location": "", "description": "", "rrule": None}
                for line in block:
                    parsed = _split_line(line)
                    if not parsed: continue
                    name, params, value = parsed
                    if name == "SUMMARY":       ev["summary"] = _unescape(value)
                    elif name == "UID":         ev["uid"] = value.strip()
                    elif name == "DTSTART":     ev["start"] = _ical_when(params, value)
                    elif name == "DTEND":       ev["end"] = _ical_when(params, value)
                    elif name == "LOCATION":    ev["location"] = _unescape(value)
                    elif name == "DESCRIPTION": ev["description"] = _unescape(value)
                    elif name == "RRULE":       ev["rrule"] = value.strip()
                if ev["start"] and not ev["end"]: ev["end"] = ev["start"]
                out.append((p, ev))
        except Exception as e:
            log_fn(f"  ! could not parse calendar event {os.path.basename(p)}: {e}")
    return out

def parse_tasks_list(files, log_fn):
    out = []
    for p in files:
        try:
            with open(p, encoding="utf-8", errors="replace") as fh:
                text = fh.read()
            for block in _blocks(text, "VTODO", "VTODO"):
                t = {"summary": "(no title)", "uid": None, "description": "",
                     "status": "needsAction", "due": None, "completed": None}
                for line in block:
                    parsed = _split_line(line)
                    if not parsed: continue
                    name, params, value = parsed
                    if name == "SUMMARY":       t["summary"] = _unescape(value)
                    elif name == "UID":         t["uid"] = value.strip()
                    elif name == "DESCRIPTION": t["description"] = _unescape(value)
                    elif name == "STATUS":
                        v = value.strip().upper()
                        if v == "COMPLETED":
                            t["status"] = "completed"
                        else:
                            t["status"] = "needsAction"
                    elif name == "DUE":
                        t["due"] = _ical_when(params, value)
                    elif name == "COMPLETED":
                        t["completed"] = _ical_when(params, value)
                out.append((p, t))
        except Exception as e:
            log_fn(f"  ! could not parse task {os.path.basename(p)}: {e}")
    return out

def contact_body(r):
    body = {}
    if r["given"] or r["family"] or r["fn"]:
        body["names"] = [{"givenName": r["given"], "familyName": r["family"],
                          "displayName": r["fn"] or f'{r["given"]} {r["family"]}'.strip()}]
    if r["emails"]: body["emailAddresses"] = [{"value": e} for e in r["emails"]]
    if r["tels"]:   body["phoneNumbers"]   = [{"value": t} for t in r["tels"]]
    if r["org"] or r["title"]: body["organizations"] = [{"name": r["org"], "title": r["title"]}]
    return body

def event_body(ev):
    body = {"summary": ev["summary"]}
    if ev["uid"]:   body["iCalUID"] = ev["uid"]
    if ev["start"]: body["start"] = ev["start"]
    if ev["end"]:   body["end"] = ev["end"]
    if ev["location"]:    body["location"] = ev["location"]
    if ev["description"]: body["description"] = ev["description"]
    if ev["rrule"]:       body["recurrence"] = ["RRULE:" + ev["rrule"]]
    return body

def format_task_date(when):
    if not when: return None
    if isinstance(when, dict):
        if "date" in when:
            return when["date"] + "T00:00:00.000Z"
        elif "dateTime" in when:
            val = when["dateTime"]
            if not val.endswith("Z") and "T" in val:
                return val + "Z"
            return val
    return str(when)

def task_body(t):
    body = {"title": t["summary"]}
    if t["description"]:
        body["notes"] = t["description"]
    body["status"] = t["status"]
    due_str = format_task_date(t["due"])
    if due_str:
        body["due"] = due_str
    comp_str = format_task_date(t["completed"])
    if comp_str:
        body["completed"] = comp_str
    return body

def _get_api_error(e):
    if hasattr(e, "read"):
        try:
            err_body = e.read().decode("utf-8", "replace")
            err_json = json.loads(err_body)
            return err_json.get("error", {}).get("message", err_body)
        except Exception:
            pass
    return str(e)

# ---- REST API uploaders ----------------------------------------------------
def _upload_contact(access_token, contact_body, log_fn):
    try:
        url = "https://people.googleapis.com/v1/people:createContact"
        payload = json.dumps(contact_body).encode("utf-8")
        req = urllib.request.Request(url, data=payload, method="POST")
        req.add_header("Authorization", f"Bearer {access_token}")
        req.add_header("Content-Type", "application/json")
        execute_urllib_request(req, log_fn)
        return True
    except Exception as e:
        log_fn(f"⚠ Failed to upload contact: {translate_error(e)}")
        return False

def _upload_event(access_token, event_body, log_fn):
    try:
        url = "https://www.googleapis.com/calendar/v3/calendars/primary/events/import"
        payload = json.dumps(event_body).encode("utf-8")
        req = urllib.request.Request(url, data=payload, method="POST")
        req.add_header("Authorization", f"Bearer {access_token}")
        req.add_header("Content-Type", "application/json")
        execute_urllib_request(req, log_fn)
        return True
    except Exception as e:
        log_fn(f"⚠ Failed to upload event '{event_body.get('summary')}': {translate_error(e)}")
        return False

def _upload_task(access_token, task_body, log_fn):
    try:
        url = "https://tasks.googleapis.com/tasks/v1/lists/@default/tasks"
        payload = json.dumps(task_body).encode("utf-8")
        req = urllib.request.Request(url, data=payload, method="POST")
        req.add_header("Authorization", f"Bearer {access_token}")
        req.add_header("Content-Type", "application/json")
        execute_urllib_request(req, log_fn)
        return True
    except Exception as e:
        log_fn(f"⚠ Failed to upload task '{task_body.get('title')}': {translate_error(e)}")
        return False

# ---- Ledger implementation ------------------------------------------------
def open_restore_ledger(src):
    import sqlite3
    ledger_path = os.path.join(src, "_restore_ledger.sqlite")
    db = sqlite3.connect(ledger_path)
    db.execute("""
        CREATE TABLE IF NOT EXISTS restored(
            kind TEXT,
            key TEXT,
            ts REAL,
            PRIMARY KEY(kind, key)
        )
    """)
    db.commit()
    return db

def is_restored(db, kind, key):
    res = db.execute("SELECT 1 FROM restored WHERE kind=? AND key=?", (kind, key)).fetchone()
    return res is not None

def mark_restored(db, kind, key):
    import time
    db.execute("INSERT OR REPLACE INTO restored (kind, key, ts) VALUES (?, ?, ?)", (kind, key, time.time()))
    db.commit()

def _run_with_auth_retry(api_call_func, refresh_token, log_fn, *args, **kwargs):
    """Runs api_call_func. If it fails, attempts to refresh token and retries once."""
    access_token = args[0]
    success = api_call_func(*args, **kwargs)
    if not success:
        log_fn("Attempting to refresh token and retry...")
        new_token = _refresh_token(refresh_token, log_fn)
        if new_token:
            access_token = new_token
            new_args = (new_token,) + args[1:]
            success = api_call_func(*new_args, **kwargs)
    return success, access_token


# ---- Core upload function --------------------------------------------------
def upload(user, tokens, src, dry_run=False,
           log=None, progress=None, should_stop=None, insecure=False):
    """
    Uploads contacts, calendar events, tasks, and emails from the backup folder.
    """
    global INSECURE_MODE
    INSECURE_MODE = insecure
    _log = (lambda s: log(s)) if log else (lambda s: None)
    
    if progress:
        import inspect
        try:
            sig = inspect.signature(progress)
            params = list(sig.parameters.values())
            has_var_positional = any(p.kind == inspect.Parameter.VAR_POSITIONAL for p in params)
            if not (has_var_positional or len(params) >= 3):
                orig_progress = progress
                progress = lambda d, t, b=0: orig_progress(d, t)
        except Exception:
            orig_progress = progress
            def _prog_wrapper(d, t, b=0):
                try:
                    orig_progress(d, t, b)
                except TypeError:
                    orig_progress(d, t)
            progress = _prog_wrapper
    
    if not src or not os.path.isdir(src):
        _log("Pick a valid backup folder first.")
        return {"ok": False, "note": "no source"}
    
    access_token, refresh_token = tokens

    # 1. Discover files
    eml_files = []
    contacts_files = []
    calendar_files = []
    tasks_files = []
    
    for root, _dirs, files in os.walk(src):
        low = root.lower()
        for f in files:
            path = os.path.join(root, f)
            fl = f.lower()
            if fl.endswith(".eml"):
                eml_files.append(path)
            elif fl.endswith(".vcf"):
                contacts_files.append(path)
            elif fl.endswith(".ics"):
                if "task" in low:
                    tasks_files.append(path)
                else:
                    calendar_files.append(path)

    # 2. Parse Contacts, Calendar, Tasks
    _log("Scanning and parsing backup files...")
    parsed_contacts = parse_vcards_list(contacts_files, _log) if contacts_files else []
    parsed_events = parse_events_list(calendar_files, _log) if calendar_files else []
    parsed_tasks = parse_tasks_list(tasks_files, _log) if tasks_files else []

    total_emails = len(eml_files)
    total_contacts = len(parsed_contacts)
    total_events = len(parsed_events)
    total_tasks = len(parsed_tasks)

    _log(f"Backup summary found:")
    _log(f"  • Contacts: {total_contacts}")
    _log(f"  • Calendar events: {total_events}")
    _log(f"  • Tasks: {total_tasks}")
    _log(f"  • Emails: {total_emails}")

    total_items = total_emails + total_contacts + total_events + total_tasks
    if total_items == 0:
        _log("Nothing found in the backup folder to restore.")
        return {"ok": True, "uploaded": 0, "found": 0, "note": "empty"}

    if dry_run:
        _log("Dry run enabled. No data was actually restored.")
        return {"ok": True, "uploaded": 0, "found": total_items, "note": "dry_run"}

    # 3. Open Ledger
    db = open_restore_ledger(src)
    processed_items = 0

    stats = {
        "contacts": {"ok": 0, "skip": 0, "fail": 0},
        "events": {"ok": 0, "skip": 0, "fail": 0},
        "tasks": {"ok": 0, "skip": 0, "fail": 0},
        "emails": {"ok": 0, "skip": 0, "fail": 0}
    }

    # 4. Phase 1: Restore Contacts
    if parsed_contacts:
        _log("\n--- Phase 1: Restoring Contacts ---")
        for p, r in parsed_contacts:
            if should_stop and should_stop():
                _log("\n🛑 Restore stopped by user.")
                return {"ok": False, "note": "cancelled"}
            
            key = r["uid"]
            if is_restored(db, "contact", key):
                stats["contacts"]["skip"] += 1
                processed_items += 1
                if progress:
                    progress(processed_items, total_items, 0)
                continue

            body = contact_body(r)
            if not body:
                stats["contacts"]["skip"] += 1
                processed_items += 1
                if progress:
                    progress(processed_items, total_items, 0)
                continue

            success, access_token = _run_with_auth_retry(
                _upload_contact, refresh_token, _log, access_token, body, _log
            )
            
            if success:
                mark_restored(db, "contact", key)
                stats["contacts"]["ok"] += 1
            else:
                stats["contacts"]["fail"] += 1

            processed_items += 1
            if progress:
                # Mock size of contact item is ~500 bytes for throughput speed
                progress(processed_items, total_items, 500)

    # 5. Phase 2: Restore Calendar Events
    if parsed_events:
        _log("\n--- Phase 2: Restoring Calendar Events ---")
        for p, ev in parsed_events:
            if should_stop and should_stop():
                _log("\n🛑 Restore stopped by user.")
                return {"ok": False, "note": "cancelled"}

            key = ev["uid"] or ev["summary"]
            if is_restored(db, "calendar", key):
                stats["events"]["skip"] += 1
                processed_items += 1
                if progress:
                    progress(processed_items, total_items, 0)
                continue

            body = event_body(ev)
            success, access_token = _run_with_auth_retry(
                _upload_event, refresh_token, _log, access_token, body, _log
            )

            if success:
                mark_restored(db, "calendar", key)
                stats["events"]["ok"] += 1
            else:
                stats["events"]["fail"] += 1

            processed_items += 1
            if progress:
                # Mock size of event is ~1000 bytes
                progress(processed_items, total_items, 1000)

    # 6. Phase 3: Restore Tasks
    if parsed_tasks:
        _log("\n--- Phase 3: Restoring Tasks ---")
        for p, t in parsed_tasks:
            if should_stop and should_stop():
                _log("\n🛑 Restore stopped by user.")
                return {"ok": False, "note": "cancelled"}

            key = t["uid"] or t["summary"]
            if is_restored(db, "task", key):
                stats["tasks"]["skip"] += 1
                processed_items += 1
                if progress:
                    progress(processed_items, total_items, 0)
                continue

            body = task_body(t)
            success, access_token = _run_with_auth_retry(
                _upload_task, refresh_token, _log, access_token, body, _log
            )

            if success:
                mark_restored(db, "task", key)
                stats["tasks"]["ok"] += 1
            else:
                stats["tasks"]["fail"] += 1

            processed_items += 1
            if progress:
                # Mock size of task is ~500 bytes
                progress(processed_items, total_items, 500)

    # 7. Phase 4: Restore Emails
    if eml_files:
        _log("\n--- Phase 4: Restoring Emails ---")
        label_cache = {}
        for p in eml_files:
            if should_stop and should_stop():
                _log("\n🛑 Restore stopped by user.")
                return {"ok": False, "note": "cancelled"}

            key = os.path.relpath(p, src)
            if is_restored(db, "email", key):
                stats["emails"]["skip"] += 1
                processed_items += 1
                if progress:
                    progress(processed_items, total_items, 0)
                continue

            folder = folder_of(p, src)
            label_names = labels_for(folder)
            
            label_ids = []
            for name in label_names:
                lid = ensure_label(access_token, name, label_cache, _log)
                label_ids.append(lid)

            success, access_token = _run_with_auth_retry(
                _upload_email, refresh_token, _log, access_token, p, label_ids, _log
            )

            bytes_uploaded = 0
            if success:
                mark_restored(db, "email", key)
                stats["emails"]["ok"] += 1
                try:
                    bytes_uploaded = os.path.getsize(p)
                except Exception:
                    pass
            else:
                stats["emails"]["fail"] += 1

            processed_items += 1
            if progress:
                progress(processed_items, total_items, bytes_uploaded)

    # 8. Complete summary
    _log("\n================ RESTORE SUMMARY ================")
    if total_contacts:
        _log(f"Contacts: {stats['contacts']['ok']} restored, {stats['contacts']['skip']} skipped, {stats['contacts']['fail']} failed.")
    if total_events:
        _log(f"Calendar Events: {stats['events']['ok']} restored, {stats['events']['skip']} skipped, {stats['events']['fail']} failed.")
    if total_tasks:
        _log(f"Tasks: {stats['tasks']['ok']} restored, {stats['tasks']['skip']} skipped, {stats['tasks']['fail']} failed.")
    if total_emails:
        _log(f"Emails: {stats['emails']['ok']} restored, {stats['emails']['skip']} skipped, {stats['emails']['fail']} failed.")
    _log("=================================================")

    return {
        "ok": True,
        "stats": stats,
        "total_restored": stats["contacts"]["ok"] + stats["events"]["ok"] + stats["tasks"]["ok"] + stats["emails"]["ok"]
    }

