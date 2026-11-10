# Termux integration (optional)

Termux is a **separate app**. NDX cannot read its files, and Android's sandbox prevents NDX from reading
`/data/data/com.termux`. Integration is only through Termux's `RUN_COMMAND` intent and is **off unless you enable it**.

Requirements (you do these; NDX cannot): install Termux (F-Droid/GitHub build), set `allow-external-apps=true` in
`~/.termux/termux.properties`, grant NDX the permission `com.termux.permission.RUN_COMMAND`, and keep Termux
unrestricted by battery optimisation (Android 12+ may block background starts).

States (`ndx termux status`): `not_installed`, `unknown_visibility` (fallback if Android still blocks the package
query), `permission_required`, `no_response`/`not tested yet`, `ready`, `error`. `ready` is reached
only after a real probe command (`echo ndx-ok`) answered. Commands: `ndx termux status|test|permission|run <cmd> [args]`.

Data exchange: **shared storage only** (`/storage/emulated/0/...`). The Python runtime refuses to send a script located in
NDX private storage to Termux.

**Manifest:** `android/extra_manifest.xml` declares `<queries><package android:name="com.termux" /></queries>` and is included by `android.extra_manifest_xml` in `buildozer.spec`, so Android 11+ package visibility is declared. This enables package discovery; it does not grant Termux execution permission.

**Status:** transport logic, state machine and result handling are covered by tests with a fake transport. The real
`AndroidIntentTransport` (pyjnius intent + BroadcastReceiver for the result) has **never run on a device**.
