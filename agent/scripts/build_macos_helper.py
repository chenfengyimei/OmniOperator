"""Build/sign the fixed Swift helper and an isolated fixture on macOS; installation is explicit."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import plistlib
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

from omni.adapters.platform.desktop.macos_helper import BUNDLE_ID, BUNDLE_NAME, EXECUTABLE, bundle_path, resolve, source_fingerprints
from omni.domain.desktop import VERSION

SOURCE = Path(__file__).resolve().parents[1] / "src/omni/adapters/workers/macos"
ROOT = Path(__file__).resolve().parents[2]


def run(argv: list[str]) -> None:
    subprocess.run(argv, check=True, timeout=600, cwd=ROOT)


def info(executable: str, identity: str, protocol: bool) -> dict:
    value = {
        "CFBundleIdentifier": identity,
        "CFBundleName": executable,
        "CFBundleExecutable": executable,
        "CFBundlePackageType": "APPL",
        "CFBundleVersion": "1",
        "CFBundleShortVersionString": "0.1.0",
        "LSMinimumSystemVersion": "14.0",
        "LSUIElement": True,
        "NSHighResolutionCapable": True,
    }
    if protocol:
        value["OmniDesktopProtocol"] = VERSION
    return value


def build(output: Path, architectures: list[str], signing: str) -> dict:
    output.mkdir(parents=True, mode=0o700, exist_ok=False)
    before = source_fingerprints()
    toolchain = {
        "swift": subprocess.check_output(["/usr/bin/xcrun", "swift", "--version"], text=True, timeout=30).strip(),
        "sdk": subprocess.check_output(
            ["/usr/bin/xcrun", "--sdk", "macosx", "--show-sdk-version"], text=True, timeout=30
        ).strip(),
        "system": platform.platform(),
        "python": platform.python_version(),
    }
    # The portable controller tests never load AppKit, open a desktop or post input.
    core_report = output / "foreground-core.xml"
    run(
        [
            "/usr/bin/xcrun",
            "swift",
            "test",
            "--package-path",
            str(SOURCE / "Core"),
            "--scratch-path",
            str(output / "swift-core-tests"),
            "--disable-automatic-resolution",
            "--xunit-output",
            str(core_report),
        ]
    )
    suite = ET.parse(core_report)
    cases = list(suite.iter("testcase"))
    if len(cases) < 9 or any(case.find(tag) is not None for case in cases for tag in ("failure", "error", "skipped")):
        raise RuntimeError("Foreground controller did not pass its complete offline contract")
    # Real local ACL APIs, isolated temporary objects, no GUI/login/root action.
    # Build must fail if these prerequisites fail, rather than signing a helper
    # whose default-path checks silently reject ordinary installations.
    native_report = output / "registration-paths.xml"
    run(
        [
            "/usr/bin/xcrun",
            "swift",
            "test",
            "--package-path",
            str(SOURCE),
            "--scratch-path",
            str(output / "swift-native-tests"),
            "--disable-automatic-resolution",
            "--filter",
            "RegistrationPathTests",
            "--xunit-output",
            str(native_report),
        ]
    )
    native_cases = list(ET.parse(native_report).iter("testcase"))
    if len(native_cases) != 8 or any(
        case.find(tag) is not None for case in native_cases for tag in ("failure", "error", "skipped")
    ):
        raise RuntimeError("Native registration-path proof did not pass all eight cases")
    products: dict[str, list[Path]] = {EXECUTABLE: [], "OmniDesktopFixture": []}
    for architecture in architectures:
        args = [
            "/usr/bin/xcrun",
            "swift",
            "build",
            "--package-path",
            str(SOURCE),
            "--scratch-path",
            str(output / ("swift-" + architecture)),
            "--configuration",
            "release",
            "--triple",
            architecture + "-apple-macosx14.0",
            "--disable-automatic-resolution",
        ]
        run(args)
        directory = Path(subprocess.check_output(args + ["--show-bin-path"], text=True, cwd=ROOT, timeout=60).strip())
        for name in products:
            binary = directory / name
            if not binary.is_file():
                raise RuntimeError("Missing Swift build product: " + name)
            products[name].append(binary)
    for name, binaries in products.items():
        helper = name == EXECUTABLE
        bundle = output / (BUNDLE_NAME if helper else "OmniDesktopFixture.app")
        executable = bundle / "Contents/MacOS" / name
        executable.parent.mkdir(parents=True)
        (bundle / "Contents/Info.plist").write_bytes(
            plistlib.dumps(info(name, BUNDLE_ID if helper else BUNDLE_ID + ".fixture", helper))
        )
        if len(binaries) == 1:
            shutil.copy2(binaries[0], executable)
        else:
            run(["/usr/bin/lipo", "-create", *map(str, binaries), "-output", str(executable)])
        executable.chmod(0o700)
        signing_args = ["/usr/bin/codesign", "--force", "--options", "runtime", "--sign", signing]
        if signing != "-":
            signing_args.append("--timestamp")
        run([*signing_args, str(bundle)])
        run(["/usr/bin/codesign", "--verify", "--strict", str(bundle)])
    if source_fingerprints() != before:
        raise RuntimeError("Swift sources changed during build; discard this build")
    executable = output / BUNDLE_NAME / "Contents/MacOS" / EXECUTABLE
    report = {
        "format": "omni-macos-helper-build-1",
        "protocol": VERSION,
        "bundle_id": BUNDLE_ID,
        "architectures": architectures,
        "toolchain": toolchain,
        "dependency_lock_sha256": hashlib.sha256((ROOT / "uv.lock").read_bytes()).hexdigest()
        if (ROOT / "uv.lock").is_file()
        else None,
        "signing": "ad_hoc" if signing == "-" else "developer_identity",
        "signature_verified": True,
        "foreground_core_tests": {"passed": len(cases), "sha256": hashlib.sha256(core_report.read_bytes()).hexdigest()},
        "registration_path_tests": {
            "passed": len(native_cases),
            "sha256": hashlib.sha256(native_report.read_bytes()).hexdigest(),
        },
        "created_at": datetime.now(timezone.utc).isoformat(),
        "source_files": before,
        "executable_sha256": hashlib.sha256(executable.read_bytes()).hexdigest(),
        "platform_acceptance": "pending",
    }
    (output / (BUNDLE_NAME + ".build.json")).write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return report


def install(output: Path) -> None:
    destination = bundle_path()
    manifest = destination.with_name(BUNDLE_NAME + ".build.json")
    destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    # Never overwrite an installed application or its TCC identity implicitly.
    if destination.exists() or destination.is_symlink() or manifest.exists() or manifest.is_symlink():
        raise RuntimeError("Fixed bundle already exists. Archive it explicitly before installing this build.")
    if destination.parent.is_symlink():
        raise RuntimeError("Fixed installation parent cannot be a link")
    shutil.copytree(output / BUNDLE_NAME, destination)
    shutil.copy2(output / (BUNDLE_NAME + ".build.json"), manifest)
    for path in (destination, *destination.rglob("*"), manifest):
        if path.is_symlink():
            raise RuntimeError("Refuse linked installation")
        path.chmod(0o700 if path.is_dir() or path.name == EXECUTABLE else 0o600)
    run(["/usr/bin/codesign", "--verify", "--strict", str(destination)])
    if resolve() != destination / "Contents/MacOS" / EXECUTABLE:
        raise RuntimeError("Installed bundle did not resolve")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--arch", choices=["universal", "arm64", "x86_64"], default="universal")
    parser.add_argument("--signing-identity", default="-")
    parser.add_argument("--install", action="store_true")
    args = parser.parse_args()
    if sys.platform != "darwin" or os.getuid() == 0:
        parser.error("Build on a logged-in, non-root macOS 14+ machine with Xcode command line tools")
    output = args.output.absolute()
    architectures = ["arm64", "x86_64"] if args.arch == "universal" else [args.arch]
    report = build(output, architectures, args.signing_identity)
    if args.install:
        install(output)
    print(json.dumps({"output": str(output), "installed": args.install, **report}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
