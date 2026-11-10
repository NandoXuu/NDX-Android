import platform
import sys
from .paths import is_android


def summary():
    return {"android": is_android(), "python": sys.version.split()[0],
            "machine": platform.machine(), "system": platform.system()}
