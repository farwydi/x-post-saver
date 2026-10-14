import argparse
import unittest
from unittest.mock import patch

from xtourl import main, post_url


class DownloadTest(unittest.TestCase):
    def test_links(self):
        self.assertEqual(
            post_url("https://x.com/example/status/123?s=20"),
            "https://x.com/example/status/123",
        )
        self.assertEqual(post_url("https://twitter.com/i/web/status/123"), "https://x.com/i/web/status/123")
        for value in ("--exec=bad", "https://x.com.evil.org/a/status/123", "https://x.com/a", "file:///a/status/123"):
            with self.subTest(value=value), self.assertRaises(argparse.ArgumentTypeError):
                post_url(value)

    @patch("xtourl.subprocess.run")
    def test_download_and_failure_status(self, run):
        run.return_value.returncode = 1
        with patch("sys.argv", ["xtourl", "https://x.com/a/status/123", "-o", "my videos"]):
            self.assertEqual(main(), 1)
        command = run.call_args.args[0]
        self.assertEqual(command[-2:], ["--", "https://x.com/a/status/123"])
        self.assertEqual(command[command.index("-P") + 1], "my videos")
        self.assertIn("--no-overwrites", command)

    @patch("xtourl.subprocess.run", side_effect=FileNotFoundError)
    def test_missing_downloader(self, run):
        with patch("sys.argv", ["xtourl", "https://x.com/a/status/123"]), self.assertRaises(SystemExit) as error:
            main()
        self.assertEqual(error.exception.code, 1)


if __name__ == "__main__":
    unittest.main()
