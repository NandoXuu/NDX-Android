# Background execution and the foreground service

`service/main.py` runs the `Supervisor` (`ndx/server_manager/supervisor.py`) in the Android foreground-service process
(`services = ndxkeepalive:service/main.py:foreground:sticky`). UI and service talk **only through files** in the app's
private `data/service/` (inbox of commands, `state.json`, `heartbeat.json`).

* Only servers already saved in `servers.json` and of external-process type can be delegated; the inbox carries an id,
  never a command line (tested). Built-in static servers live in the UI process and cannot be delegated.
* Delegating is refused when there is no fresh heartbeat (30 s). `ndx service` shows `running`, `stale` or `not running`.
* A foreground service **reduces, but does not remove,** the chance that Android stops NDX or its servers (battery
  optimisation, OEM task killers, swipe-away, memory pressure). NDX never promises persistence.
* Notification text: "NDX is running N background server(s)" / "NDX background service is idle". The notification is
  posted by the p4a foreground service; live text updates from the supervisor are NOT implemented.
* Target API 33. Raising `android.api` to 34+ needs a foreground-service type (e.g. `dataSync` or `specialUse`) and the
  matching `FOREGROUND_SERVICE_*` permission - documented, **not implemented or tested**.
* Tested on desktop with real subprocesses and a fake clock; never run as an Android service.
