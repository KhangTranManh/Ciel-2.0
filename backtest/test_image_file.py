"""Offline coverage for image uploads on headless deployments."""
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from PIL import Image
from skills.internal.image_file_ops import _describe_image_file, get_image_file_tools


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
        with patch("skills.internal.system_ops._is_safe_path", side_effect=PermissionError):
            result = _describe_image_file("outside.png")
        self.assertEqual(result["error"]["code"], "PERMISSION_DENIED")


if __name__ == "__main__":
    unittest.main()
