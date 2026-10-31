"""Browser-test server only. Never used by takeone.app or the normal startup command."""
from pathlib import Path
import tempfile

from takeone.app import create_app
from takeone.config import Settings
from tests.test_backend import FakeMuse

app = create_app(Settings(workspace=Path(tempfile.mkdtemp(prefix="takeone-browser-test-"))), provider=FakeMuse())
