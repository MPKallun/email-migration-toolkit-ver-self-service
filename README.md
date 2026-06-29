# Data Backup (EAHI)

A one-login tool that backs up your **GoDaddy / Professional Email** mailbox to a folder
on your own computer:

- **Email** — over IMAP
- **Calendar + Tasks** — over CalDAV
- **Contacts** — over CardDAV
- **Drive** — folded in from the webmail *“Download your personal data”* export `.zip` (optional)

You only enter your **email + password** — the app discovers your account automatically
(RFC 6764). It is **read-only**: nothing on the server is changed or deleted.

The app has two tabs: **Backup** (above) and **Restore** — *(Restore is currently a scaffold: it reads a backup folder and reports what it would upload to Gmail; the actual upload isn't wired up yet.)*

---

## Download (staff)

Get the latest build from **Releases**:
**https://github.com/MPKallun/email-migration-toolkit-ver-self-service/releases/latest**

- **macOS** — download `Data Backup.dmg`, open it, drag the app to **Applications**.
- **Windows** — download `Data Backup.exe`.

**First launch (the app is unsigned):**
- macOS — right-click the app → **Open** → **Open**. (On newer macOS: **System Settings → Privacy & Security → Open Anyway**.)
- Windows — **More info → Run anyway**.

*(The one-time warning is only because the app isn’t code-signed; signing is optional and costs money — see “Building”.)*

### Alternative: Run via Launcher Script (Bypass all OS security blocks)

If you or your colleagues cannot bypass the unsigned application blocks, you can run the application directly from the source folder with a single click:
1. Download this repository as a ZIP (click **Code → Download ZIP** on GitHub) and extract it.
2. Double-click the launcher script for your OS:
   - **macOS**: Double-click `run_mac.command`
   - **Windows**: Double-click `run_windows.bat`
3. The script will automatically verify Python is installed, configure a local virtual environment, install the GUI dependency (`customtkinter`), and launch the app with zero OS security warnings.

---

## How to use

1. Open the app.
2. Enter your **email address** and **password** (or an **app password** if 2-step verification is on).
3. Choose a **folder to save into**.
4. *(Optional, for Drive)* in webmail → **Settings → Download your personal data → tick Drive → download the `.zip`**, then **Add export .zip(s)…**.
5. Click **Back up my data**.

If you hit a **TLS / certificate error**, tick **“Skip certificate check”** and run again.

**Result:** `‹your folder›/‹your email›/` containing `E-Mails/`, `Calendar/`, `Tasks/`,
`Address book/` (and `Drive/` if you added the zip), plus a reconciliation report.

---

## Run from source (developers)

```bash
pip install customtkinter      # the only dependency for the GUI
python3 src/gui.py
```

The engines also run standalone from the command line:

```bash
# email (stdlib only):
python3 src/imap_backup.py  --user you@equgruppo.com --dest /path/to/backup --dry-run

# calendar / tasks / contacts (stdlib only):
python3 src/caldav_backup.py --user you@equgruppo.com --dest /path/to/backup \
    --url "https://am1.myprofessionalmail.com/.well-known/caldav"
```

---

## Building the installers

PyInstaller can’t cross-compile — build on each OS:

```bash
# macOS
bash packaging/build_mac.sh        # -> dist/Data Backup.app
bash packaging/make_dmg.sh         # -> dist/Data Backup.dmg   (upload this to a Release)

# Windows
packaging\build_windows.bat     # -> dist\Data Backup.exe
```

Builds are **unsigned** (users get a one-time warning). To remove it, code-sign:
the **Apple Developer Program ($99/yr)** for macOS, and a **code-signing certificate**
for Windows.

**Publishing a download link:** create a **GitHub Release** and attach
`Data Backup.dmg` / `Data Backup.exe` as assets — Releases then gives a permanent
download URL. This is free; no paid account needed.

---

## Servers & behaviour

| Data | Protocol | Host |
|---|---|---|
| Email | IMAP (SSL) | `imap.secureserver.net:993` |
| Calendar · Tasks · Contacts | CalDAV / CardDAV | `am1.myprofessionalmail.com` (RFC 6764 discovery) |
| Drive | — (no live endpoint) | webmail export `.zip` |

Read-only throughout, and **resumable** — re-running continues where it left off.

---

## Repo layout

```
src/        imap_backup.py · caldav_backup.py · gmail_upload.py (Restore stub) · gui.py
packaging/  app.spec · build_mac.sh · build_windows.bat · make_dmg.sh
README.md · .gitignore
```

## Security

- Use an **app password** if your account has 2-step verification.
- **Never commit** secrets or anyone’s backup data — the included `.gitignore` blocks
  `.env`, credential/token files, `*.sqlite` ledgers, and backed-up `.eml/.vcf/.ics` plus
  per-user output folders.

---

*EAHI — Equgruppo Assetto Holdings, Inc. · internal IT tool.*
