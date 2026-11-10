# Git extension

Uses the real `git` resolved through the Toolchain Manager (install: `ndx tool install git`), run through the
ToolRunner/Terminal Bridge in the project's environment. No git library is bundled.

* **Identity** is configured by the user (`user.name`/`user.email`, per repo or global). NDX never invents an identity:
  committing without one fails with an explanation.
* Commands: `ndx git status|init|clone|add|commit|log|diff|conflicts|resolve ...`; URLs are validated (https/ssh/local
  absolute path; no `ext::` or option-like values); arguments are argv arrays.
* **GitHub auth:** token stored by `SecretStore` (file mode 0600; **not encrypted and not hardware-backed**), passed to git
  through a temporary credential helper/env, never in argv, URLs, logs or the audit log (redaction is tested).
* Conflicts: listed and resolved by choosing ours/theirs/edited content; nothing is auto-resolved silently.
* Tested against the real git binary where present and mock executables otherwise (tests/test_git.py). Not tested on Android.
