"""Tests for file-path safety utilities (app/utils/paths.py)."""

from __future__ import annotations

import os
import tempfile

import pytest

from app.core.exceptions import FileSafetyError
from app.utils.paths import (
    contains_traversal,
    is_absolute_path,
    is_path_safe,
    is_within_directory,
    normalize_path,
    resolve_project_path,
    restrict_to_directory,
    safe_mkdir,
    validate_extension,
    validate_path_safety,
)


class TestContainsTraversal:
    def test_unix_traversal(self) -> None:
        assert contains_traversal("../../secret.txt") is True

    def test_windows_traversal(self) -> None:
        assert contains_traversal("..\\..\\secret.txt") is True

    def test_mid_path_traversal(self) -> None:
        assert contains_traversal("assets/../../secret.png") is True

    def test_safe_path(self) -> None:
        assert contains_traversal("assets/photo.png") is False

    def test_empty_path(self) -> None:
        assert contains_traversal("") is False

    def test_dot_only_not_traversal(self) -> None:
        # A single "." is not a traversal, but ".." is.
        assert contains_traversal("./file.png") is False
        assert contains_traversal("../file.png") is True


class TestIsPathSafe:
    def test_safe_relative_path(self) -> None:
        assert is_path_safe("assets/photo.png") is True

    def test_traversal_rejected(self) -> None:
        assert is_path_safe("../../secret.txt") is False

    def test_absolute_rejected_by_default(self) -> None:
        assert is_path_safe("/etc/passwd") is False

    def test_absolute_allowed_with_flag(self) -> None:
        assert is_path_safe("/home/user/file.png", allow_absolute=True) is True

    def test_empty_rejected(self) -> None:
        assert is_path_safe("") is False

    def test_control_chars_rejected(self) -> None:
        assert is_path_safe("file\x00.png") is False


class TestValidatePathSafety:
    def test_safe_path_returns_path(self) -> None:
        assert validate_path_safety("assets/photo.png") == "assets/photo.png"

    def test_empty_raises(self) -> None:
        with pytest.raises(FileSafetyError):
            validate_path_safety("")

    def test_traversal_raises(self) -> None:
        with pytest.raises(FileSafetyError):
            validate_path_safety("../../secret.txt")

    def test_absolute_raises(self) -> None:
        with pytest.raises(FileSafetyError):
            validate_path_safety("/etc/passwd")

    def test_absolute_allowed_with_flag(self) -> None:
        assert validate_path_safety("/home/user/file.png", allow_absolute=True)


class TestRestrictToDirectory:
    def test_safe_relative_path(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            p = restrict_to_directory("sub/file.png", td)
            assert str(p).startswith(td)

    def test_traversal_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            with pytest.raises(FileSafetyError):
                restrict_to_directory("../../secret.txt", td)

    def test_must_exist(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            with pytest.raises(FileSafetyError):
                restrict_to_directory("nonexistent.png", td, must_exist=True)

    def test_must_exist_passes_when_exists(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            os.makedirs(os.path.join(td, "sub"))
            p = restrict_to_directory("sub", td, must_exist=True)
            assert p.exists()


class TestValidateExtension:
    def test_valid_extension(self) -> None:
        assert validate_extension("file.png", frozenset({"png", "jpg"})) == "file.png"

    def test_invalid_extension(self) -> None:
        with pytest.raises(FileSafetyError):
            validate_extension("file.exe", frozenset({"png", "jpg"}))

    def test_no_extension(self) -> None:
        with pytest.raises(FileSafetyError):
            validate_extension("noext", frozenset({"png"}))

    def test_case_insensitive(self) -> None:
        assert validate_extension("file.PNG", frozenset({"png"})) == "file.PNG"


class TestSafeMkdir:
    def test_creates_directory(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            d = safe_mkdir("newdir", parent=td)
            assert d.is_dir()

    def test_creates_nested(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            d = safe_mkdir("a/b/c", parent=td)
            assert d.is_dir()

    def test_refuses_to_overwrite_file(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            open(os.path.join(td, "afile"), "w").close()
            with pytest.raises(FileSafetyError):
                safe_mkdir("afile", parent=td)

    def test_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            safe_mkdir("newdir", parent=td)
            d = safe_mkdir("newdir", parent=td)  # should not raise
            assert d.is_dir()


class TestIsWithinDirectory:
    def test_inside(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            assert is_within_directory("sub/file.png", td) is True

    def test_outside(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            assert is_within_directory("../../secret", td) is False


class TestNormalizePath:
    def test_strips_redundant_separators(self) -> None:
        assert normalize_path("assets//photo.png") == os.path.join("assets", "photo.png")

    def test_empty_raises(self) -> None:
        with pytest.raises(FileSafetyError):
            normalize_path("")

    def test_control_chars_rejected(self) -> None:
        with pytest.raises(FileSafetyError):
            normalize_path("file\x00.png")


class TestIsAbsolutePath:
    def test_unix_absolute(self) -> None:
        assert is_absolute_path("/etc/passwd") is True

    def test_relative(self) -> None:
        assert is_absolute_path("assets/photo.png") is False
