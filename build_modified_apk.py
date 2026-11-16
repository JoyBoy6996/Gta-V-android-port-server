import zipfile
import base64
import hashlib
import subprocess
import os
import shutil
from pathlib import Path

SRC_APK = "/storage/emulated/0/Download/Nagram/thegwchr/game_android_final.zip"
OUT_APK = "/data/user/0/com.termux/files/home/gtaserver/game_android_local_gwsc.apk"
KEY_FILE = "/data/user/0/com.termux/files/home/gtaserver/gwsc_local_termux/key.pem"
CERT_FILE = "/data/user/0/com.termux/files/home/gtaserver/gwsc_local_termux/cert.pem"

print(f"Reading source APK from {SRC_APK}...")

# 1. New gwsc.properties content
NEW_GWSC_PROP = (
    "SERVER_NAME=Gooneral Wheelchair Social Club (Local)\n"
    "SERVER_HOST=127.0.0.1:8443\n"
    "WEB_URL=https://127.0.0.1:8443/\n"
    "SELF_SIGNED_CERT=1\n"
).encode("utf-8")

# 2. Extract and patch libgtav.so
TARGET_STR = b"roscaelifer.thevakhovske.eu.org"
REPLACE_STR = b"127.0.0.1:8443\x00" + b"\x00" * (len(TARGET_STR) - len(b"127.0.0.1:8443\x00"))
assert len(TARGET_STR) == len(REPLACE_STR)

with zipfile.ZipFile(SRC_APK, "r") as zin:
    libgtav_data = zin.read("lib/arm64-v8a/libgtav.so")

pos = libgtav_data.find(TARGET_STR)
if pos == -1:
    print("Warning: TARGET_STR not found in libgtav.so")
    patched_libgtav = libgtav_data
else:
    print(f"Found {TARGET_STR} at offset {hex(pos)} in libgtav.so. Patching to {REPLACE_STR[:15]}...")
    patched_libgtav = libgtav_data[:pos] + REPLACE_STR + libgtav_data[pos+len(TARGET_STR):]

# 3. Build manifest entries
manifest_lines = [
    "Manifest-Version: 1.0",
    "Created-By: 1.0 (Android SignApk)",
    ""
]

files_data = {}

with zipfile.ZipFile(SRC_APK, "r") as zin:
    for item in zin.infolist():
        name = item.filename
        # Skip existing signatures
        if name.startswith("META-INF/") and (name.endswith(".SF") or name.endswith(".RSA") or name.endswith(".DSA") or name.endswith(".MF")):
            continue
        
        if name == "assets/gwsc.properties":
            content = NEW_GWSC_PROP
        elif name == "lib/arm64-v8a/libgtav.so":
            content = patched_libgtav
        else:
            content = zin.read(name)
        
        files_data[name] = content

        digest = base64.b64encode(hashlib.sha256(content).digest()).decode("ascii")
        manifest_lines.append(f"Name: {name}")
        manifest_lines.append(f"SHA-256-Digest: {digest}")
        manifest_lines.append("")

manifest_content = "\r\n".join(manifest_lines).encode("utf-8")

# 4. Build CERT.SF
manifest_sha256 = base64.b64encode(hashlib.sha256(manifest_content).digest()).decode("ascii")
cert_sf_lines = [
    "Signature-Version: 1.0",
    "Created-By: 1.0 (Android SignApk)",
    f"SHA-256-Digest-Manifest: {manifest_sha256}",
    ""
]

for name, content in files_data.items():
    digest = base64.b64encode(hashlib.sha256(content).digest()).decode("ascii")
    cert_sf_lines.append(f"Name: {name}")
    cert_sf_lines.append(f"SHA-256-Digest: {digest}")
    cert_sf_lines.append("")

cert_sf_content = "\r\n".join(cert_sf_lines).encode("utf-8")

# 5. Sign CERT.SF to create CERT.RSA
sf_temp = "/data/user/0/com.termux/files/home/gtaserver/analysis/CERT.SF"
rsa_temp = "/data/user/0/com.termux/files/home/gtaserver/analysis/CERT.RSA"
with open(sf_temp, "wb") as f:
    f.write(cert_sf_content)

cmd = [
    "openssl", "cms", "-sign", "-in", sf_temp, "-binary",
    "-outform", "DER", "-signer", CERT_FILE,
    "-inkey", KEY_FILE, "-out", rsa_temp, "-noattr"
]
subprocess.run(cmd, check=True)

with open(rsa_temp, "rb") as f:
    cert_rsa_content = f.read()

# 6. Write final APK
print(f"Writing modified signed APK to {OUT_APK}...")
with zipfile.ZipFile(OUT_APK, "w", compression=zipfile.ZIP_DEFLATED) as zout:
    # Write META-INF files first
    zout.writestr("META-INF/MANIFEST.MF", manifest_content)
    zout.writestr("META-INF/CERT.SF", cert_sf_content)
    zout.writestr("META-INF/CERT.RSA", cert_rsa_content)

    # Write all game files
    for name, content in files_data.items():
        # Do not compress resources.arsc or uncompressed files if needed, standard deflate is fine
        if name.endswith(".so") or name == "resources.arsc":
            zout.writestr(name, content, compress_type=zipfile.ZIP_STORED)
        else:
            zout.writestr(name, content, compress_type=zipfile.ZIP_DEFLATED)

print(f"Successfully created: {OUT_APK} (Size: {os.path.getsize(OUT_APK)} bytes)")

# Also copy to downloads for easy user access
dest_dir = "/storage/emulated/0/Download/Nagram/thegwchr"
if os.path.exists(dest_dir):
    dest_apk = os.path.join(dest_dir, "game_android_local_gwsc.apk")
    shutil.copy2(OUT_APK, dest_apk)
    print(f"Copied to device storage: {dest_apk}")
