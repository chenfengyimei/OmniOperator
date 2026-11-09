"""Build the packaged input helper using an explicit local Android SDK; never install or contact ADB."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import zipfile
from pathlib import Path


def build(
    sdk: Path,
    output: Path,
    java_home: Path | None = None,
    *,
    keystore: Path | None = None,
    password_file: Path | None = None,
    alias: str = "omni-input-development",
) -> dict:
    source = Path(__file__).resolve().parents[1] / "src/omni/adapters/platform/android_input_helper"
    jar = next(
        (p for p in (sdk / "platforms/android-35/android.jar", sdk / "android-35/android.jar") if p.is_file()), None
    )
    tools = next((p for p in (sdk / "build-tools/35.0.0", sdk / "android-15") if (p / "lib/d8.jar").is_file()), None)
    suffix = ".exe" if os.name == "nt" else ""
    if java_home is None:
        found = shutil.which("javac")
        if not found:
            raise ValueError("Specify a JDK 17+ with --java-home")
        java_home = Path(found).resolve().parent.parent
    if jar is None or tools is None or not (java_home / ("bin/javac" + suffix)).is_file():
        raise ValueError("Android API 35, build-tools 35.0.0 and a JDK are required")
    if bool(keystore) != bool(password_file) or not re.fullmatch(r"[A-Za-z0-9_.-]{1,80}", alias):
        raise ValueError("Signing requires an explicit keystore, password file and alias")
    output.mkdir(parents=True, exist_ok=True)
    final = output / "omni-input-helper.apk"
    if final.exists() or (output / "build.json").exists():
        raise ValueError("Use a new output directory; existing artifacts are not overwritten")

    def run(argv):
        result = subprocess.run([str(a) for a in argv], capture_output=True, timeout=120, check=False)
        if result.returncode:
            # Build output can contain private signing paths. Keep the failure bounded.
            raise RuntimeError(
                "Build tool failed: " + Path(argv[0]).name + "\n" + result.stderr.decode(errors="replace")[-4000:]
            )
        return result.stdout.decode(errors="replace")

    with tempfile.TemporaryDirectory(prefix="omni-input-build-", dir=output) as name:
        work = Path(name)
        classes, dex = work / "classes", work / "dex"
        classes.mkdir()
        dex.mkdir()
        aapt = tools / ("aapt2" + suffix)
        run([aapt, "compile", "--dir", source / "res", "-o", work / "resources.zip"])
        run(
            [
                aapt,
                "link",
                "-I",
                jar,
                "--manifest",
                source / "AndroidManifest.xml",
                "--min-sdk-version",
                "30",
                "--target-sdk-version",
                "35",
                "-o",
                work / "base.apk",
                work / "resources.zip",
            ]
        )
        java = java_home / ("bin/java" + suffix)
        inputs = sorted(source.rglob("*.java"))
        run(
            [
                java_home / ("bin/javac" + suffix),
                "--release",
                "11",
                "-encoding",
                "UTF-8",
                "-classpath",
                jar,
                "-d",
                classes,
                *inputs,
            ]
        )
        run(
            [
                java,
                "-XX:ActiveProcessorCount=4",
                "-Xmx512m",
                "-cp",
                tools / "lib/d8.jar",
                "com.android.tools.r8.D8",
                "--min-api",
                "30",
                "--lib",
                jar,
                "--output",
                dex,
                *sorted(classes.rglob("*.class")),
            ]
        )
        with zipfile.ZipFile(work / "base.apk", "a", compression=zipfile.ZIP_DEFLATED) as apk:
            for item in sorted(dex.glob("*.dex")):
                apk.write(item, item.name)
        run([tools / ("zipalign" + suffix), "-p", "4", work / "base.apk", work / "aligned.apk"])
        certificate = ""
        artifact = work / "aligned.apk"
        if keystore is not None:
            signer = [java, "-jar", tools / "lib/apksigner.jar"]
            artifact = work / "signed.apk"
            run(
                [
                    *signer,
                    "sign",
                    "--ks",
                    keystore,
                    "--ks-key-alias",
                    alias,
                    "--ks-pass",
                    "file:" + str(password_file),
                    "--out",
                    artifact,
                    work / "aligned.apk",
                ]
            )
            verified = run([*signer, "verify", "--verbose", "--print-certs", artifact])
            matches = re.findall(r"certificate SHA-256 digest: ([0-9a-f]{64})", verified)
            if len(matches) != 1:
                raise ValueError("Exactly one verified APK signer is required")
            certificate = matches[0]
        source_hashes = {
            p.relative_to(source).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(source.rglob("*"))
            if p.is_file()
        }
        record = {
            "protocol": 1,
            "package": "org.omnioperator.inputhelper",
            "min_sdk": 30,
            "target_sdk": 35,
            "apk_sha256": hashlib.sha256(artifact.read_bytes()).hexdigest(),
            "certificate_sha256": certificate,
            "signing": "explicit_keystore" if keystore else "unsigned_not_installable",
            "sources": source_hashes,
            "android_jar_sha256": hashlib.sha256(jar.read_bytes()).hexdigest(),
            "device_tested": False,
            "installed": False,
        }
        with final.open("xb") as stream:
            stream.write(artifact.read_bytes())
        (output / "build.json").write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return record


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sdk", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--java-home", type=Path)
    parser.add_argument("--keystore", type=Path)
    parser.add_argument("--password-file", type=Path)
    parser.add_argument("--alias", default="omni-input-development")
    args = parser.parse_args()
    record = build(
        args.sdk,
        args.output,
        args.java_home,
        keystore=args.keystore,
        password_file=args.password_file,
        alias=args.alias,
    )
    print(json.dumps({k: v for k, v in record.items() if k != "sources"}, ensure_ascii=False))


if __name__ == "__main__":
    main()
