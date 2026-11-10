# Android permissions
| Permission | Why | Needs user action |
|---|---|---|
| INTERNET | GitHub package install/search, rootfs downloads, local servers (loopback) | no |
| ACCESS_NETWORK_STATE | detect offline mode | no |
| FOREGROUND_SERVICE | keep-alive service notification | no |
| POST_NOTIFICATIONS | show that notification (Android 13+) | yes, runtime prompt, only when you press "Allow notifications" in Settings |
| com.termux.permission.RUN_COMMAND | OPTIONAL Termux integration (run commands in Termux). Dangerous permission: NDX never grants it itself; Termux must also set `allow-external-apps=true`. Not verified on a device (TERMUX.md) | yes, runtime grant + Termux setting |
| WAKE_LOCK | keep CPU awake during long builds/servers | no |
No broad storage permissions (`READ_EXTERNAL_STORAGE`, `WRITE_EXTERNAL_STORAGE`, or `MANAGE_EXTERNAL_STORAGE`)
are requested. App data lives in app-private storage. Android document import/export is provided by the Storage
Access Framework (SAF): the user selects a document or folder in the system picker, and Android grants access only
to the returned `content://` URI. The app must use `ContentResolver`; a SAF URI is **not** a filesystem path.
Persisted URI grants are attempted where the provider supports them. No general access to shared storage is implied.
Things that need **outside-the-app** authorization: Developer options, USB/Wireless debugging and its pairing code,
`pm grant` of privileged permissions, Accessibility services. NDX never tries to bypass these.
