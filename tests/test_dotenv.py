import os
import subprocess
import sys
from pathlib import Path


def run_config(directory: Path, **overrides: str) -> None:
    environment = {key: value for key, value in os.environ.items()
                   if not key.startswith(('DB_', 'DATABASE_', 'MIGRATION_DATABASE_', 'APP_ENV'))}
    environment.pop('PYTHON_DOTENV_DISABLED', None)
    environment.update(overrides)
    result = subprocess.run(
        [sys.executable, '-c', '''
import os
from sqlalchemy.engine import make_url
from refunddesk.entrypoints import api, worker, seed
from refunddesk.settings import database_options
url, options = database_options()
parsed = make_url(url)
assert parsed.host == "192.0.2.1"
assert parsed.password == "literal${PASSWORD} #@:/"
assert options["sslmode"] == "disable"
assert os.environ["APP_ENV"] == "development"
assert database_options(migration=True)[0] == url
'''], cwd=directory, env=environment, capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, result.stderr


def test_dotenv_loaded_at_startup(tmp_path: Path) -> None:
    (tmp_path / '.env').write_text(
        "DB_HOST=192.0.2.1\nDB_PASSWORD='literal${PASSWORD} #@:/'\n"
        "DB_SSLMODE=disable\nAPP_ENV=development\n", encoding='utf-8',
    )
    run_config(tmp_path)


def test_process_environment_wins(tmp_path: Path) -> None:
    (tmp_path / '.env').write_text(
        'DB_HOST=wrong.example\nDB_PASSWORD=wrong\nDB_SSLMODE=verify-full\nAPP_ENV=test\n',
        encoding='utf-8',
    )
    run_config(tmp_path, DB_HOST='192.0.2.1', DB_PASSWORD='literal${PASSWORD} #@:/',
               DB_SSLMODE='disable', APP_ENV='development')


def test_no_dotenv_required(tmp_path: Path) -> None:
    run_config(tmp_path, DB_HOST='192.0.2.1', DB_PASSWORD='literal${PASSWORD} #@:/',
               DB_SSLMODE='disable', APP_ENV='development')
