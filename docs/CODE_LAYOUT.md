# Code layout

New features are self-contained packages; existing code is not reorganised in bulk. The shared core (`service.py`, `media.py`,
`rendering.py`, `contracts.py`, ...) is imported by many files, so moving it would change every import for no behavioural gain.

## Rule

A feature owns a folder on each side and touches the core only through small, named hooks:

| | Backend | Frontend | Tests |
|---|---|---|---|
| Color (source domain, input profiles) | `takeone/color/` | `frontend/src/features/color/` | `tests/color/` |
| Preferences (older, kept where it is) | `takeone/preference_api.py`, `preferences.py` | `frontend/src/lib/preference*.ts` | `tests/test_preferences*.py` |

* **Pure logic** (resolving, parsing, deciding) sits in modules with no I/O, so it is tested without a server: `color/domain.py`,
  `color/lut.py`, `color/levels.py` (measurement), `color/profile.py`, `features/color/inputProfile.ts`.
* **Service methods** live in a mixin (`color/service.py`: `ColorMixin`) that `Service` inherits, instead of in `service.py`.
* **HTTP** lives in `color/api.py`, installed by `app.create_app` with one line (`install_color`).
* **`color/__init__.py` stays empty**: `media` imports `color.domain` and `color.profile`, and `color.files` imports `media`. Import
  the submodule you need (`from .color import domain as color_domain`).
* **Readers of the original** call `color.files.chain_for_file(path, ...)`. Never build a conversion chain by hand; the input profile
  is found beside the file, so a reader that goes through it cannot miss it.
