import importlib.util
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / 'scripts/check_architecture.py'
spec = importlib.util.spec_from_file_location('architecture', SCRIPT)
architecture = importlib.util.module_from_spec(spec)
spec.loader.exec_module(architecture)


class ArchitectureTests(unittest.TestCase):
    def check_code(self, name, code):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / 'src/refunddesk' / name
            path.parent.mkdir(parents=True)
            path.write_text(code, encoding='utf-8')
            return architecture.check(root)[0]

    def test_domain_standard_library(self):
        self.assertFalse(self.check_code('domain/money.py', 'from decimal import Decimal'))

    def test_application_domain(self):
        self.assertFalse(self.check_code('application/service.py', 'from refunddesk.domain import money'))

    def test_domain_database(self):
        self.assertTrue(self.check_code('domain/money.py', 'import sqlalchemy as sa'))

    def test_relative_import(self):
        self.assertTrue(self.check_code('application/service.py', 'from ..infrastructure import db'))

    def test_package_import_bypass(self):
        self.assertTrue(self.check_code('domain/money.py', 'from refunddesk import infrastructure'))

    def test_adapter_database(self):
        self.assertTrue(self.check_code('adapters/http/routes.py', 'from sqlalchemy import select'))

    def test_adapter_cross_import(self):
        self.assertTrue(self.check_code('adapters/http/routes.py', 'from ..mcp import tools'))

    def test_agent_application(self):
        self.assertTrue(self.check_code('agents/run.py', 'from refunddesk.application import usecases'))

    def test_composition_root(self):
        self.assertFalse(self.check_code('entrypoints/api.py', 'from refunddesk.infrastructure import db'))

    def test_legacy_only_at_entrypoint(self):
        self.assertTrue(self.check_code('application/a.py', 'from refunddesk.db import open_engine'))
        self.assertFalse(self.check_code('entrypoints/api.py', 'from refunddesk.db import open_engine'))

    def test_empty_project_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertTrue(architecture.check(Path(tmp))[0])

    def test_unknown_layer_fails(self):
        self.assertTrue(self.check_code('services/a.py', 'import json'))


if __name__ == '__main__':
    unittest.main()
