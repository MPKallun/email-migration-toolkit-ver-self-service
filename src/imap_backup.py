#!/usr/bin/env python3
"""
Titan / IMAP mailbox  ->  local .eml backup
===========================================
Pulls a whole mailbox down over IMAP and writes it to local disk in the SAME
layout the migrator reads:  <dest>/<email>/E-Mails/<Folder>/<uid>.eml
So one backup folder is BOTH:
  • your offline cold archive (copy it to the backup drive), AND
  • a drop-in --src for eml_to_gmail.py / a --mailbox for batch_migrate.py.

WHY IT'S BUILT THIS WAY
- Titan's "Download your personal data" archive SILENTLY DROPS the E-Mails module
  on anything but tiny mailboxes -- a backup that looks fine but has no mail.
  IMAP is Titan's own recommended path and it scales; this is that path.
- Reads with BODY.PEEK[] and SELECTs read-only: it NEVER marks mail as read,
  never moves or deletes anything on the server.
- No Gmail anywhere in this tool, so the 500 MB/day Gmail IMAP UPLOAD cap does
  not apply. Downloading from Titan is bounded only by Titan's connection limits.
- Resumable + idempotent: a SQLite ledger records every (folder, UIDVALIDITY, UID)
  saved, so a dropped connection or a re-run continues instead of restarting.
- Reconciliation report: server message count per folder vs. files saved.
- Stdlib only (imaplib/email/sqlite3) -- nothing to pip install for the engine.

This module is BOTH a CLI and an importable library:
    from imap_backup import backup
    backup(host=..., port=993, ssl=True, user=..., password=..., dest=...,
           dry_run=True, log=print, progress=lambda d, t: ...)
The GUI imports backup() and runs it on a thread (so the packaged app never has
to shell out to a python interpreter that isn't there once it's frozen).

AUTH (no password reset needed)
  Generate an APP-SPECIFIC PASSWORD in the mailbox's security settings.

CLI USAGE
  IMAP_PASSWORD='app-password' python3 imap_backup.py \
      --user someone@equgruppo.com --dest "/Volumes/Backup/EAHI-mail" --dry-run
"""
import argparse, getpass, imaplib, os, re, socket, sqlite3, sys, time
import ssl as _ssl
from collections import defaultdict
from utils import translate_error, retry, log_debug

imaplib._MAXLINE = 10_000_000          # some folders return very long UID lists

# ---- parse one LIST line -> (flags, separator, raw-name) --------------------
_LIST_RE = re.compile(rb'^\((?P<flags>[^)]*)\)\s+(?:"(?P<sep>[^"]*)"|NIL)\s+(?P<name>.*)$')

def parse_list_line(line):
    if isinstance(line, tuple):                 # literal{} form: name is in line[1]
        line = line[0]
    m = _LIST_RE.match(line.strip())
    if not m:
        return None
    flags = m.group("flags").decode("ascii", "replace").split()
    name = m.group("name").strip()
    if name.startswith(b'"') and name.endswith(b'"'):
        name = name[1:-1]
    return flags, _imap_utf7_decode(name)

def _imap_utf7_decode(b):
    """Decode modified-UTF7 IMAP folder names; fall back to latin-1 if odd."""
    try:
        s = b.decode("ascii")
    except Exception:
        return b.decode("latin-1", "replace")
    if "&" not in s:
        return s
    out, i = [], 0
    while i < len(s):
        c = s[i]
        if c == "&":
            j = s.find("-", i)
            if j == -1: out.append(s[i:]); break
            chunk = s[i+1:j]
            if chunk == "":
                out.append("&")
            else:
                import base64
                data = chunk.replace(",", "/")
                pad = "=" * (-len(data) % 4)
                out.append(base64.b64decode(data + pad).decode("utf-16-be", "replace"))
            i = j + 1
        else:
            out.append(c); i += 1
    return "".join(out)

# ---- pick the local sub-folder name the migrator will understand -----------
def canonical_dir(flags, name):
    f = {x.lower() for x in flags}
    if name.upper() == "INBOX":            return "Inbox"
    if "\\sent" in f:                      return "Sent"
    if "\\drafts" in f:                    return "Drafts"
    if "\\junk" in f:                      return "Junk"
    if "\\trash" in f:                     return "Trash"
    if "\\archive" in f or "\\all" in f:   return "Archive"
    low = name.lower()
    for key, out in (("sent","Sent"),("draft","Drafts"),("junk","Junk"),
                     ("spam","Spam"),("trash","Trash"),("deleted","Trash")):
        if low == key or low.endswith("." + key) or low.endswith("/" + key):
            return out
    return _safe(name)

def _safe(name):
    name = name.replace("INBOX.", "").replace("INBOX/", "")
    name = re.sub(r'[\\/:*?"<>|]+', "_", name).replace(".", " ").replace("  ", " ").strip()
    return name or "Folder"

# ---- IMAP connect / reconnect ----------------------------------------------
def connect(host, port, ssl, user, pw, ssl_ctx=None):
    if ssl:
        M = imaplib.IMAP4_SSL(host, port, ssl_context=ssl_ctx)
    else:
        M = imaplib.IMAP4(host, port)
    M.login(user, pw)
    return M

def status_count(M, mailbox):
    """(uidvalidity, message_count) for a mailbox without selecting it."""
    typ, data = M.status(_q(mailbox), "(UIDVALIDITY MESSAGES)")
    if typ != "OK" or not data or not data[0]:
        return None, 0
    s = data[0].decode("ascii", "replace")
    uv = re.search(r"UIDVALIDITY\s+(\d+)", s)
    mc = re.search(r"MESSAGES\s+(\d+)", s)
    return (int(uv.group(1)) if uv else None,
            int(mc.group(1)) if mc else 0)

def _q(name):
    return '"%s"' % name.replace('"', '\\"')

def open_ledger(path):
    db = sqlite3.connect(path)
    db.execute("""CREATE TABLE IF NOT EXISTS saved(
        folder TEXT, uidvalidity INTEGER, uid INTEGER, path TEXT, ts REAL,
        PRIMARY KEY(folder, uidvalidity, uid))""")
    db.commit()
    return db

# ============================================================================
# Importable core -- the GUI and the CLI both call this.
# ============================================================================
def backup(host, port, ssl, user, password, dest, dry_run=False, only=None,
           log=None, progress=None, should_stop=None, insecure=False):
    """Back up one mailbox. Returns a result dict. Callbacks:
       log(str)            -> a line of human output
       progress(done,total)-> for a progress bar
       should_stop()->bool -> return True to cancel cleanly mid-run
    """
    only = only or []
    ctx = _ssl._create_unverified_context() if insecure else None
    _log = (lambda s: log(s)) if log else (lambda s: None)
    _stop = should_stop or (lambda: False)
    
    if progress:
        import inspect
        try:
            sig = inspect.signature(progress)
            params = list(sig.parameters.values())
            has_var_positional = any(p.kind == inspect.Parameter.VAR_POSITIONAL for p in params)
            if has_var_positional or len(params) >= 3:
                _prog = progress
            else:
                _prog = lambda d, t, b=0: progress(d, t)
        except Exception:
            def _prog_wrapper(d, t, b=0):
                try:
                    progress(d, t, b)
                except TypeError:
                    progress(d, t)
            _prog = _prog_wrapper
    else:
        _prog = lambda d, t, b=0: None

    box_root = os.path.join(dest, user)
    eml_root = os.path.join(box_root, "E-Mails")
    if not dry_run:
        os.makedirs(eml_root, exist_ok=True)
    ledger_path = os.path.join(box_root if not dry_run else dest,
                               user + "._backup_ledger.sqlite")

    try:
        M = connect(host, port, ssl, user, password, ssl_ctx=ctx)
    except Exception as e:
        clean_err = translate_error(e)
        _log("LOGIN FAILED for %s: %s" % (user, clean_err))
        return {"ok": False, "exit": 2, "error": clean_err, "dest": box_root}

    # discover folders
    try:
        typ, raw = M.list()
    except Exception as e:
        clean_err = translate_error(e)
        _log(f"FOLDER LISTING FAILED for {user}: {clean_err}")
        return {"ok": False, "exit": 2, "error": clean_err, "dest": box_root}

    folders = []
    if typ == "OK":
        for line in raw:
            parsed = parse_list_line(line)
            if not parsed:
                continue
            flags, name = parsed
            if "\\noselect" in {x.lower() for x in flags}:
                continue
            if only and name not in only:
                continue
            folders.append((name, canonical_dir(flags, name)))

    if not folders:
        try: M.logout()
        except Exception: pass
        _log("No selectable folders found.")
        return {"ok": False, "exit": 2, "error": "no folders", "dest": box_root}

    # plan: server counts (also the dry-run output)
    plan, grand = [], 0
    for name, sub in folders:
        try:
            uv, mc = status_count(M, name)
            plan.append((name, sub, uv, mc))
            grand += mc
        except Exception as e:
            clean_err = translate_error(e)
            _log(f"FOLDER STATUS FAILED for {name}: {clean_err}")
            return {"ok": False, "exit": 2, "error": clean_err, "dest": box_root}

    _log("\n%-26s %-12s %10s" % ("SERVER FOLDER", "-> LOCAL", "MESSAGES"))
    _log("-" * 52)
    for name, sub, uv, mc in plan:
        _log("%-26s %-12s %10d" % (name[:26], sub[:12], mc))
    _log("-" * 52)
    _log("%-26s %-12s %10d" % ("TOTAL", "", grand))
    _log("\nDESTINATION: " + box_root)

    if dry_run:
        _log("\nMODE: DRY-RUN (connected + counted; nothing downloaded)")
        try: M.logout()
        except Exception: pass
        return {"ok": True, "exit": 0, "dry_run": True, "dest": box_root,
                "server_total": grand, "report": ""}

    db = open_ledger(ledger_path)
    stats = defaultdict(lambda: {"server": 0, "new": 0, "skip": 0, "fail": 0})
    done_total, fails, stopped = 0, [], False

    def reconnect():
        nonlocal M
        try: M.logout()
        except Exception: pass
        for attempt in range(5):
            try:
                M = connect(host, port, ssl, user, password, ssl_ctx=ctx); return
            except Exception:
                time.sleep(2 ** attempt)
        raise RuntimeError("could not reconnect to IMAP")

    for name, sub, uv, mc in plan:
        if _stop(): stopped = True; break
        stats[sub]["server"] += mc
        outdir = os.path.join(eml_root, sub)
        os.makedirs(outdir, exist_ok=True)
        try:
            M.select(_q(name), readonly=True)        # read-only: never alters the server
            typ, data = M.uid("SEARCH", None, "ALL")
            uids = data[0].split() if (typ == "OK" and data and data[0]) else []
        except Exception:
            try:
                reconnect()
                M.select(_q(name), readonly=True)
                typ, data = M.uid("SEARCH", None, "ALL")
                uids = data[0].split() if (typ == "OK" and data and data[0]) else []
            except Exception as select_err:
                clean_err = translate_error(select_err)
                _log(f"FOLDER ACCESS FAILED for {name}: {clean_err}")
                return {"ok": False, "exit": 1, "error": clean_err, "dest": box_root}

        for uidb in uids:
            if _stop(): stopped = True; break
            uid = int(uidb)
            bytes_transferred = 0
            if db.execute("SELECT 1 FROM saved WHERE folder=? AND uidvalidity=? AND uid=?",
                          (name, uv, uid)).fetchone():
                stats[sub]["skip"] += 1
            else:
                raw_msg = None
                for attempt in range(4):
                    try:
                        typ, fdata = M.uid("FETCH", uidb, "(BODY.PEEK[])")
                        if typ == "OK" and fdata and isinstance(fdata[0], tuple):
                            raw_msg = fdata[0][1]
                        break
                    except Exception:
                        if attempt == 3:
                            break
                        try:
                            reconnect()
                            M.select(_q(name), readonly=True)
                        except Exception:
                            pass
                if raw_msg:
                    path = os.path.join(outdir, "%d.eml" % uid)
                    try:
                        with open(path, "wb") as fh:
                            fh.write(raw_msg)
                        db.execute("INSERT OR IGNORE INTO saved VALUES(?,?,?,?,?)",
                                   (name, uv, uid, path, time.time()))
                        db.commit()
                        stats[sub]["new"] += 1
                        bytes_transferred = len(raw_msg)
                    except Exception as write_err:
                        clean_err = translate_error(write_err)
                        _log(f"FILE WRITE FAILED for {uid}.eml: {clean_err}")
                        stats[sub]["fail"] += 1
                        fails.append((name, uid))
                else:
                    stats[sub]["fail"] += 1
                    fails.append((name, uid))
            done_total += 1
            _prog(done_total, grand, bytes_transferred)
        if stopped: break

    try: M.logout()
    except Exception: pass

    # reconciliation report -- the "nothing missing" proof
    lines = ["\n%-14s %9s %9s %9s %9s" % ("FOLDER", "SERVER", "NEW", "HAD", "FAILED"),
             "-" * 56]
    tot = defaultdict(int)
    for sub in sorted(stats):
        s = stats[sub]
        lines.append("%-14s %9d %9d %9d %9d" % (sub, s["server"], s["new"], s["skip"], s["fail"]))
        for k in s: tot[k] += s[k]
    lines.append("-" * 56)
    lines.append("%-14s %9d %9d %9d %9d" % ("TOTAL", tot["server"], tot["new"], tot["skip"], tot["fail"]))
    saved = tot["new"] + tot["skip"]
    lines.append("\nSAVED (new + already had): %d   SERVER TOTAL: %d" % (saved, tot["server"]))
    if fails:
        lines.append("\nFAILED MESSAGES (folder, uid):")
        for name, uid in fails[:20]:
            lines.append("   %s  uid=%d" % (name, uid))
    if stopped:
        verdict = "STOPPED by user -- partial backup (re-run to finish; it resumes)"
        ok = False
    elif tot["fail"] == 0 and saved >= tot["server"]:
        verdict = "ALL ACCOUNTED FOR"
        ok = True
    else:
        verdict = "MISMATCH -- investigate before trusting this backup"
        ok = False
    lines.append("\nRESULT: " + verdict)
    report = "\n".join(lines)
    _log(report)

    try:
        with open(os.path.join(box_root, "_reconciliation.txt"), "w", encoding="utf-8") as fh:
            fh.write("Backup of %s from %s\n%s\n" % (user, host, time.ctime()) + report + "\n")
    except Exception:
        pass

    _log("\nBackup folder (also the migrator --src): " + box_root)
    return {"ok": ok, "exit": 0 if ok else 1, "dry_run": False, "dest": box_root,
            "stopped": stopped, "totals": dict(tot), "report": report,
            "verdict": verdict}

# ============================================================================
# Fold a Titan personal-data export (.zip) into the same backup folder.
# Email is intentionally SKIPPED -- the IMAP backup above is the authoritative,
# reliable copy (the export's E-Mails module is the one that silently fails).
# This grabs the modules IMAP cannot: Calendar, Address book, Drive, Tasks.
# ============================================================================
import zipfile, shutil

_EMAIL_DIR_NAMES = {"e-mails", "emails", "email", "mail"}

def _has_email_component(name):
    parts = [p.lower() for p in name.replace("\\", "/").split("/") if p]
    return any(p in _EMAIL_DIR_NAMES for p in parts)

def fold_in_data_export(zip_paths, box_root, dry_run=False, log=None):
    """Extract the non-email modules of a Titan 'Download your personal data'
    export (one .zip, or several split parts) into box_root, alongside E-Mails/.
    Skips the export's own email. Returns {top_folder: file_count}."""
    _log = (lambda s: log(s)) if log else (lambda s: None)
    added = {}
    if not dry_run:
        os.makedirs(box_root, exist_ok=True)
    real_root = os.path.realpath(box_root)
    for zp in zip_paths:
        try:
            zf = zipfile.ZipFile(zp)
        except Exception as e:
            _log("  ! could not open %s: %s" % (os.path.basename(zp), e))
            continue
        with zf:
            for info in zf.infolist():
                name = info.filename
                if name.endswith("/"):
                    continue
                if _has_email_component(name):
                    continue                      # email = IMAP's job; skip
                dest_path = os.path.realpath(os.path.join(box_root, name))
                if dest_path != real_root and not dest_path.startswith(real_root + os.sep):
                    continue                      # zip-slip guard
                parts = [p for p in name.replace("\\", "/").split("/") if p]
                top = parts[0] if len(parts) > 1 else "(root)"
                added[top] = added.get(top, 0) + 1
                if not dry_run:
                    os.makedirs(os.path.dirname(dest_path), exist_ok=True)
                    with zf.open(info) as src, open(dest_path, "wb") as out:
                        shutil.copyfileobj(src, out)
    if added:
        _log("\n%-22s %8s" % ("DATA EXPORT FOLDER", "FILES"))
        _log("-" * 32)
        for k in sorted(added):
            _log("%-22s %8d" % (k[:22], added[k]))
        _log("(email skipped — backed up via IMAP above)")
    else:
        _log("\nDATA EXPORT: nothing added (empty, or the zip held only email).")
    return added


# ============================================================================
# CLI wrapper
# ============================================================================
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--user", required=True, help="full email address (IMAP username)")
    ap.add_argument("--dest", required=True, help="backup root; mailbox saved to <dest>/<user>/")
    ap.add_argument("--host", default=os.environ.get("IMAP_HOST", "imap.secureserver.net"))
    ap.add_argument("--port", type=int, default=int(os.environ.get("IMAP_PORT", "993")))
    ap.add_argument("--no-ssl", action="store_true", help="plain IMAP (not recommended)")
    ap.add_argument("--password", help="app password (prefer $IMAP_PASSWORD instead)")
    ap.add_argument("--dry-run", action="store_true", help="connect + count only; no download")
    ap.add_argument("--progress", action="store_true", help="emit '@P done total' for a GUI bar")
    ap.add_argument("--insecure", action="store_true", help="skip TLS cert verification (macOS local-issuer workaround)")
    ap.add_argument("--only", action="append", default=[],
                    help="back up only this folder (repeatable); default = all folders")
    ap.add_argument("--archive", action="append", default=[],
                    help="Titan personal-data export .zip to fold in (calendar/contacts/Drive/tasks); repeatable")
    args = ap.parse_args()

    pw = os.environ.get("IMAP_PASSWORD") or args.password
    if not pw:
        if sys.stdin.isatty():
            pw = getpass.getpass("App password for %s: " % args.user)
        else:
            sys.exit("No password: set $IMAP_PASSWORD or pass --password.")

    prog = (lambda d, t: print("@P %d %d" % (d, t), flush=True)) if args.progress else None
    res = backup(host=args.host, port=args.port, ssl=not args.no_ssl, user=args.user,
                 password=pw, dest=args.dest, dry_run=args.dry_run, only=args.only,
                 log=lambda s: print(s), progress=prog, insecure=args.insecure)
    if args.archive:
        fold_in_data_export(args.archive, res.get("dest"), dry_run=args.dry_run,
                            log=lambda s: print(s))
    sys.exit(res.get("exit", 1))


if __name__ == "__main__":
    main()
