"""ファイル監視で記録しないもののテスト。"""

import unittest

from lablog.filewatch import is_ignored


class IgnoreTest(unittest.TestCase):
    def test_project_files_are_recorded(self):
        for path in [r"研究\main.py", r"論文\本文.docx", r"repo\.github\workflows\ci.yml", r"data\results.csv"]:
            self.assertFalse(is_ignored(path), path)

    def test_noise_is_ignored(self):
        for path in [r"repo\.git\index", r"repo\node_modules\x\index.js", r"~$本文.docx", r"repo\x.pyc",
                     # ホームフォルダごと監視したとき、アプリが裏で書くもの
                     r"AppData\Local\Google\Chrome\User Data\Default\History",
                     r"appdata\Roaming\Code\User\settings.json", r".cache\pip\x", r".ollama\models\blobs\sha256-1"]:
            self.assertTrue(is_ignored(path), path)


if __name__ == "__main__":
    unittest.main()
