"""Android Storage Access Framework bridge for Kivy/python-for-android.

Uses PyJNIus and Android ContentResolver. All picker operations are asynchronous;
selected documents are represented by content:// URIs, never guessed filesystem paths.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Callable

REQUEST_OPEN = 0x4E10
REQUEST_CREATE = 0x4E11
REQUEST_TREE = 0x4E12


class SAFUnavailable(RuntimeError):
    """Raised when Android's activity or PyJNIus APIs are unavailable."""


class AndroidSAF:
    def __init__(self):
        try:
            from android import activity, mActivity
            from jnius import autoclass, jarray
        except Exception as exc:
            raise SAFUnavailable(
                "Android SAF requires python-for-android and PyJNIus"
            ) from exc
        self._activity_module = activity
        self._activity = mActivity
        self._autoclass = autoclass
        self._jarray = jarray
        self._Intent = autoclass("android.content.Intent")
        self._Activity = autoclass("android.app.Activity")
        self._Uri = autoclass("android.net.Uri")
        self._DocumentsContract = autoclass("android.provider.DocumentsContract")
        self._OpenableColumns = autoclass("android.provider.OpenableColumns")
        self._callback = None
        self._kind = None
        activity.bind(on_activity_result=self._on_activity_result)

    def close(self):
        """Unbind the activity callback when the owner is disposed."""
        try:
            self._activity_module.unbind(on_activity_result=self._on_activity_result)
        except Exception:
            pass
        self._callback = None
        self._kind = None

    def _launch(self, kind, action, request_code, callback, mime_type=None,
                suggested_name=None, initial_uri=None):
        if self._callback is not None:
            raise RuntimeError("A document picker request is already active")
        intent = self._Intent(action)
        intent.addFlags(
            self._Intent.FLAG_GRANT_READ_URI_PERMISSION
            | self._Intent.FLAG_GRANT_WRITE_URI_PERMISSION
            | self._Intent.FLAG_GRANT_PERSISTABLE_URI_PERMISSION
        )
        if kind in ("open", "create"):
            intent.addCategory(self._Intent.CATEGORY_OPENABLE)
            intent.setType(mime_type or (
                "*/*" if kind == "open" else "application/octet-stream"
            ))
        if kind == "create" and suggested_name:
            intent.putExtra(self._Intent.EXTRA_TITLE, str(suggested_name))
        if initial_uri is not None and hasattr(self._Intent, "EXTRA_INITIAL_URI"):
            intent.putExtra(self._Intent.EXTRA_INITIAL_URI, self._uri(initial_uri))
        self._callback = callback
        self._kind = (kind, request_code)
        try:
            self._activity.startActivityForResult(intent, request_code)
        except Exception:
            self._callback = None
            self._kind = None
            raise

    def open_document(self, callback: Callable, mime_type="*/*"):
        """Show the system picker for one readable document."""
        self._launch("open", self._Intent.ACTION_OPEN_DOCUMENT,
                     REQUEST_OPEN, callback, mime_type)

    def create_document(self, callback: Callable,
                        mime_type="application/octet-stream",
                        suggested_name="document.bin"):
        """Show the system picker to choose a destination and create a document."""
        self._launch("create", self._Intent.ACTION_CREATE_DOCUMENT,
                     REQUEST_CREATE, callback, mime_type, suggested_name)

    def open_tree(self, callback: Callable, initial_uri=None):
        """Ask the user to grant access to a directory tree."""
        self._launch("tree", self._Intent.ACTION_OPEN_DOCUMENT_TREE,
                     REQUEST_TREE, callback, initial_uri=initial_uri)

    def _on_activity_result(self, request_code, result_code, data):
        if self._kind is None or request_code != self._kind[1]:
            return
        kind, _ = self._kind
        callback = self._callback
        self._callback = None
        self._kind = None
        if result_code != self._Activity.RESULT_OK or data is None:
            callback({"ok": False, "cancelled": True, "kind": kind})
            return
        uri = data.getData()
        if uri is None:
            callback({"ok": False, "cancelled": True, "kind": kind})
            return
        grant_mask = (
            self._Intent.FLAG_GRANT_READ_URI_PERMISSION
            | self._Intent.FLAG_GRANT_WRITE_URI_PERMISSION
        )
        flags = int(data.getFlags()) & grant_mask
        persisted = False
        if flags:
            try:
                self._activity.getContentResolver().takePersistableUriPermission(uri, flags)
                persisted = True
            except Exception:
                # Some document providers only grant temporary access.
                pass
        callback({
            "ok": True, "uri": str(uri.toString()), "flags": flags,
            "persisted": persisted, "kind": kind,
        })

    def release_permission(self, uri_string, flags=None):
        """Release a previously persisted URI grant."""
        uri = self._uri(uri_string)
        if flags is None:
            flags = (self._Intent.FLAG_GRANT_READ_URI_PERMISSION
                     | self._Intent.FLAG_GRANT_WRITE_URI_PERMISSION)
        self._activity.getContentResolver().releasePersistableUriPermission(uri, int(flags))

    def read_bytes(self, uri_string, max_bytes=32 *  1024 * 1024):
        """Read a document URI through ContentResolver with a size ceiling."""
        if max_bytes < 0:
            raise ValueError("max_bytes must be non-negative")
        stream = self._resolver().openInputStream(self._uri(uri_string))
        if stream is None:
            raise OSError("Document provider did not open the selected document")
        out = bytearray()
        buf = self._jarray("b")([0] * 65536)
        try:
            while True:
                count = int(stream.read(buf))
                if count < 0:
                    break
                if len(out) + count > max_bytes:
                    raise ValueError("selected document exceeds max_bytes")
                out.extend((int(buf[i]) & 0xff) for i in range(count))
        finally:
            stream.close()
        return bytes(out)

    def write_bytes(self, uri_string, data, mode="wt"):
        """Write bytes to a document URI selected with ACTION_CREATE_DOCUMENT."""
        if not isinstance(data, (bytes, bytearray, memoryview)):
            raise TypeError("data must be bytes-like")
        raw = bytes(data)
        stream = self._resolver().openOutputStream(self._uri(uri_string), mode)
        if stream is None:
            raise OSError("Document provider did not open the destination")
        try:
            # Java byte arrays use signed bytes.
            java_data = self._jarray("b")(
                [b if b < 128 else b - 256 for b in raw]
            )
            stream.write(java_data)
            stream.flush()
        finally:
            stream.close()

    def copy_to_private(self, uri_string, destination, max_bytes=128 * 1024 * 1024):
        """Copy a selected SAF document into an app-private filesystem path."""
        payload = self.read_bytes(uri_string, max_bytes=max_bytes)
        target = Path(destination)
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(target.name + ".ndx-tmp")
        try:
            with open(temporary, "wb") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(str(temporary), str(target))
        finally:
            try:
                temporary.unlink()
            except OSError:
                pass
        return str(target)

    def copy_from_private(self, source, destination_uri):
        """Export a private file to a URI created/selected through SAF."""
        with open(source, "rb") as handle:
            payload = handle.read()
        self.write_bytes(destination_uri, payload)
        return len(payload)

    def query_document(self, uri_string):
        """Return provider metadata (name, size, MIME type) for a document URI."""
        uri = self._uri(uri_string)
        cursor = self._resolver().query(uri, None, None, None, None)
        if cursor is None:
            return {}
        result = {}
        try:
            if cursor.moveToFirst():
                name_col = cursor.getColumnIndex(self._OpenableColumns.DISPLAY_NAME)
                size_col = cursor.getColumnIndex(self._OpenableColumns.SIZE)
                if name_col >= 0:
                    result["name"] = str(cursor.getString(name_col))
                if size_col >= 0 and not cursor.isNull(size_col):
                    result["size"] = int(cursor.getLong(size_col))
                mime = self._resolver().getType(uri)
                if mime:
                    result["mime_type"] = str(mime)
        finally:
            cursor.close()
        return result

    def list_tree(self, tree_uri):
        """List immediate children of a granted tree as URI/metadata dictionaries."""
        tree = self._uri(tree_uri)
        if not self._DocumentsContract.isTreeUri(tree):
            raise ValueError("URI is not a document tree")
        tree_id = self._current_document_id(tree)
        children_uri = self._DocumentsContract.buildChildDocumentsUriUsingTree(tree, tree_id)
        columns = [
            self._DocumentsContract.Document.COLUMN_DOCUMENT_ID,
            self._OpenableColumns.DISPLAY_NAME,
            self._DocumentsContract.Document.COLUMN_MIME_TYPE,
            self._OpenableColumns.SIZE,
            self._DocumentsContract.Document.COLUMN_LAST_MODIFIED,
        ]
        cursor = self._resolver().query(children_uri, columns, None, None, None)
        if cursor is None:
            return []
        items = []
        try:
            while cursor.moveToNext():
                doc_id = cursor.getString(cursor.getColumnIndex(
                    self._DocumentsContract.Document.COLUMN_DOCUMENT_ID))
                child_uri = self._DocumentsContract.buildDocumentUriUsingTree(tree, doc_id)
                def value(column):
                    index = cursor.getColumnIndex(column)
                    return None if index < 0 or cursor.isNull(index) else cursor.getString(index)
                size_index = cursor.getColumnIndex(self._OpenableColumns.SIZE)
                items.append({
                    "uri": str(child_uri.toString()),
                    "name": value(self._OpenableColumns.DISPLAY_NAME),
                    "mime_type": value(self._DocumentsContract.Document.COLUMN_MIME_TYPE),
                    "size": (int(cursor.getLong(size_index))
                             if size_index >= 0 and not cursor.isNull(size_index) else None),
                    "last_modified": value(self._DocumentsContract.Document.COLUMN_LAST_MODIFIED),
                })
        finally:
            cursor.close()
        return items

    def create_in_tree(self, tree_uri, name, mime_type="application/octet-stream"):
        """Create a child document inside a user-granted tree."""
        if not name or "/" in name or "\\" in name:
            raise ValueError("name must be a single non-empty filename")
        tree = self._uri(tree_uri)
        if not self._DocumentsContract.isTreeUri(tree):
            raise ValueError("URI is not a document tree")
        doc_id = self._current_document_id(tree)
        parent = self._DocumentsContract.buildDocumentUriUsingTree(tree, doc_id)
        created = self._DocumentsContract.createDocument(
            self._resolver(), parent, mime_type, name
        )
        if created is None:
            raise OSError("Document provider could not create the document")
        return str(created.toString())

    def export_to_tree(self, tree_uri, source_path, name=None, mime_type="application/octet-stream"):
        """Create a file in a granted tree and copy a private file into it."""
        source = Path(source_path)
        target_uri = self.create_in_tree(
            tree_uri, name or source.name, mime_type=mime_type
        )
        try:
            self.copy_from_private(str(source), target_uri)
        except Exception:
            # Best-effort cleanup if the provider created an empty/partial file.
            try:
                self.delete_document(target_uri)
            except Exception:
                pass
            raise
        return target_uri


    def copy_tree_to_private(self, tree_uri, destination, max_files=10000):
        """Recursively import a SAF directory into app-private storage.

        Every source is accessed through ContentResolver; no content:// URI is
        converted into a guessed filesystem path. Symlinks are not applicable
        to document providers.
        """
        root = Path(destination)
        root.mkdir(parents=True, exist_ok=True)
        count = [0]

        def walk(parent_uri, target_dir):
            for item in self.list_tree(parent_uri):
                count[0] += 1
                if count[0] > max_files:
                    raise ValueError("folder contains too many entries")
                name = self._safe_name(item.get("name") or "unnamed")
                target = target_dir / name
                mime = item.get("mime_type") or ""
                if mime == "vnd.android.document/directory":
                    target.mkdir(parents=True, exist_ok=True)
                    walk(item["uri"], target)
                else:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    payload = self.read_bytes(item["uri"], max_bytes=256 * 1024 * 1024)
                    with open(target, "wb") as handle:
                        handle.write(payload)
        walk(tree_uri, root)
        return {"path": str(root), "entries": count[0]}

    def export_directory_to_tree(self, tree_uri, source_dir, max_files=10000):
        """Recursively export a private directory into a user-selected SAF tree."""
        source = Path(source_dir)
        if not source.is_dir():
            raise NotADirectoryError(str(source))
        count = [0]

        def ensure_dir(parent_uri, name):
            parent = self._uri(parent_uri)
            if self._DocumentsContract.isTreeUri(parent):
                parent_id = self._current_document_id(parent)
                parent = self._DocumentsContract.buildDocumentUriUsingTree(parent, parent_id)
            created = self._DocumentsContract.createDocument(
                self._resolver(), parent,
                "vnd.android.document/directory", self._safe_name(name)
            )
            if created is None:
                raise OSError("Document provider could not create folder: " + name)
            return str(created.toString())

        remote_root = ensure_dir(tree_uri, source.name)

        def walk(local_dir, remote_uri):
            for child in sorted(local_dir.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower())):
                count[0] += 1
                if count[0] > max_files:
                    raise ValueError("folder contains too many entries")
                if child.is_symlink():
                    continue
                if child.is_dir():
                    walk(child, ensure_dir(remote_uri, child.name))
                elif child.is_file():
                    import mimetypes
                    mime = mimetypes.guess_type(child.name)[0] or "application/octet-stream"
                    self.export_to_tree(remote_uri, child, name=child.name, mime_type=mime)
        walk(source, remote_root)
        return count[0]

    @staticmethod
    def _safe_name(name):
        name = str(name).replace("/", "_").replace("\\", "_").strip().strip(".")
        return name[:180] or "unnamed"

    def delete_document(self, uri_string):
        """Delete a selected document, subject to provider support and user grant."""
        return bool(self._DocumentsContract.deleteDocument(
            self._resolver(), self._uri(uri_string)
        ))

    def _current_document_id(self, uri):
        """Resolve the selected directory itself, including nested tree URIs."""
        try:
            return self._DocumentsContract.getDocumentId(uri)
        except Exception:
            return self._DocumentsContract.getTreeDocumentId(uri)

    def _resolver(self):
        return self._activity.getContentResolver()

    def _uri(self, uri_string):
        if not isinstance(uri_string, str) or not uri_string.startswith("content://"):
            raise ValueError("SAF requires a content:// URI returned by the system picker")
        return self._Uri.parse(uri_string)
