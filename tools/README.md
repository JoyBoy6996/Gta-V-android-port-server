# Tools

Optional helper scripts that are **not** part of the backend runtime.

## `build_modified_apk.py`

A legacy helper that patches and re-signs an Android APK with local server
settings (`assets/gwsc.properties` and a hostname string in `libgtav.so`).

- **Out of scope for this repository.** The backend does not build, sign or
  distribute APKs. Use it only on a device/workstation you control.
- It uses absolute Termux paths and an external source APK; edit the paths at
  the top of the script before running.
- It reads `gwsc_local_termux/cert.pem` and `key.pem`, which are gitignored.
- Never commit the resulting APK, certificates or private keys.

Run from the repository root:

```bash
python tools/build_modified_apk.py
```

The rest of the project layout is documented in the
[repository README](../README.md).
