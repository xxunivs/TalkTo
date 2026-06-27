import unittest


class AppImportTest(unittest.TestCase):
    def test_app_imports(self) -> None:
        from app.main import app

        self.assertEqual(app.title, "TalkTo APP AI")


if __name__ == "__main__":
    unittest.main()
