"""The container's clock is Finland's, and that is config — so it is tested as config.

Every temporal judgement in this system hangs off `date.today()`: `source.temporal_status` decides
current vs history, the 365-day window drops old notes, and age is derived from a date. Without a
timezone the runtime clocks UTC, and the service and its users are Finnish — so between 21:00 and
midnight UTC the server's "today" is already yesterday in Finland, and a record exactly 365 days old
flips from `history` to `future`.

**No Python test can catch this**: every test passes an explicit `today`, so the suite never reads a
live clock. That is why the two config surfaces are pinned here instead. This test is narrow on
purpose — it does not start a container; it asserts the setting exists in the image definition and in
the live-deployment template, which is where it can silently go missing.
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DOCKERFILE = ROOT / "Dockerfile"
BICEP = ROOT / "azure" / "hosting" / "main.bicep"
TZ_VALUE = "Europe/Helsinki"


def test_the_runtime_image_sets_the_finnish_timezone():
    """The image default covers any run of the image, including a local `docker run`."""
    dockerfile = DOCKERFILE.read_text(encoding="utf-8")
    assert f"TZ={TZ_VALUE}" in dockerfile, (
        "the runtime image must set TZ, or the container clocks UTC and date.today() is a day stale "
        "in Finland after 21:00 UTC"
    )


def test_the_deployment_template_sets_the_finnish_timezone():
    """The template covers the live app, where the env is set per revision."""
    bicep = BICEP.read_text(encoding="utf-8")
    assert "name: 'TZ'" in bicep and f"value: '{TZ_VALUE}'" in bicep, (
        "the container-app env must carry TZ; the image default alone does not survive an "
        "explicit env list on the revision"
    )


def test_the_timezone_is_actually_known_to_the_image():
    """`TZ` only helps if tzdata is present — checked, because a slim base image may omit it.

    A bare `python:3.12-slim` ships tzdata (this was verified inside the deployed image, where
    `/usr/share/zoneinfo/Europe/Helsinki` exists). If the base image ever changes, this test is the
    reminder that the variable alone would then be inert and the package must be installed.
    """
    dockerfile = DOCKERFILE.read_text(encoding="utf-8")
    assert "python:3.12-slim" in dockerfile, (
        "the base image changed; re-verify that tzdata is present, or set TZ via a package install"
    )
