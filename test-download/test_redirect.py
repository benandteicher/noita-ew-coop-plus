import io
from pathlib import Path
import sys
import unittest
from urllib.request import Request

sys.dont_write_bytecode = True
import manage_redirect as redirect


class RedirectTests(unittest.TestCase):
    def test_frozen_manifest_page_and_fallback_agree(self):
        manifest = redirect.validate_local()
        page = redirect.render_page(manifest).decode()
        self.assertEqual(page.count(redirect.ASSET_URL), 2)
        self.assertIn('http-equiv="refresh"', page)
        self.assertIn("Extracted TEST folders never update automatically", page)
        self.assertNotIn("releases/latest", page)

    def test_changed_manifest_refuses(self):
        raw = (redirect.ROOT / "site/test/target.json").read_bytes()
        with self.assertRaises(RuntimeError):
            redirect.validate_local(raw.replace(b"setup-r3", b"setup-r4"))

    def test_default_is_verify_only_and_no_target_override_exists(self):
        options = redirect.arguments([])
        self.assertFalse(options.publish)
        self.assertFalse(options.verify_live)
        with self.assertRaises(SystemExit):
            redirect.arguments(["--target", "https://example.com"])

    def test_corrupt_or_incomplete_download_refuses(self):
        with self.assertRaises(RuntimeError):
            redirect.verify_stream(io.BytesIO(b"not the approved ZIP"))

    def test_unsafe_redirect_refuses(self):
        handler = redirect.AnonymousRedirects()
        for url in ("http://github.com/file", "https://example.com/file", "https://user:secret@github.com/file"):
            with self.subTest(url=url), self.assertRaises(RuntimeError):
                handler.redirect_request(Request(redirect.ASSET_URL), None, 302, "Found", {}, url)

    def test_publication_scope_is_exact_and_separate(self):
        self.assertEqual(redirect.SITE_FILES, {".nojekyll", "test/index.html", "test/target.json"})
        self.assertEqual(redirect.PAGES_BRANCH, "coopplus-test-pages")
        self.assertEqual(redirect.STABLE_URL, "https://benandteicher.github.io/noita-ew-coop-plus/test/")


if __name__ == "__main__":
    unittest.main()
