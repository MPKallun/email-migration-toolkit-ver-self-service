# Data Backup v1.0.1

Bug-fix release. **If you downloaded v1.0.0, please replace it with this build** — the previous macOS app could fail to open.

## What's fixed

- **macOS app opened then closed immediately.** On some machines the v1.0.0 app would launch and quit straight away without showing a window. This is fixed — the app now starts normally.
  - *What caused it:* the app tried to write its background log file to a location it isn't allowed to when launched by double-click. It now writes the log to your user folder instead (`~/Library/Logs/EAHI Data Backup/` on macOS).

Nothing else has changed — all backup features from v1.0.0 work exactly as before.

## What it backs up

- **Email** (IMAP)
- **Calendar & Tasks** (CalDAV)
- **Contacts** (CardDAV)
- **Drive** — optional, from your webmail "Download your personal data" export `.zip`

You only enter your email and password — the app finds your account automatically, and it's **read-only** (nothing on the server is changed or deleted).

## Downloads

- **macOS** — `Data Backup.dmg` (below). Open it and drag the app to Applications.
- **Windows** — `Data Backup.exe` (below). It runs directly — there's no installer, so just save it somewhere permanent and double-click.

## First launch

Both apps are unsigned, so your operating system blocks them the first time. Follow the steps for your platform below — it's a one-time step.

### macOS

macOS blocks the app the first time. On recent macOS (Sequoia and later) the old right-click → **Open** trick no longer shows an Open button — you only get **"Move to Trash"** or **"Done"**. You can bypass this block using either of the following methods:

#### Option A — Terminal (Faster)

Clear the quarantine flag once from Terminal:

1. Drag **Data Backup** into your **Applications** folder.
2. Open **Terminal** (Applications → Utilities → Terminal).
3. Paste this line and press **Return**:

   ```
   xattr -dr com.apple.quarantine "/Applications/Data Backup.app"
   ```

4. Open the app normally from Applications.

#### Option B — System Settings (GUI-only)

Allow the app via macOS security settings:

1. Drag **Data Backup** into your **Applications** folder.
2. Double-click **Data Backup** in Applications. A message will say the app cannot be opened because the developer cannot be verified. Click **Cancel** or **Done**.
3. Open **System Settings** on your Mac.
4. Click **Privacy & Security** in the left menu, then scroll down to the **Security** section.
5. Under the security message stating `"Data Backup.app" was blocked...`, click **Open Anyway**.
6. Enter your Mac user password or use Touch ID, then click **Open** on the final confirmation prompt.

That's it — one time only. (Option A removes the quarantine flag from the app and everything inside it; Option B grants a system exception. If you kept the app somewhere other than Applications, adjust the path accordingly.)

### Windows

The app isn't code-signed yet, so **Windows SmartScreen** blocks it the first time. There's no "Open" button on the first prompt — you have to reveal it:

1. Save **`Data Backup.exe`** somewhere permanent first (for example a **Data Backup** folder in **Documents**, or your **Desktop**). Don't run it straight from the Downloads pop-up.
2. Double-click **`Data Backup.exe`**. A blue **"Windows protected your PC"** window appears.
3. Click **More info** (the small link in that window).
4. Click the **Run anyway** button that appears.

That's it — one time only. Windows remembers your choice for that file, so it opens normally after this.

> If your antivirus (Microsoft Defender or a third-party product) quarantines or removes the file, restore it and add an exception for `Data Backup.exe` — being unsigned, it can occasionally be flagged by mistake.

> If you hit a certificate/TLS error, tick **"Skip certificate check"** in the app and run again. (Applies to both macOS and Windows.)

## Upgrading from v1.0.0

Download the new build for your platform below. Your settings and any existing backups on disk are untouched.

- **macOS** — drag the new `Data Backup.dmg` over the old app in Applications (replace when prompted).
- **Windows** — replace your old `Data Backup.exe` with the new one (delete or overwrite the old file).
