import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


class RendererTests(unittest.TestCase):
    def run_tool(self, root, *args):
        return subprocess.run([sys.executable, str(root / 'scripts/render_ai_rules.py'), *args],
                              capture_output=True, text=True, check=False)

    def fixture(self, root):
        for name in ('.ai/rules.json', 'scripts/render_ai_rules.py'):
            target = root / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / name, target)

    def test_drift_detection_and_regeneration(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.fixture(root)
            self.assertNotEqual(self.run_tool(root, '--check').returncode, 0)
            self.assertEqual(self.run_tool(root).returncode, 0)
            self.assertEqual(self.run_tool(root, '--check').returncode, 0)
            with (root / 'AGENTS.md').open('a') as f:
                f.write('\nmanual drift\n')
            self.assertNotEqual(self.run_tool(root, '--check').returncode, 0)
            self.assertEqual(self.run_tool(root).returncode, 0)
            self.assertEqual(self.run_tool(root, '--check').returncode, 0)

    def test_preserve_existing_instructions(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.fixture(root)
            (root / 'CLAUDE.md').write_text('Existing team instructions', encoding='utf-8')
            self.assertNotEqual(self.run_tool(root).returncode, 0)
            self.assertFalse((root / 'AGENTS.md').exists())
            self.assertEqual((root / 'CLAUDE.md').read_text(), 'Existing team instructions')


if __name__ == '__main__':
    unittest.main()
