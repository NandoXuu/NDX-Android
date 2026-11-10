"""Android foreground service entry point (p4a `services = ndxkeepalive:service/main.py:foreground:sticky`).

Runs the NDX Supervisor in the SERVICE process: it hosts only external-process servers the UI delegated through
files in the app's private storage and writes a heartbeat the UI reads.  It cannot host built-in static servers or
anything living inside the UI process.  Android may still stop this process: docs/BACKGROUND.md."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))


def _data_dir():
    from ndx.core.paths import Paths
    return Paths().ensure().data


if __name__ == "__main__":
    from ndx.server_manager.supervisor import Supervisor
    Supervisor(_data_dir()).run_forever()
