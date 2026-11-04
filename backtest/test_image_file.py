"""Offline coverage for image uploads on headless deployments."""
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from PIL import Image
from skills.internal.image_file_ops import (
    _describe_image_file,
    _resolve_readonly_image_path,
    get_image_file_tools,
)


class ImageFileTests(unittest.TestCase):
    def test_tool_is_registered_without_desktop_vision(self):
        self.assertEqual([t.name for t in get_image_file_tools()["tools"]], ["describe_image_file"])

    def test_image_and_question_reach_provider(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "photo.png"
            Image.new("RGB", (2000, 100), "red").save(path)
            with patch("skills.internal.system_ops._is_safe_path", return_value=path), patch("langchain_openai.ChatOpenAI") as client:
                client.return_value.invoke.return_value = SimpleNamespace(content="A red image")
                result = _describe_image_file("photo.png", "What color?")
            self.assertTrue(result["success"])
            self.assertEqual(result["data"]["message"], "A red image")
            content = client.return_value.invoke.call_args.args[0][0].content
            self.assertIn("What color?", content[0]["text"])
            self.assertTrue(content[1]["image_url"]["url"].startswith("data:image/png;base64,"))

    def test_provider_failure_is_not_reported_as_success(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "photo.png"
            Image.new("RGB", (2, 2)).save(path)
            with patch("skills.internal.system_ops._is_safe_path", return_value=path), patch("langchain_openai.ChatOpenAI", side_effect=RuntimeError("secret")):
                result = _describe_image_file("photo.png")
            self.assertFalse(result["success"])
            self.assertNotIn("secret", str(result))

    def test_outside_workspace_is_denied(self):
        with tempfile.TemporaryDirectory() as repo_tmp, tempfile.TemporaryDirectory() as outside_tmp:
            outside = Path(outside_tmp) / "outside.png"
            Image.new("RGB", (2, 2)).save(outside)
            with patch.dict(os.environ, {"CIEL_REPO_PATH": repo_tmp}), patch(
                "skills.internal.system_ops._is_safe_path", side_effect=PermissionError
            ):
                result = _describe_image_file(str(outside))
        self.assertEqual(result["error"]["code"], "PERMISSION_DENIED")

    def test_explicit_image_inside_current_project_is_readable(self):
        with tempfile.TemporaryDirectory() as repo_tmp:
            project_image = Path(repo_tmp) / "Model" / "reference.png"
            project_image.parent.mkdir()
            Image.new("RGB", (2, 2), "blue").save(project_image)
            with patch.dict(os.environ, {"CIEL_REPO_PATH": repo_tmp}):
                resolved = _resolve_readonly_image_path(str(project_image))
            self.assertEqual(resolved, project_image.resolve())

    def test_project_image_resolution_blocks_traversal(self):
        with tempfile.TemporaryDirectory() as repo_tmp, tempfile.TemporaryDirectory() as outside_tmp:
            outside = Path(outside_tmp) / "secret.png"
            Image.new("RGB", (2, 2)).save(outside)
            relative_escape = os.path.relpath(outside, repo_tmp)
            with patch.dict(os.environ, {"CIEL_REPO_PATH": repo_tmp}):
                with self.assertRaises(PermissionError):
                    _resolve_readonly_image_path(relative_escape)

    def test_non_image_extension_is_rejected_before_provider(self):
        result = _describe_image_file("Model/notes.txt")
        self.assertEqual(result["error"]["code"], "UNSUPPORTED_IMAGE_TYPE")


if __name__ == "__main__":
    unittest.main()
