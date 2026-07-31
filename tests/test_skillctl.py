from __future__ import annotations

from contextlib import contextmanager, redirect_stderr, redirect_stdout
import importlib.machinery
import importlib.util
import hashlib
import io
import json
import os
from pathlib import Path
import sys
import tarfile
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest import mock
import zipfile


REPO_ROOT = Path(__file__).resolve().parents[1]
SKILLCTL = REPO_ROOT / "scripts" / "skillctl"
ARCHIVE_EPOCH = 315_532_800
ZIP_EPOCH = (1980, 1, 1, 0, 0, 0)


def load_skillctl() -> object:
    loader = importlib.machinery.SourceFileLoader("skillctl_under_test", str(SKILLCTL))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    if spec is None:
        raise AssertionError(f"cannot load release-control script: {SKILLCTL}")
    module = importlib.util.module_from_spec(spec)
    previous = sys.dont_write_bytecode
    previous_module = sys.modules.get(loader.name)
    had_previous_module = loader.name in sys.modules
    sys.dont_write_bytecode = True
    sys.path.insert(0, str(SKILLCTL.parent))
    sys.modules[loader.name] = module
    try:
        loader.exec_module(module)
    finally:
        if had_previous_module:
            sys.modules[loader.name] = previous_module
        else:
            sys.modules.pop(loader.name, None)
        sys.path.pop(0)
        sys.dont_write_bytecode = previous
    return module


SKILLCTL_MODULE = load_skillctl()


@contextmanager
def process_umask(mask: int):
    previous = os.umask(mask)
    try:
        yield
    finally:
        os.umask(previous)


class SkillctlPackagingTests(unittest.TestCase):
    maxDiff = None

    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory(prefix="skillctl-packaging-")
        self.root = Path(self.tempdir.name)
        self.skill_root = self.root / "skills" / "demo"
        (self.skill_root / "assets").mkdir(parents=True)
        (self.skill_root / "scripts").mkdir()

        self.skill_md = self.skill_root / "SKILL.md"
        self.data_file = self.skill_root / "assets" / "data.txt"
        self.executable = self.skill_root / "scripts" / "run.sh"
        self.skill_md.write_text(
            "---\nname: demo\ndescription: Packaging regression fixture.\n---\n",
            encoding="utf-8",
        )
        self.data_file.write_text("payload\n", encoding="utf-8")
        self.executable.write_text("#!/bin/sh\nprintf 'demo\\n'\n", encoding="utf-8")

        # Deliberately noncanonical source permissions. Packaging should retain
        # only executable intent and emit canonical 0644/0755 member modes.
        self.skill_md.chmod(0o600)
        self.data_file.chmod(0o664)
        self.executable.chmod(0o711)

        self._write_config()
        SKILLCTL_MODULE.ROOT = self.root
        SKILLCTL_MODULE.CONFIG = self.root / "skills.toml"
        SKILLCTL_MODULE.DIST = self.root / "dist"
        SKILLCTL_MODULE.MANIFEST_DIR = self.root / ".release"

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def _write_config(
        self,
        *,
        formats: tuple[str, ...] = ("tar.gz", "zip"),
        allowlist: tuple[str, ...] = ("SKILL.md", "assets/", "scripts/"),
        overlay: str | None = None,
    ) -> None:
        format_values = ", ".join(f'"{value}"' for value in formats)
        allowlist_values = ", ".join(f'"{value}"' for value in allowlist)
        overlay_setting = f'release_overlay = "{overlay}"\n' if overlay else ""
        (self.root / "skills.toml").write_text(
            "[defaults]\n"
            "init_release = false\n"
            f"package_formats = [{format_values}]\n"
            f"package_allowlist = [{allowlist_values}]\n"
            'required_files = ["SKILL.md"]\n'
            "sync_exclude_globs = []\n"
            "forbidden_package_globs = []\n"
            "\n"
            '[skills."demo"]\n'
            'path = "skills/demo"\n'
            'version = "1.2.3"\n'
            'status = "local"\n'
            "public = false\n"
            f"{overlay_setting}",
            encoding="utf-8",
        )

    def _run_pack(self) -> tuple[int, str, str]:
        stdout = io.StringIO()
        stderr = io.StringIO()
        try:
            with redirect_stdout(stdout), redirect_stderr(stderr):
                return_code = SKILLCTL_MODULE.command_pack(SimpleNamespace(skill="demo"))
        except SystemExit as exc:
            return_code = exc.code if isinstance(exc.code, int) else 1
        return int(return_code or 0), stdout.getvalue(), stderr.getvalue()

    def _archive_path(self, format_name: str) -> Path:
        suffix = ".tar.gz" if format_name == "tar.gz" else f".{format_name}"
        return SKILLCTL_MODULE.DIST / f"demo-1.2.3{suffix}"

    def _build_archive(
        self,
        format_name: str,
        *,
        build: str,
        source_mtime: int,
        wall_clock: int,
        umask: int,
    ) -> bytes:
        self._write_config(formats=(format_name,))
        for source in (self.skill_md, self.data_file, self.executable):
            os.utime(source, (source_mtime, source_mtime))

        SKILLCTL_MODULE.DIST = self.root / f"dist-{build}"
        SKILLCTL_MODULE.MANIFEST_DIR = self.root / f"release-{build}"
        with mock.patch("time.time", return_value=wall_clock), process_umask(umask):
            return_code, stdout, stderr = self._run_pack()
        self.assertEqual(
            return_code,
            0,
            f"fixture package failed unexpectedly\nstdout:\n{stdout}\nstderr:\n{stderr}",
        )
        return self._archive_path(format_name).read_bytes()

    def _single_archive(self, format_name: str) -> bytes:
        return self._build_archive(
            format_name,
            build=f"metadata-{format_name.replace('.', '-')}",
            source_mtime=1_733_184_123,
            wall_clock=1_900_000_123,
            umask=0o027,
        )

    def test_tar_gz_bytes_are_reproducible_across_mtime_clock_and_umask(self) -> None:
        first = self._build_archive(
            "tar.gz",
            build="tar-a",
            source_mtime=1_700_000_001,
            wall_clock=1_800_000_001,
            umask=0o077,
        )
        second = self._build_archive(
            "tar.gz",
            build="tar-b",
            source_mtime=1_750_000_009,
            wall_clock=1_900_000_009,
            umask=0o002,
        )

        self.assertEqual(first, second)

    def test_zip_bytes_are_reproducible_across_mtime_clock_and_umask(self) -> None:
        first = self._build_archive(
            "zip",
            build="zip-a",
            source_mtime=1_700_000_001,
            wall_clock=1_800_000_001,
            umask=0o077,
        )
        second = self._build_archive(
            "zip",
            build="zip-b",
            source_mtime=1_750_000_009,
            wall_clock=1_900_000_009,
            umask=0o002,
        )

        self.assertEqual(first, second)

    def test_gzip_header_uses_1980_01_01_utc_timestamp(self) -> None:
        archive = self._single_archive("tar.gz")

        self.assertEqual(archive[:2], b"\x1f\x8b")
        self.assertEqual(int.from_bytes(archive[4:8], "little"), ARCHIVE_EPOCH)

    def test_tar_archive_includes_explicit_directory_entries(self) -> None:
        archive = self._single_archive("tar.gz")

        with tarfile.open(fileobj=io.BytesIO(archive), mode="r:gz") as tar:
            members = tar.getmembers()
        self.assertEqual(
            {member.name for member in members if member.isdir()},
            {"demo", "demo/assets", "demo/scripts"},
        )
        self.assertEqual(
            {member.name for member in members if member.isfile()},
            {"demo/SKILL.md", "demo/assets/data.txt", "demo/scripts/run.sh"},
        )

    def test_zip_archive_includes_explicit_directory_entries(self) -> None:
        archive = self._single_archive("zip")

        with zipfile.ZipFile(io.BytesIO(archive), mode="r") as zipf:
            members = zipf.infolist()
        self.assertEqual(
            {member.filename for member in members if member.is_dir()},
            {"demo/", "demo/assets/", "demo/scripts/"},
        )
        self.assertEqual(
            {member.filename for member in members if not member.is_dir()},
            {"demo/SKILL.md", "demo/assets/data.txt", "demo/scripts/run.sh"},
        )

    def test_tar_file_payloads_match_fixture_source_bytes(self) -> None:
        archive = self._single_archive("tar.gz")
        expected = {
            "demo/SKILL.md": self.skill_md.read_bytes(),
            "demo/assets/data.txt": self.data_file.read_bytes(),
            "demo/scripts/run.sh": self.executable.read_bytes(),
        }

        with tarfile.open(fileobj=io.BytesIO(archive), mode="r:gz") as tar:
            payloads: dict[str, bytes] = {}
            for member in tar.getmembers():
                if not member.isfile():
                    continue
                extracted = tar.extractfile(member)
                self.assertIsNotNone(extracted, member.name)
                assert extracted is not None
                payloads[member.name] = extracted.read()

        self.assertEqual(payloads, expected)

    def test_zip_file_payloads_match_fixture_source_bytes(self) -> None:
        archive = self._single_archive("zip")
        expected = {
            "demo/SKILL.md": self.skill_md.read_bytes(),
            "demo/assets/data.txt": self.data_file.read_bytes(),
            "demo/scripts/run.sh": self.executable.read_bytes(),
        }

        with zipfile.ZipFile(io.BytesIO(archive), mode="r") as zipf:
            payloads = {
                member.filename: zipf.read(member)
                for member in zipf.infolist()
                if not member.is_dir()
            }

        self.assertEqual(payloads, expected)

    def test_tar_members_have_normalized_owner_and_timestamps(self) -> None:
        archive = self._single_archive("tar.gz")

        with tarfile.open(fileobj=io.BytesIO(archive), mode="r:gz") as tar:
            members = tar.getmembers()
        for member in members:
            with self.subTest(member=member.name):
                self.assertEqual(
                    (member.mtime, member.uid, member.gid, member.uname, member.gname),
                    (ARCHIVE_EPOCH, 0, 0, "", ""),
                )

    def test_zip_members_have_normalized_timestamps_and_platform(self) -> None:
        archive = self._single_archive("zip")

        with zipfile.ZipFile(io.BytesIO(archive), mode="r") as zipf:
            members = zipf.infolist()
        for member in members:
            with self.subTest(member=member.filename):
                self.assertEqual(member.date_time, ZIP_EPOCH)
                self.assertEqual(member.create_system, 3)
                self.assertEqual(member.extra, b"")
                self.assertEqual(member.comment, b"")

    def test_tar_member_modes_are_canonical_and_preserve_executable_intent(self) -> None:
        archive = self._single_archive("tar.gz")

        with tarfile.open(fileobj=io.BytesIO(archive), mode="r:gz") as tar:
            modes = {member.name: member.mode & 0o777 for member in tar.getmembers()}
        self.assertEqual(
            modes,
            {
                "demo": 0o755,
                "demo/SKILL.md": 0o644,
                "demo/assets": 0o755,
                "demo/assets/data.txt": 0o644,
                "demo/scripts": 0o755,
                "demo/scripts/run.sh": 0o755,
            },
        )

    def test_zip_member_modes_are_canonical_and_preserve_executable_intent(self) -> None:
        archive = self._single_archive("zip")

        with zipfile.ZipFile(io.BytesIO(archive), mode="r") as zipf:
            modes = {
                member.filename: (member.external_attr >> 16) & 0o777
                for member in zipf.infolist()
            }
        self.assertEqual(
            modes,
            {
                "demo/": 0o755,
                "demo/SKILL.md": 0o644,
                "demo/assets/": 0o755,
                "demo/assets/data.txt": 0o644,
                "demo/scripts/": 0o755,
                "demo/scripts/run.sh": 0o755,
            },
        )

    def _assert_rejected_without_outputs(self, format_name: str) -> None:
        return_code, _stdout, _stderr = self._run_pack()
        archive = self._archive_path(format_name)
        manifest = SKILLCTL_MODULE.MANIFEST_DIR / "demo-1.2.3.json"
        with self.subTest(check="nonzero return code"):
            self.assertNotEqual(return_code, 0)
        with self.subTest(check="no archive"):
            self.assertFalse(archive.exists())
        with self.subTest(check="no manifest"):
            self.assertFalse(manifest.exists())

    def _assert_allowlisted_symlink_is_rejected(self, format_name: str) -> None:
        target = self.skill_root / "target.txt"
        target.write_text("must not be packaged through a link\n", encoding="utf-8")
        (self.skill_root / "linked.txt").symlink_to("target.txt")
        self._write_config(formats=(format_name,), allowlist=("SKILL.md", "linked.txt"))
        self._assert_rejected_without_outputs(format_name)

    def _assert_release_overlay_symlink_is_rejected(self, format_name: str) -> None:
        overlay = self.root / "release-overlay"
        overlay.mkdir()
        (overlay / "linked.txt").symlink_to(Path("..") / "skills" / "demo" / "SKILL.md")
        self._write_config(
            formats=(format_name,),
            allowlist=("SKILL.md",),
            overlay="release-overlay",
        )
        self._assert_rejected_without_outputs(format_name)

    def test_allowlisted_symlink_is_rejected_from_tar_gz(self) -> None:
        self._assert_allowlisted_symlink_is_rejected("tar.gz")

    def test_allowlisted_symlink_is_rejected_from_zip(self) -> None:
        self._assert_allowlisted_symlink_is_rejected("zip")

    def test_release_overlay_symlink_is_rejected_from_tar_gz(self) -> None:
        self._assert_release_overlay_symlink_is_rejected("tar.gz")

    def test_release_overlay_symlink_is_rejected_from_zip(self) -> None:
        self._assert_release_overlay_symlink_is_rejected("zip")

    def test_source_leaf_replacement_during_snapshot_is_rejected(self) -> None:
        original = self.data_file.stat()
        real_read = SKILLCTL_MODULE.os.read
        injected = False

        def replace_leaf_after_read(descriptor: int, size: int) -> bytes:
            nonlocal injected
            data = real_read(descriptor, size)
            opened = SKILLCTL_MODULE.os.fstat(descriptor)
            if (
                data
                and not injected
                and (opened.st_dev, opened.st_ino) == (original.st_dev, original.st_ino)
            ):
                replacement = self.data_file.with_name("replacement.txt")
                replacement.write_text("foreign replacement payload\n", encoding="utf-8")
                os.replace(replacement, self.data_file)
                injected = True
            return data

        with mock.patch.object(
            SKILLCTL_MODULE.os,
            "read",
            side_effect=replace_leaf_after_read,
        ):
            return_code, _stdout, _stderr = self._run_pack()

        self.assertTrue(injected, "fault injection did not replace the source leaf")
        self.assertNotEqual(return_code, 0)
        self.assertFalse(self._archive_path("tar.gz").exists())
        self.assertFalse(self._archive_path("zip").exists())
        self.assertFalse(
            (SKILLCTL_MODULE.MANIFEST_DIR / "demo-1.2.3.json").exists()
        )

    def test_detached_skill_root_after_snapshot_read_is_rejected(self) -> None:
        moved = self.root / "moved-skill-root"
        real_read_locator = SKILLCTL_MODULE._read_locator
        injected = False

        def read_then_detach(*args: object, **kwargs: object) -> object:
            nonlocal injected
            result = real_read_locator(*args, **kwargs)
            if not injected:
                self.skill_root.rename(moved)
                self.skill_root.mkdir(parents=True)
                (self.skill_root / "SKILL.md").write_text(
                    "---\nname: foreign\ndescription: replacement\n---\n",
                    encoding="utf-8",
                )
                injected = True
            return result

        with mock.patch.object(
            SKILLCTL_MODULE,
            "_read_locator",
            side_effect=read_then_detach,
        ):
            return_code, _stdout, _stderr = self._run_pack()

        self.assertTrue(injected, "fault injection did not detach the skill root")
        self.assertNotEqual(return_code, 0)
        self.assertFalse(self._archive_path("tar.gz").exists())
        self.assertFalse(self._archive_path("zip").exists())
        self.assertFalse(
            (SKILLCTL_MODULE.MANIFEST_DIR / "demo-1.2.3.json").exists()
        )

    def _assert_symlinked_output_root_is_not_followed(self, output_name: str) -> None:
        with tempfile.TemporaryDirectory(prefix=f"skillctl-{output_name}-outside-") as outside_raw:
            outside = Path(outside_raw)
            sentinel = outside / "sentinel.txt"
            sentinel.write_text("outside sentinel\n", encoding="utf-8")
            output = self.root / output_name
            output.symlink_to(outside, target_is_directory=True)
            if output_name == "dist":
                SKILLCTL_MODULE.DIST = output
            else:
                SKILLCTL_MODULE.MANIFEST_DIR = output
            self._write_config(formats=("tar.gz",))
            real_open = SKILLCTL_MODULE.os.open
            outside_opens: list[str] = []

            def probe_open(path: object, *args: object, **kwargs: object) -> int:
                if isinstance(path, (str, bytes, os.PathLike)):
                    resolved = Path(path).resolve(strict=False)
                    try:
                        resolved.relative_to(outside)
                    except ValueError:
                        pass
                    else:
                        outside_opens.append(str(resolved.relative_to(outside)))
                return real_open(path, *args, **kwargs)

            with mock.patch.object(SKILLCTL_MODULE.os, "open", side_effect=probe_open):
                return_code, _stdout, _stderr = self._run_pack()

            self.assertNotEqual(return_code, 0)
            self.assertEqual(outside_opens, [])
            self.assertEqual(
                {path.relative_to(outside).as_posix(): path.read_bytes() for path in outside.rglob("*")},
                {"sentinel.txt": b"outside sentinel\n"},
            )

    def test_symlinked_dist_root_is_not_followed(self) -> None:
        self._assert_symlinked_output_root_is_not_followed("dist")

    def test_symlinked_manifest_root_is_not_followed(self) -> None:
        self._assert_symlinked_output_root_is_not_followed(".release")

    @unittest.skipUnless(hasattr(os, "mkfifo"), "FIFO creation requires POSIX")
    def test_special_dist_root_is_rejected(self) -> None:
        os.mkfifo(self.root / "dist")
        self._write_config(formats=("tar.gz",))

        return_code, _stdout, _stderr = self._run_pack()

        self.assertNotEqual(return_code, 0)
        self.assertFalse((SKILLCTL_MODULE.MANIFEST_DIR / "demo-1.2.3.json").exists())

    @unittest.skipUnless(hasattr(os, "mkfifo"), "FIFO creation requires POSIX")
    def test_special_manifest_root_is_rejected(self) -> None:
        os.mkfifo(self.root / ".release")
        self._write_config(formats=("zip",))

        return_code, _stdout, _stderr = self._run_pack()

        self.assertNotEqual(return_code, 0)
        self.assertFalse((SKILLCTL_MODULE.DIST / "demo-1.2.3.zip").exists())

    def test_detached_dist_before_staging_fails_and_cleans_transaction_files(self) -> None:
        original_build = SKILLCTL_MODULE.build_archives
        detached = False
        with tempfile.TemporaryDirectory(prefix="skillctl-detached-dist-") as outside_raw:
            moved_dist = Path(outside_raw) / "moved-dist"

            def detach_after_memory_build(
                skill: dict[str, object],
                snapshot: dict[str, object],
                output_dir: object = None,
            ) -> object:
                nonlocal detached
                result = original_build(skill, snapshot, output_dir=output_dir)
                os.rename(SKILLCTL_MODULE.DIST, moved_dist)
                SKILLCTL_MODULE.DIST.mkdir()
                detached = True
                return result

            with mock.patch.object(
                SKILLCTL_MODULE,
                "build_archives",
                side_effect=detach_after_memory_build,
            ):
                return_code, _stdout, _stderr = self._run_pack()

            self.assertTrue(detached)
            self.assertNotEqual(return_code, 0)
            self.assertEqual(list(moved_dist.iterdir()), [])
            self.assertEqual(list(SKILLCTL_MODULE.DIST.iterdir()), [])
            self.assertFalse(
                (SKILLCTL_MODULE.MANIFEST_DIR / "demo-1.2.3.json").exists()
            )

    def test_detached_manifest_during_publication_rolls_back_transaction_outputs(self) -> None:
        real_replace = SKILLCTL_MODULE._replace_file
        detached = False
        with tempfile.TemporaryDirectory(prefix="skillctl-detached-manifest-") as outside_raw:
            moved_manifest = Path(outside_raw) / "moved-release"

            def detach_after_manifest_replace(source: object, destination: object) -> None:
                nonlocal detached
                real_replace(source, destination)
                if (
                    not detached
                    and destination.name == "demo-1.2.3.json"
                    and source.entry_name != source.name
                ):
                    os.rename(SKILLCTL_MODULE.MANIFEST_DIR, moved_manifest)
                    SKILLCTL_MODULE.MANIFEST_DIR.mkdir()
                    detached = True

            with mock.patch.object(
                SKILLCTL_MODULE,
                "_replace_file",
                side_effect=detach_after_manifest_replace,
            ):
                return_code, _stdout, _stderr = self._run_pack()

            self.assertTrue(detached)
            self.assertNotEqual(return_code, 0)
            self.assertFalse(self._archive_path("tar.gz").exists())
            self.assertFalse(self._archive_path("zip").exists())
            self.assertEqual(
                {path.name for path in moved_manifest.iterdir()},
                {".demo.pack.lock"},
            )
            self.assertEqual(list(SKILLCTL_MODULE.MANIFEST_DIR.iterdir()), [])

    def test_overlay_directory_cannot_silently_replace_base_file(self) -> None:
        (self.skill_root / "clash").write_text("base file\n", encoding="utf-8")
        overlay = self.root / "release-overlay"
        (overlay / "clash").mkdir(parents=True)
        (overlay / "clash" / "child.txt").write_text("overlay child\n", encoding="utf-8")
        self._write_config(
            formats=("tar.gz",),
            allowlist=("SKILL.md", "clash"),
            overlay="release-overlay",
        )

        self._assert_rejected_without_outputs("tar.gz")

    def test_overlay_file_cannot_silently_replace_base_directory(self) -> None:
        (self.skill_root / "clash").mkdir()
        (self.skill_root / "clash" / "child.txt").write_text("base child\n", encoding="utf-8")
        overlay = self.root / "release-overlay"
        overlay.mkdir()
        (overlay / "clash").write_text("overlay file\n", encoding="utf-8")
        self._write_config(
            formats=("zip",),
            allowlist=("SKILL.md", "clash/"),
            overlay="release-overlay",
        )

        self._assert_rejected_without_outputs("zip")

    def test_missing_nested_allowlist_parent_is_ignored(self) -> None:
        self._write_config(
            formats=("tar.gz",),
            allowlist=("SKILL.md", "missing/parent/child.txt"),
        )

        return_code, stdout, stderr = self._run_pack()

        self.assertEqual(return_code, 0, f"stdout:\n{stdout}\nstderr:\n{stderr}")
        with tarfile.open(self._archive_path("tar.gz"), "r:gz") as archive:
            self.assertEqual(
                {member.name for member in archive.getmembers() if member.isfile()},
                {"demo/SKILL.md"},
            )

    def test_nested_allowlist_symlink_parent_is_rejected(self) -> None:
        unsafe = self.skill_root / "unsafe"
        unsafe.mkdir()
        (unsafe / "linked-parent").symlink_to(".")
        self._write_config(
            formats=("tar.gz",),
            allowlist=("SKILL.md", "unsafe/linked-parent/child.txt"),
        )

        self._assert_rejected_without_outputs("tar.gz")

    @unittest.skipUnless(hasattr(os, "mkfifo"), "FIFO creation requires POSIX")
    def test_nested_allowlist_special_parent_is_rejected(self) -> None:
        unsafe = self.skill_root / "unsafe"
        unsafe.mkdir()
        os.mkfifo(unsafe / "fifo-parent")
        self._write_config(
            formats=("zip",),
            allowlist=("SKILL.md", "unsafe/fifo-parent/child.txt"),
        )

        self._assert_rejected_without_outputs("zip")

    def test_keyboard_interrupt_after_backup_move_restores_existing_outputs(self) -> None:
        return_code, stdout, stderr = self._run_pack()
        self.assertEqual(return_code, 0, f"stdout:\n{stdout}\nstderr:\n{stderr}")
        artifacts = (
            self._archive_path("tar.gz"),
            self._archive_path("zip"),
            SKILLCTL_MODULE.MANIFEST_DIR / "demo-1.2.3.json",
        )
        before = {path: path.read_bytes() for path in artifacts}
        self.data_file.write_text("replacement payload\n", encoding="utf-8")

        real_replace = SKILLCTL_MODULE._replace_file
        interrupted = False

        def interrupt_after_move(source: object, destination: object) -> None:
            nonlocal interrupted
            real_replace(source, destination)
            if (
                not interrupted
                and source.entry_name == self._archive_path("tar.gz").name
                and destination.entry_name.endswith(".previous")
            ):
                interrupted = True
                raise KeyboardInterrupt("injected after old archive moved to backup")

        with mock.patch.object(
            SKILLCTL_MODULE,
            "_replace_file",
            side_effect=interrupt_after_move,
        ):
            with self.assertRaises(KeyboardInterrupt):
                self._run_pack()

        self.assertTrue(interrupted)
        self.assertEqual({path: path.read_bytes() for path in artifacts}, before)
        self.assertEqual(list(SKILLCTL_MODULE.DIST.glob("*.previous")), [])
        self.assertEqual(list(SKILLCTL_MODULE.MANIFEST_DIR.glob("*.previous")), [])

    def test_staging_archive_tamper_after_verification_preserves_existing_outputs(self) -> None:
        return_code, stdout, stderr = self._run_pack()
        self.assertEqual(return_code, 0, f"stdout:\n{stdout}\nstderr:\n{stderr}")
        artifacts = (
            self._archive_path("tar.gz"),
            self._archive_path("zip"),
            SKILLCTL_MODULE.MANIFEST_DIR / "demo-1.2.3.json",
        )
        before = {path: path.read_bytes() for path in artifacts}
        self.data_file.write_text("verified replacement payload\n", encoding="utf-8")
        original_publish = SKILLCTL_MODULE._publish_targets
        tampered = False

        def tamper_before_publish(targets: object, commit_check: object = None) -> None:
            nonlocal tampered
            materialized = list(targets)
            tar_source = next(
                target[0]
                for target in materialized
                if target[0].name.endswith(".tar.gz")
            )
            SKILLCTL_MODULE._write_output_bytes(
                tar_source,
                b"post-verification staging corruption\n",
            )
            tampered = True
            original_publish(materialized, commit_check=commit_check)

        with mock.patch.object(
            SKILLCTL_MODULE,
            "_publish_targets",
            side_effect=tamper_before_publish,
        ):
            return_code, _stdout, _stderr = self._run_pack()

        self.assertTrue(tampered)
        self.assertNotEqual(return_code, 0)
        self.assertEqual({path: path.read_bytes() for path in artifacts}, before)
        self.assertEqual(list(SKILLCTL_MODULE.DIST.glob("*.previous")), [])
        self.assertEqual(list(SKILLCTL_MODULE.MANIFEST_DIR.glob("*.previous")), [])

    def test_staging_inode_swap_inside_replace_restores_existing_outputs(self) -> None:
        return_code, stdout, stderr = self._run_pack()
        self.assertEqual(return_code, 0, f"stdout:\n{stdout}\nstderr:\n{stderr}")
        artifacts = (
            self._archive_path("tar.gz"),
            self._archive_path("zip"),
            SKILLCTL_MODULE.MANIFEST_DIR / "demo-1.2.3.json",
        )
        before = {path: path.read_bytes() for path in artifacts}
        self.data_file.write_text("inode-race replacement payload\n", encoding="utf-8")
        tar_destination = self._archive_path("tar.gz")
        real_replace = SKILLCTL_MODULE._replace_file
        injected = False

        def swap_source_inside_replace(source: object, destination: object) -> None:
            nonlocal injected
            if (
                not injected
                and destination.entry_name == tar_destination.name
                and source.name == tar_destination.name
                and source.entry_name != source.name
            ):
                attacker = SKILLCTL_MODULE._write_anchored_temp(
                    source.directory,
                    source.name,
                    b"attacker-controlled archive bytes\n",
                )
                real_replace(attacker, source)
                injected = True
            real_replace(source, destination)

        with mock.patch.object(
            SKILLCTL_MODULE,
            "_replace_file",
            side_effect=swap_source_inside_replace,
        ):
            return_code, _stdout, _stderr = self._run_pack()

        self.assertTrue(injected)
        self.assertNotEqual(return_code, 0)
        self.assertEqual({path: path.read_bytes() for path in artifacts}, before)
        self.assertEqual(list(SKILLCTL_MODULE.DIST.glob("*.previous")), [])
        self.assertEqual(list(SKILLCTL_MODULE.MANIFEST_DIR.glob("*.previous")), [])

    def test_failed_new_publish_preserves_foreign_destination(self) -> None:
        tar_destination = self._archive_path("tar.gz")
        foreign_bytes = b"foreign destination created during rename\n"
        real_replace = SKILLCTL_MODULE._replace_file
        injected = False

        def create_foreign_then_fail(source: object, destination: object) -> None:
            nonlocal injected
            if (
                not injected
                and destination.entry_name == tar_destination.name
                and source.name == tar_destination.name
                and source.entry_name != source.name
            ):
                SKILLCTL_MODULE._write_output_bytes(
                    destination,
                    foreign_bytes,
                    create=True,
                )
                injected = True
                raise OSError("injected before staged rename")
            real_replace(source, destination)

        with mock.patch.object(
            SKILLCTL_MODULE,
            "_replace_file",
            side_effect=create_foreign_then_fail,
        ):
            return_code, _stdout, stderr = self._run_pack()

        self.assertTrue(injected)
        self.assertNotEqual(return_code, 0)
        self.assertIn("transaction_conflict", stderr)
        self.assertEqual(tar_destination.read_bytes(), foreign_bytes)
        self.assertFalse(self._archive_path("zip").exists())
        self.assertFalse((SKILLCTL_MODULE.MANIFEST_DIR / "demo-1.2.3.json").exists())

    def test_failed_existing_publish_preserves_foreign_destination_and_old_backup(self) -> None:
        return_code, stdout, stderr = self._run_pack()
        self.assertEqual(return_code, 0, f"stdout:\n{stdout}\nstderr:\n{stderr}")
        tar_destination = self._archive_path("tar.gz")
        zip_destination = self._archive_path("zip")
        manifest = SKILLCTL_MODULE.MANIFEST_DIR / "demo-1.2.3.json"
        old_tar = tar_destination.read_bytes()
        old_zip = zip_destination.read_bytes()
        old_manifest = manifest.read_bytes()
        foreign_bytes = b"foreign destination racing existing publish\n"
        self.data_file.write_text("existing race replacement\n", encoding="utf-8")
        real_replace = SKILLCTL_MODULE._replace_file
        injected = False

        def create_foreign_then_fail(source: object, destination: object) -> None:
            nonlocal injected
            if (
                not injected
                and destination.entry_name == tar_destination.name
                and source.name == tar_destination.name
                and source.entry_name != source.name
            ):
                SKILLCTL_MODULE._write_output_bytes(
                    destination,
                    foreign_bytes,
                    create=True,
                )
                injected = True
                raise OSError("injected before staged rename")
            real_replace(source, destination)

        with mock.patch.object(
            SKILLCTL_MODULE,
            "_replace_file",
            side_effect=create_foreign_then_fail,
        ):
            return_code, _stdout, stderr = self._run_pack()

        self.assertTrue(injected)
        self.assertNotEqual(return_code, 0)
        self.assertIn("transaction_conflict", stderr)
        self.assertEqual(tar_destination.read_bytes(), foreign_bytes)
        self.assertEqual(zip_destination.read_bytes(), old_zip)
        self.assertEqual(manifest.read_bytes(), old_manifest)
        backups = list(SKILLCTL_MODULE.DIST.glob(".*.previous"))
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_bytes(), old_tar)

    def test_final_cleanup_preserves_reused_foreign_staging_name(self) -> None:
        real_replace = SKILLCTL_MODULE._replace_file
        foreign = b"foreign owner reused staging name\n"
        reused: list[Path] = []

        def replace_then_reuse_staging(source: object, destination: object) -> None:
            real_replace(source, destination)
            if (
                not reused
                and isinstance(source, SKILLCTL_MODULE.AnchoredFile)
                and destination.name.endswith(".tar.gz")
            ):
                path = SKILLCTL_MODULE.DIST / source.entry_name
                descriptor = os.open(
                    source.entry_name,
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                    0o600,
                    dir_fd=source.directory.descriptor,
                )
                try:
                    os.write(descriptor, foreign)
                    os.fsync(descriptor)
                finally:
                    os.close(descriptor)
                reused.append(path)

        with mock.patch.object(
            SKILLCTL_MODULE,
            "_replace_file",
            side_effect=replace_then_reuse_staging,
        ):
            return_code, _stdout, stderr = self._run_pack()

        self.assertTrue(reused, "fault injection did not reuse a staging name")
        self.assertNotEqual(return_code, 0)
        self.assertIn("staging", stderr)
        self.assertEqual(reused[0].read_bytes(), foreign)

    def test_final_cleanup_preserves_replaced_foreign_backup_name(self) -> None:
        first_code, first_stdout, first_stderr = self._run_pack()
        self.assertEqual(first_code, 0, f"stdout:\n{first_stdout}\nstderr:\n{first_stderr}")
        self.data_file.write_text("new package payload\n", encoding="utf-8")
        real_assert_attached = SKILLCTL_MODULE._assert_output_roots_attached
        foreign = b"foreign owner replaced backup name\n"
        replaced: list[Path] = []

        def replace_backup_at_commit(output_roots: object) -> None:
            real_assert_attached(output_roots)
            if replaced:
                return
            backups = sorted(SKILLCTL_MODULE.DIST.glob(".*.previous"))
            if backups:
                backup = backups[0]
                backup.unlink()
                backup.write_bytes(foreign)
                replaced.append(backup)

        with mock.patch.object(
            SKILLCTL_MODULE,
            "_assert_output_roots_attached",
            side_effect=replace_backup_at_commit,
        ):
            return_code, _stdout, stderr = self._run_pack()

        self.assertTrue(replaced, "fault injection did not reach backup cleanup")
        self.assertNotEqual(return_code, 0)
        self.assertIn("backup", stderr)
        self.assertEqual(replaced[0].read_bytes(), foreign)

    def test_concurrent_packs_serialize_and_publish_one_consistent_snapshot(self) -> None:
        original_build = SKILLCTL_MODULE.build_archives
        first_inside = threading.Event()
        second_inside = threading.Event()
        release_first = threading.Event()
        state_lock = threading.Lock()
        state = {"active": 0, "max_active": 0}
        snapshots: list[bytes] = []
        results: list[int] = []
        failures: list[BaseException] = []

        def controlled_build(
            skill: dict[str, object],
            snapshot: dict[str, object],
            output_dir: object = None,
        ) -> object:
            payload = snapshot["assets/data.txt"].data
            with state_lock:
                snapshots.append(payload)
                state["active"] += 1
                state["max_active"] = max(state["max_active"], state["active"])
            if payload == b"payload\n":
                first_inside.set()
                if not release_first.wait(timeout=5):
                    raise AssertionError("timed out waiting to release first pack")
            else:
                second_inside.set()
            try:
                return original_build(skill, snapshot, output_dir=output_dir)
            finally:
                with state_lock:
                    state["active"] -= 1

        def run_pack() -> None:
            try:
                results.append(SKILLCTL_MODULE.command_pack(SimpleNamespace(skill="demo")))
            except BaseException as exc:  # pragma: no cover - asserted below
                failures.append(exc)

        with (
            mock.patch.object(SKILLCTL_MODULE, "build_archives", side_effect=controlled_build),
            mock.patch("builtins.print"),
        ):
            first = threading.Thread(target=run_pack)
            first.start()
            self.assertTrue(first_inside.wait(timeout=5), "first pack did not reach archive build")
            self.data_file.write_text("concurrent snapshot\n", encoding="utf-8")
            second = threading.Thread(target=run_pack)
            second.start()
            overlapped = second_inside.wait(timeout=0.25)
            release_first.set()
            first.join(timeout=10)
            second.join(timeout=10)

        self.assertFalse(first.is_alive() or second.is_alive(), "concurrent packs did not finish")
        self.assertEqual(failures, [])
        self.assertEqual(results, [0, 0])
        self.assertFalse(overlapped, "second pack entered archive build before the first released")
        self.assertEqual(state["max_active"], 1)
        self.assertEqual(snapshots, [b"payload\n", b"concurrent snapshot\n"])

        manifest_path = SKILLCTL_MODULE.MANIFEST_DIR / "demo-1.2.3.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        for entry in manifest["archives"]:
            archive_path = SKILLCTL_MODULE.DIST / entry["name"]
            self.assertEqual(hashlib.sha256(archive_path.read_bytes()).hexdigest(), entry["sha256"])
        self.assertEqual(manifest["sha256"], manifest["archives"][0]["sha256"])
        with tarfile.open(self._archive_path("tar.gz"), "r:gz") as archive:
            extracted = archive.extractfile("demo/assets/data.txt")
            self.assertIsNotNone(extracted)
            assert extracted is not None
            self.assertEqual(extracted.read(), b"concurrent snapshot\n")
        with zipfile.ZipFile(self._archive_path("zip"), "r") as archive:
            self.assertEqual(archive.read("demo/assets/data.txt"), b"concurrent snapshot\n")


if __name__ == "__main__":
    unittest.main()
