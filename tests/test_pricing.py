"""The prices behind the per-video cost estimate come from one place, are overridable, and never break startup."""
import pytest

from takeone.config import Settings
from tests.test_backend import FakeMuse, client, settings  # noqa: F401


@pytest.fixture(autouse=True)
def clean_env(monkeypatch, tmp_path):
    for name in ('TAKEONE_PRICE_MUSE_INPUT_PER_M', 'TAKEONE_PRICE_MUSE_CACHED_INPUT_PER_M', 'TAKEONE_PRICE_MUSE_OUTPUT_PER_M',
                 'TAKEONE_PRICE_SAM_PER_1K_FRAMES', 'TAKEONE_SAM_MIN_BILLED_FRAMES'):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv('TAKEONE_WORKSPACE', str(tmp_path))


def test_defaults_are_metas_published_rates():
    p = Settings.from_env().pricing()
    assert p['currency'] == 'USD' and p['basis'] == 'estimate_from_reported_usage'
    assert (p['muse']['input_per_million'], p['muse']['cached_input_per_million'], p['muse']['output_per_million']) == (1.25, .15, 4.25)
    assert (p['sam']['per_thousand_frames'], p['sam']['min_billed_frames']) == (.20, 50)
    assert p['muse']['model'] == 'muse-spark-1.3' and p['sam']['model'] == 'sam-3.1'


def test_rates_can_be_overridden_from_the_environment(monkeypatch):
    monkeypatch.setenv('TAKEONE_PRICE_MUSE_OUTPUT_PER_M', '5.5')
    monkeypatch.setenv('TAKEONE_PRICE_SAM_PER_1K_FRAMES', '0.3')
    monkeypatch.setenv('TAKEONE_SAM_MIN_BILLED_FRAMES', '100')
    p = Settings.from_env().pricing()
    assert p['muse']['output_per_million'] == 5.5 and p['sam']['per_thousand_frames'] == .3 and p['sam']['min_billed_frames'] == 100


@pytest.mark.parametrize('bad', ['free', '-1', 'nan', 'inf', ' '])
def test_a_bad_value_keeps_the_published_default(monkeypatch, bad):
    monkeypatch.setenv('TAKEONE_PRICE_MUSE_INPUT_PER_M', bad)
    assert Settings.from_env().pricing()['muse']['input_per_million'] == 1.25  # never crashes startup, never a negative price


def test_zero_is_a_valid_price(monkeypatch):
    monkeypatch.setenv('TAKEONE_PRICE_SAM_PER_1K_FRAMES', '0')
    assert Settings.from_env().pricing()['sam']['per_thousand_frames'] == 0


def test_health_publishes_the_rates_the_ui_uses(settings):
    with client(settings, FakeMuse()) as c:
        pricing = c.get('/api/health').json()['pricing']
        assert pricing == settings.pricing() and pricing['muse']['input_per_million'] == 1.25
