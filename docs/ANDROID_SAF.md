# Android document access (Storage Access Framework)

NDX uses Android's Storage Access Framework (SAF) for user-selected public files and folders.
The implementation lives in `ndx.android_storage.AndroidSAF` and uses the Android
`Intent`, `ContentResolver`, and `DocumentsContract` APIs through PyJNIus.

## Why SAF

- `ACTION_OPEN_DOCUMENT`: select a file to read (and potentially write, if granted).
- `ACTION_CREATE_DOCUMENT`: choose a destination and create a file.
- `ACTION_OPEN_DOCUMENT_TREE`: grant access to a folder and its descendants.
- Android returns a `content://` URI. A URI is **not** a filesystem path.
- NDX requests and persists only the URI permissions Android returns from the picker.
- No broad shared-storage permission is required.

Do not add `READ_EXTERNAL_STORAGE`, `WRITE_EXTERNAL_STORAGE`, or
`MANAGE_EXTERNAL_STORAGE` for these workflows. Do not convert SAF URIs to
`/storage/emulated/0/...`; use `ContentResolver` streams.

## Requirements

`pyjnius` is already included in the Android Buildozer requirements. SAF is available
only in the python-for-android runtime; importing `ndx.android_storage` on desktop is
safe, but constructing `AndroidSAF` raises `SAFUnavailable`.

`androidstorage4kivy.SharedStorage` is an optional alternative for simple copies into
shared media/Documents locations. NDX does not require it: SAF is preferred when the
user should choose the exact destination or grant access to a folder. Avoid adding
both approaches to the same operation unless there is a clear product need.

## Select and read a document

```python
from ndx.android_storage import AndroidSAF, SAFUnavailable

try:
    saf = AndroidSAF()
except SAFUnavailable:
    saf = None

def selected(result):
    if not result["ok"]:
        return  # user cancelled
    print("URI:", result["uri"])
    print("Persistent grant:", result["persisted"])
    metadata = saf.query_document(result["uri"])
    payload = saf.read_bytes(result["uri"], max_bytes=8 * 1024 * 1024)
    print(metadata, len(payload))

saf.open_document(selected, mime_type="*/*")
```

## Create and export

```python
def destination(result):
    if result["ok"]:
        saf.write_bytes(result["uri"], b"export data")

saf.create_document(
    destination,
    mime_type="application/octet-stream",
    suggested_name="export.bin",
)
```

To export directly into a previously granted folder:

```python
def folder(result):
    if result["ok"]:
        created_uri = saf.export_to_tree(
            result["uri"], "/path/in/app/private/data.json",
            mime_type="application/json",
        )
        print("Exported to:", created_uri)

saf.open_tree(folder)
```

## Import into app-private storage

```python
def selected(result):
    if result["ok"]:
        path = saf.copy_to_private(
            result["uri"], "/path/in/app/private/imported.bin"
        )
        print("Imported:", path)

saf.open_document(selected)
```

`copy_to_private` writes to a temporary sibling and atomically replaces the destination.
`read_bytes` has a configurable memory ceiling; for very large files, use a streaming
implementation rather than loading the entire file into RAM.

## Work with a granted folder

```python
def folder(result):
    if not result["ok"]:
        return
    tree_uri = result["uri"]
    for item in saf.list_tree(tree_uri):
        print(item["name"], item["mime_type"], item["size"], item["uri"])

    new_uri = saf.create_in_tree(
        tree_uri, "backup.json", mime_type="application/json"
    )
    saf.write_bytes(new_uri, b'{"ok": true}')

saf.open_tree(folder)
```

`list_tree` returns immediate children, not a recursive walk. A document provider may
restrict listing, creation, deletion, or metadata. `delete_document` returns the
provider's result; callers should handle failures and never assume every provider
supports every operation.

## Persisted permissions and lifecycle

The picker callback receives a dictionary:

- Success: `{"ok": True, "uri": "content://...", "flags": int, "persisted": bool, "kind": ...}`
- Cancellation: `{"ok": False, "cancelled": True, "kind": ...}`

Store the URI string in app-private settings/database if it must be reopened later.
Persisted grants survive process restarts when the provider supports them. `persisted`
is false when Android/provider refuses the persistable grant. Call `release_permission`
when the app no longer needs a persisted grant. Call `saf.close()` when the owning
component is disposed.

Picker calls are asynchronous: never block the Kivy UI thread waiting for the user.
The implementation currently allows one active picker request per `AndroidSAF`
instance. Static tests cannot prove OEM/provider behavior; test on physical Android
devices and providers such as DocumentsUI, Drive, and OEM file managers.


## Interface integration

The Android Projects screen uses `ACTION_OPEN_DOCUMENT_TREE` to import a selected
folder recursively into the app-private projects directory. Each imported folder is
registered with `ProjectManager`, so it appears in the project list and can be opened
by the editor and Git tools. Project cards expose an export action; the user selects
a destination folder and NDX creates a named project directory there before copying
its contents.

The Editor screen exposes an external-file picker (`ACTION_OPEN_DOCUMENT`) and
`Save as` (`ACTION_CREATE_DOCUMENT`). Opened files are copied into the private
`data/imports` directory before being opened, preserving the editor's local-path
contract. Save as writes the active document to the URI returned by Android. Normal
Save continues to write to the private working copy.

SAF is initialized lazily by `NDXApp.get_saf()`, so desktop startup and tests do not
require PyJNIus. The activity-result listener is unbound when the application stops.
No broad external-storage permission is required.
