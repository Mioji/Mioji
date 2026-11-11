# SPDX-FileCopyrightText: 2026 Alexander (Mioji) <mioji.net@outlook.com>
# SPDX-License-Identifier: MIT

"""End-to-end rendering, configuration validation and publication."""

from __future__ import annotations

import shutil
import struct
from html.parser import HTMLParser
from pathlib import Path, PurePath
from typing import Any

import pytest

from profilegen import paths, publish
from profilegen.cli import main
from profilegen.errors import ConfigError, SecurityPolicyError
from profilegen.models import Snapshot
from profilegen.render import load_inputs, load_snapshot, render
from profilegen.security import validate_svg, validate_webp
from profilegen.svg import units
from tests import factories

EN = paths.LOCALES_DIR / "en" / paths.STRINGS_FILE


@pytest.fixture
def root(tmp_path: Path) -> Path:
    return factories.copy_inputs(tmp_path)


def _render(root: Path, document: dict[str, Any] | None = None) -> dict[str, str]:
    snapshot = load_snapshot(root) if document is None else Snapshot.from_json(document)
    return render(load_inputs(root), snapshot)


def _edit(root: Path, name: PurePath, change: Any) -> None:
    data = factories.read(root / name)
    change(data)
    factories.write(root / name, data)


def test_committed_outputs_are_current() -> None:
    outputs = _render(paths.REPOSITORY_ROOT)
    assert publish.stale_files(paths.REPOSITORY_ROOT, outputs) == []


def test_rendering_is_deterministic_and_valid(root: Path) -> None:
    first = _render(root)
    assert first == _render(root)
    for name, content in first.items():
        if name.endswith(".svg"):
            validate_svg(content, source=name)
    readme = first["README.md"]
    order = [readme.index(f'id="{anchor}"') for anchor in ("work", "activity", "profile")]
    assert order == sorted(order)
    assert "locales/readme/en/README.md" not in first
    assert "../../../assets/profile/banner.webp" in first["locales/readme/ru/README.md"]
    assert 'href="locales/readme/zh-CN/README.md"' in readme


def test_locale_text_is_escaped_everywhere(root: Path) -> None:
    def inject(data: dict[str, Any]) -> None:
        data["content"]["dashboard_title"] = 'A < B & "C"'
        data["content"]["banner_alt"] = 'Banner "quoted" <alt>'

    _edit(root, EN, inject)
    outputs = _render(root)
    assert (
        "A &lt; B &amp; &quot;C&quot;"
        in outputs["assets/generated/readme/en/dashboard/activity.svg"]
    )
    assert 'alt="Banner &quot;quoted&quot; &lt;alt&gt;"' in outputs["README.md"]


def test_star_counts_follow_visibility(root: Path) -> None:
    outputs = _render(root, factories.snapshot(repositories=[factories.repository(stars=17)]))
    private_card = outputs["assets/generated/readme/en/projects/01.svg"]
    assert "—" in private_card
    assert "Not public" in private_card
    assert ">17</text>" in outputs["assets/generated/readme/en/projects/03.svg"]
    assert "17 public project stars" in outputs["assets/generated/readme/en/dashboard/summary.svg"]


def test_catalog_pages_fold_into_details(root: Path) -> None:
    def many(data: dict[str, Any]) -> None:
        # Cycling through every description keeps all locale keys in use,
        # which the strict catalogue check requires.
        keys = ("pattsys", "registry", "profile_description")
        template = data["catalog"]["projects"][0]
        data["catalog"]["projects"] = [
            dict(
                template,
                repository=f"Example/project-{index}",
                description_key=keys[index % len(keys)],
            )
            for index in range(20)
        ]

    _edit(root, paths.PROFILE_CONFIG, many)
    outputs = _render(root)
    readme = outputs["README.md"]
    assert readme.count("<details>") == 3
    assert readme.count('href="https://github.com/Example/project-') == 20
    assert "Projects 19–20" in readme
    assert "assets/generated/readme/en/projects/20.svg" in outputs


def test_disabled_sections_are_skipped_and_renumbered(root: Path) -> None:
    _edit(
        root, paths.PROFILE_CONFIG, lambda data: data["layout"]["sections"][0].update(enabled=False)
    )
    outputs = _render(root)
    assert 'id="work"' not in outputs["README.md"]
    assert ">01</text>" in outputs["assets/generated/readme/en/sections/activity.svg"]
    assert "assets/generated/readme/en/sections/work.svg" not in outputs


def test_public_project_missing_from_snapshot_is_an_error(root: Path) -> None:
    with pytest.raises(ConfigError, match="--fetch"):
        _render(root, factories.snapshot(repositories=[]))


def test_snapshot_of_another_account_is_an_error(root: Path) -> None:
    other = factories.snapshot(username="Other", repositories=[])
    with pytest.raises(ConfigError, match="belongs to Other"):
        _render(root, other)


@pytest.mark.parametrize(
    ("name", "change", "message"),
    [
        (
            paths.PROFILE_CONFIG,
            lambda data: data["contacts"][0].update(href="javascript:alert(1)"),
            "https",
        ),
        (
            paths.PROFILE_CONFIG,
            lambda data: data["contacts"][0].update(accent="cyan"),
            "unknown colour",
        ),
        (
            paths.PROFILE_CONFIG,
            lambda data: data["layout"]["sections"][0].update(component="chart"),
            "expected one of",
        ),
        (
            paths.PROFILE_CONFIG,
            lambda data: data["catalog"]["projects"].append(data["catalog"]["projects"][0]),
            "duplicate repository",
        ),
        (paths.PROFILE_CONFIG, lambda data: data.update(extra=True), "unknown keys"),
        (paths.THEME_CONFIG, lambda data: data["palette"].update(pink="pink"), "does not match"),
        (paths.THEME_CONFIG, lambda data: data["palette"].pop("grid"), "missing tokens"),
        (EN, lambda data: data["content"].pop("updated_label"), "missing keys"),
        (EN, lambda data: data["content"].update(unused="x"), "unknown keys"),
        (EN, lambda data: data["content"].update(updated_label="{when}"), "unknown placeholders"),
        (EN, lambda data: data["locale_format"]["date"].update(date_format="%B"), "directive"),
        (EN, lambda data: data.update(locale_code="ru"), "directory name"),
    ],
)
def test_invalid_configuration_is_rejected(
    root: Path, name: PurePath, change: Any, message: str
) -> None:
    _edit(root, name, change)
    with pytest.raises(ConfigError, match=message):
        _render(root)


def test_template_placeholders_are_checked(root: Path) -> None:
    (root / paths.TEMPLATE).write_text("{unknown}\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="unknown placeholders"):
        load_inputs(root)


def test_artwork_requires_a_generation_prompt(root: Path) -> None:
    (root / paths.PROMPTS_DIR / "summary.md").unlink()
    with pytest.raises(ConfigError, match="prompt"):
        load_inputs(root)


@pytest.mark.parametrize(
    "content",
    [
        "<svg xmlns='http://www.w3.org/2000/svg'><script/></svg>",
        "<svg xmlns='http://www.w3.org/2000/svg' onload='x'/>",
        "<svg xmlns='http://www.w3.org/2000/svg'><image href='https://example.org/a.png'/></svg>",
        "<svg xmlns='http://www.w3.org/2000/svg'><path fill='url(https://example.org/a)'/></svg>",
        "<svg xmlns='http://www.w3.org/2000/svg'><path fill='URL(//evil.com)'/></svg>",
        "<svg xmlns='http://www.w3.org/2000/svg'><path fill='uRl(//evil.com)'/></svg>",
        "<svg xmlns='http://www.w3.org/2000/svg'><path fill='javascript:x'/></svg>",
        "<svg xmlns='http://www.w3.org/2000/svg' xml:base='//evil.com'><path d='M0 0'/></svg>",
        "<svg xmlns='http://www.w3.org/2000/svg'><path d='M0 0' target='_blank'/></svg>",
        "<?xml-stylesheet href='http://x/s.css'?><svg xmlns='http://www.w3.org/2000/svg'/>",
        "<!DOCTYPE svg><svg xmlns='http://www.w3.org/2000/svg'/>",
        "<svg/>",
        "<svg xmlns='http://www.w3.org/2000/svg'><svg/></svg>",
        "<svg xmlns='http://www.w3.org/2000/svg'><path d='M0 0'><text>x</text></path></svg>",
    ],
)
def test_svg_validator_rejects_active_content(content: str) -> None:
    with pytest.raises(SecurityPolicyError):
        validate_svg(content, source="test.svg")


def test_svg_validator_reports_event_handler_name() -> None:
    with pytest.raises(SecurityPolicyError, match="event handler"):
        validate_svg("<svg xmlns='http://www.w3.org/2000/svg' onclick='x'/>", source="test.svg")


@pytest.mark.parametrize(
    "payload",
    [
        b"RIFF" + struct.pack("<I", 12) + b"WEBPVP8 " + struct.pack("<I", 0),
        b"RIFF" + struct.pack("<I", 16) + b"WEBPVP8 " + struct.pack("<I", 99) + b"abcd",
    ],
)
def test_webp_validator_rejects_invalid_chunk_bounds(payload: bytes) -> None:
    with pytest.raises(SecurityPolicyError, match="WebP"):
        validate_webp(payload, source="bad.webp")


def test_banner_rejects_traversal_special_chars_and_corrupt_webp(root: Path) -> None:
    for bad_path in (
        "assets\\profile\\banner.webp",
        "assets/profile/../profile/banner.webp",
        'assets/profile/"evil.webp',
    ):
        _edit(
            root,
            paths.PROFILE_CONFIG,
            lambda data, p=bad_path: data["identity"]["banner"].update(asset_path=p),
        )
        with pytest.raises(ConfigError, match=r"identity\.banner\.asset_path"):
            load_inputs(root)

    (root / "assets/profile/banner.webp").write_bytes(b"not-a-webp-image-0000")
    _edit(
        root,
        paths.PROFILE_CONFIG,
        lambda data: data["identity"]["banner"].update(asset_path="assets/profile/banner.webp"),
    )
    with pytest.raises(SecurityPolicyError, match="WebP"):
        load_inputs(root)


def test_template_rejects_format_specifiers_and_config_guards_reserved_keys(root: Path) -> None:
    (root / paths.TEMPLATE).write_text("{sections!r}\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="unsupported placeholder"):
        load_inputs(root)

    fresh = root / "fresh"
    factories.copy_inputs(fresh)
    _edit(
        fresh,
        paths.PROFILE_CONFIG,
        lambda data: data["catalog"]["projects"][0].update(description_key="dashboard_alt"),
    )
    with pytest.raises(ConfigError, match="reserved"):
        load_inputs(fresh)


def test_theme_accepts_uppercase_hex_colors(root: Path) -> None:
    _edit(root, paths.THEME_CONFIG, lambda data: data["palette"].update(pink="#F28ACB"))
    inputs = load_inputs(root)
    assert inputs.theme.palette["pink"] == "#f28acb"


def test_skills_with_punctuation_require_explicit_id_and_render(root: Path) -> None:
    def add_cpp(data: dict[str, Any]) -> None:
        data["languages_label"].append(
            {"label": "C++", "mark": "C++", "url": "https://isocpp.org/"}
        )

    _edit(root, paths.SKILLS_CONFIG, add_cpp)
    with pytest.raises(ConfigError, match="id"):
        load_inputs(root)

    def add_cpp_with_id(data: dict[str, Any]) -> None:
        data["languages_label"][-1]["id"] = "cpp"

    _edit(root, paths.SKILLS_CONFIG, add_cpp_with_id)
    outputs = _render(root)
    assert "assets/generated/shared/skills/cpp.svg" in outputs
    assert ">C++</text>" in outputs["assets/generated/shared/skills/cpp.svg"]
    assert "assets/generated/readme/en/skills/languages_label.svg" in outputs
    assert ">07</text>" in outputs["assets/generated/readme/en/skills/languages_label.svg"]


def test_wide_badge_mark_and_time_directive_in_date_format_are_rejected(root: Path) -> None:
    _edit(root, paths.PROFILE_CONFIG, lambda data: data["contacts"][0].update(mark="中文"))
    with pytest.raises(ConfigError, match="mark"):
        load_inputs(root)

    fresh = root / "fresh_date"
    factories.copy_inputs(fresh)
    _edit(
        fresh,
        EN,
        lambda data: data["locale_format"]["date"].update(date_format="%Y-%m-%d %H:%M"),
    )
    with pytest.raises(ConfigError, match="directive"):
        load_inputs(fresh)


def test_zh_cn_card_alt_has_single_period_and_disabled_contacts_leave_no_gap(root: Path) -> None:
    outputs = _render(root)
    assert "。." not in outputs["locales/readme/zh-CN/README.md"]

    _edit(
        root,
        paths.PROFILE_CONFIG,
        lambda data: [contact.update(enabled=False) for contact in data["contacts"]],
    )
    disabled_outputs = _render(root)
    assert '<p align="center"></p>' not in disabled_outputs["README.md"]
    assert "\n\n\n" not in disabled_outputs["README.md"]


class _SvgInspector(HTMLParser):
    """Collects SVG canvas bounds and text elements for layout assertions."""

    def __init__(self) -> None:
        super().__init__()
        self.width = 0.0
        self.height = 0
        self.texts: list[tuple[dict[str, str], str]] = []
        self._current: dict[str, str] | None = None
        self._chunks: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        mapping = {k: v or "" for k, v in attrs}
        if tag == "svg":
            view_box = mapping["viewbox"].split()
            self.width = float(view_box[2])
            self.height = int(view_box[3])
        elif tag == "text":
            self._current = mapping
            self._chunks = []

    def handle_data(self, data: str) -> None:
        if self._current is not None:
            self._chunks.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "text" and self._current is not None:
            self.texts.append((self._current, "".join(self._chunks)))
            self._current = None


def _inspect_svg(svg: str) -> _SvgInspector:
    validate_svg(svg, source="layout-check.svg")
    inspector = _SvgInspector()
    inspector.feed(svg)
    return inspector


def _assert_text_fits_inside_panel(svg: str) -> None:
    info = _inspect_svg(svg)
    margin = 6 if info.height == 42 else 10
    boxes: list[tuple[float, float, float, str]] = []
    for attrs, content in info.texts:
        x = float(attrs["x"])
        y = float(attrs["y"])
        size = float(attrs["font-size"])
        mono = "monospace" in attrs.get("font-family", "")
        advance = 0.6 if mono else 0.54
        text_width = units(content) * size * advance
        if attrs.get("text-anchor") == "end":
            left, right = x - text_width, x
        else:
            left, right = x, x + text_width
        assert left >= 0, f"{content!r} overflows left edge ({left:.1f} < 0)"
        assert right <= info.width - margin, (
            f"{content!r} overflows ({right:.1f} > {info.width - margin})"
        )
        boxes.append((y, left, right, content))

    for idx, (y_a, left_a, right_a, text_a) in enumerate(boxes):
        for y_b, left_b, right_b, text_b in boxes[idx + 1 :]:
            if abs(y_b - y_a) <= 3.0:
                overlap = min(right_a, right_b) - max(left_a, left_b)
                assert overlap <= 1.0, (
                    f"{text_a!r} [{left_a:.1f}, {right_a:.1f}] overlaps "
                    f"{text_b!r} [{left_b:.1f}, {right_b:.1f}]"
                )


def test_all_committed_svgs_fit_within_panel_bounds() -> None:
    for name, content in _render(paths.REPOSITORY_ROOT).items():
        if name.endswith(".svg"):
            _assert_text_fits_inside_panel(content)


def test_extreme_valid_inputs_fit_inside_svg_bounds(root: Path) -> None:
    long_repo = "Example/" + "w" * 100
    long_cjk = "一二三四五六七八九十。，" * 5

    def update_profile(data: dict[str, Any]) -> None:
        data["catalog"]["projects"][2]["repository"] = long_repo
        data["contacts"][0]["label"] = "Super Long Contact Name!"

    _edit(root, paths.PROFILE_CONFIG, update_profile)
    for code in ("en", "ru", "zh-CN"):
        loc = paths.LOCALES_DIR / code / paths.STRINGS_FILE

        def update_locale(data: dict[str, Any], current: str = code) -> None:
            if current == "en":
                data["language"] = "Brazilian Portuguese"
            data["content"].update(
                project_stars_label=long_cjk,
                event_repositories_label=long_cjk,
                profile_description=long_cjk,
                joined_label="Member registration timestamp label!!",
                softskills_label="Very Long Engineering Principles Heading Title!!",
                projects_heading="Extremely Long Section Heading For Testing Overflow",
            )
            data["structured_lists"]["softskills_items"] = [long_cjk, long_cjk]

        _edit(root, loc, update_locale)
    snap = factories.snapshot(
        repositories=[factories.repository(name="Mioji/Mioji", stars=999_999)],
        project_repositories=[factories.repository(name=long_repo, stars=999_999)],
    )
    for name, content in _render(root, snap).items():
        if name.endswith(".svg"):
            _assert_text_fits_inside_panel(content)


def test_disabled_sections_omit_unreferenced_component_svgs(root: Path) -> None:
    def disable(data: dict[str, Any]) -> None:
        for section in data["layout"]["sections"]:
            if section["component"] in {"projects", "skills"}:
                section["enabled"] = False

    _edit(root, paths.PROFILE_CONFIG, disable)
    outputs = _render(root)
    assert not any("/projects/" in key for key in outputs)
    assert not any("/skills/" in key for key in outputs)
    assert "assets/generated/readme/en/dashboard/summary.svg" in outputs


def test_three_letter_locale_code_keeps_full_mark(root: Path) -> None:
    fil_dir = root / paths.LOCALES_DIR / "fil"
    fil_dir.mkdir(parents=True)
    data = factories.read(root / EN)
    data["locale_code"] = "fil"
    data["language"] = "Filipino"
    factories.write(fil_dir / paths.STRINGS_FILE, data)
    outputs = _render(root)
    badge_svg = outputs["assets/generated/shared/languages/fil.svg"]
    assert ">FIL</text>" in badge_svg
    _assert_text_fits_inside_panel(badge_svg)


def test_long_russian_counts_and_multiline_copy_stay_within_panel_bounds(root: Path) -> None:
    outputs = _render(
        root, factories.snapshot(followers=1_234, repositories=[factories.repository(stars=1_234)])
    )
    for key in (
        "assets/generated/readme/ru/projects/03.svg",
        "assets/generated/readme/ru/projects/03-compact.svg",
        "assets/generated/readme/ru/dashboard/summary.svg",
        "assets/generated/readme/ru/dashboard/summary-compact.svg",
    ):
        _assert_text_fits_inside_panel(outputs[key])

    ru = paths.LOCALES_DIR / "ru" / paths.STRINGS_FILE
    _edit(
        root,
        ru,
        lambda data: data["content"].update(
            project_stars_label="Очень длинная подпись для подсчёта звёзд",
            public_scope="Очень подробное описание источника публичных данных репозиториев",
        ),
    )
    stretched = _render(root, factories.snapshot(repositories=[factories.repository(stars=1_234)]))
    summary_svg = stretched["assets/generated/readme/ru/dashboard/summary-compact.svg"]
    stats_svg = stretched["assets/generated/readme/ru/dashboard/profile-compact.svg"]
    assert _inspect_svg(summary_svg).height > 145
    assert _inspect_svg(stats_svg).height > 770


def test_publish_rollback_prunes_new_empty_directories_and_cleans_broken_symlinks(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    outputs = _render(root)
    publish.apply(root, publish.plan(root, outputs))

    broken = root / paths.GENERATED_DIR / "readme" / "xx" / "stale.svg"
    broken.parent.mkdir(parents=True)
    broken.symlink_to(root / "does-not-exist.svg")
    assert f"{paths.GENERATED_DIR}/readme/xx/stale.svg (orphaned)" in publish.stale_files(
        root, outputs
    )
    publish.apply(root, publish.plan(root, outputs))
    assert not broken.is_symlink()
    assert not broken.parent.exists()

    extra = dict(outputs)
    extra[f"{paths.GENERATED_DIR}/readme/zz/panel.svg"] = outputs[
        f"{paths.GENERATED_DIR}/readme/en/sections/work.svg"
    ]
    real_replace = Path.replace

    def fail_on_new(self: Path, target: Path) -> Path:
        if target.name == "panel.svg":
            raise OSError("simulated disk error")
        return real_replace(self, target)

    monkeypatch.setattr(Path, "replace", fail_on_new)
    with pytest.raises(OSError, match="simulated"):
        publish.apply(root, publish.plan(root, extra))
    assert not (root / paths.GENERATED_DIR / "readme" / "zz").exists()


def test_publish_prunes_orphans_and_is_idempotent(root: Path) -> None:
    outputs = _render(root)
    orphan = root / paths.GENERATED_DIR / "readme" / "xx" / "old.svg"
    orphan.parent.mkdir(parents=True)
    orphan.write_text("<svg/>", encoding="utf-8")
    assert f"{paths.GENERATED_DIR}/readme/xx/old.svg (orphaned)" in publish.stale_files(
        root, outputs
    )
    publish.apply(root, publish.plan(root, outputs))
    assert not orphan.parent.exists()
    assert publish.plan(root, outputs).is_empty


def test_publish_rolls_back_on_failure(root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    outputs = _render(root)
    publish.apply(root, publish.plan(root, outputs))
    before = {name: (root / name).read_bytes() for name in outputs}
    changed = {name: content + "\n" for name, content in outputs.items()}
    real_replace = Path.replace
    calls = 0

    def flaky_replace(self: Path, target: Path) -> Path:
        nonlocal calls
        calls += 1
        if calls == 3:
            raise OSError("disk full")
        return real_replace(self, target)

    monkeypatch.setattr(Path, "replace", flaky_replace)
    with pytest.raises(OSError, match="disk full"):
        publish.apply(root, publish.plan(root, changed))
    assert before == {name: (root / name).read_bytes() for name in outputs}


def test_publish_refuses_unmanaged_paths(root: Path) -> None:
    with pytest.raises(SecurityPolicyError):
        publish.plan(root, {"configs/profile.json": "{}"})


def test_cli_check_reports_drift(root: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main([], root=root) == 0
    assert main(["--check"], root=root) == 0
    (root / "README.md").write_text("edited\n", encoding="utf-8")
    assert main(["--check"], root=root) == 1
    assert "README.md" in capsys.readouterr().err


def test_cli_failed_fetch_leaves_files_untouched(root: Path) -> None:
    assert main([], root=root) == 0
    before = sorted(path.read_bytes() for path in root.rglob("*") if path.is_file())

    class FailingClient:
        def __init__(self, token: str | None) -> None:
            self.token = token

        def get(self, path: str) -> object:
            raise ConfigError("API unavailable")

        def get_pages(self, path: str, *, max_pages: int, allow_truncation: bool) -> list[object]:
            raise AssertionError("not reached")

    assert main(["--fetch"], root=root, client_factory=FailingClient) == 1
    assert before == sorted(path.read_bytes() for path in root.rglob("*") if path.is_file())


def test_cli_modes_are_mutually_exclusive() -> None:
    with pytest.raises(SystemExit) as exit_info:
        main(["--fetch", "--check"])
    assert exit_info.value.code == 2


def test_webp_validator_rejects_trailing_data_and_empty_vp8x() -> None:
    # len == 28 bytes, so RIFF payload size is 20 (leaving trailing bytes).
    trailing = (
        b"RIFF"
        + struct.pack("<I", 20)
        + b"WEBPVP8 "
        + struct.pack("<I", 4)
        + b"abcdJUNK"
        + b"\x00\x00\x00\x00"
    )
    trailing = b"RIFF" + struct.pack("<I", len(trailing) - 8) + trailing[8:]
    with pytest.raises(SecurityPolicyError, match="WebP"):
        validate_webp(trailing, source="polyglot.webp")

    vp8x_only = (
        b"RIFF" + struct.pack("<I", 22) + b"WEBP" + b"VP8X" + struct.pack("<I", 10) + b"\x00" * 10
    )
    with pytest.raises(SecurityPolicyError, match="WebP"):
        validate_webp(vp8x_only, source="empty-vp8x.webp")

    vp8x_valid = (
        b"RIFF"
        + struct.pack("<I", 34)
        + b"WEBP"
        + b"VP8X"
        + struct.pack("<I", 10)
        + b"\x00" * 10
        + b"VP8 "
        + struct.pack("<I", 4)
        + b"abcd"
    )
    validate_webp(vp8x_valid, source="valid-vp8x.webp")


def test_svg_validator_rejects_stray_text_and_tail() -> None:
    with pytest.raises(SecurityPolicyError):
        validate_svg("<svg xmlns='http://www.w3.org/2000/svg'>stray</svg>", source="stray.svg")
    with pytest.raises(SecurityPolicyError):
        validate_svg(
            "<svg xmlns='http://www.w3.org/2000/svg'><path d='M0 0'/>tail</svg>",
            source="tail.svg",
        )


def test_urls_reject_whitespace_and_unicode_skills_with_id_render(root: Path) -> None:
    _edit(
        root,
        paths.PROFILE_CONFIG,
        lambda data: data["contacts"][1].update(href="https://github.com/Mioji bad"),
    )
    with pytest.raises(ConfigError, match="whitespace"):
        load_inputs(root)

    fresh = root / "skills_unicode"
    factories.copy_inputs(fresh)
    _edit(
        fresh,
        paths.SKILLS_CONFIG,
        lambda data: data["languages_label"].append(
            {
                "label": "深度学习 & 部署",
                "id": "deep-learning",
                "mark": "DL",
                "url": "https://example.com/",
            }
        ),
    )
    outputs = _render(fresh)
    assert "assets/generated/shared/skills/deep-learning.svg" in outputs
    assert 'alt="深度学习 &amp; 部署"' in outputs["README.md"]

    _edit(
        fresh,
        paths.SKILLS_CONFIG,
        lambda data: data["languages_label"][-1].update(label="Docker "),
    )
    with pytest.raises(ConfigError):
        load_inputs(fresh)


def test_banner_read_error_raises_config_error(root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    real_read_bytes = Path.read_bytes

    def fail_banner(self: Path) -> bytes:
        if self.name == "banner.webp":
            raise PermissionError("locked banner")
        return real_read_bytes(self)

    monkeypatch.setattr(Path, "read_bytes", fail_banner)
    with pytest.raises(ConfigError, match="banner"):
        load_inputs(root)


def test_publish_symlinks_backslash_and_rollback_safety(
    root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with pytest.raises(SecurityPolicyError):
        publish.plan(root, {"assets/generated/shared\\evil.svg": "<svg/>"})
    with pytest.raises(SecurityPolicyError):
        publish.plan(root, {"README.md\x00.bak": "x"})

    outputs = _render(root)
    publish.apply(root, publish.plan(root, outputs))

    target_copy = tmp_path / "external_readme.md"
    target_copy.write_bytes(outputs["README.md"].encode("utf-8"))
    readme_path = root / paths.ROOT_README
    readme_path.unlink()
    readme_path.symlink_to(target_copy)
    symlink_plan = publish.plan(root, outputs)
    assert paths.ROOT_README in symlink_plan.writes
    publish.apply(root, symlink_plan)
    assert not readme_path.is_symlink()

    external_empty = tmp_path / "external_empty"
    external_empty.mkdir()
    link_dir = root / paths.GENERATED_DIR / "external_link"
    link_dir.symlink_to(external_empty, target_is_directory=True)
    changed = dict(outputs)
    changed["README.md"] = outputs["README.md"] + "\n"
    publish.apply(root, publish.plan(root, changed))
    assert external_empty.is_dir()
    link_dir.unlink()

    orphan = root / paths.GENERATED_DIR / "readme" / "xx" / "locked.svg"
    orphan.parent.mkdir(parents=True)
    orphan.write_bytes(b"locked-content")
    real_unlink = Path.unlink
    real_write_bytes = Path.write_bytes

    def deny_unlink(self: Path, missing_ok: bool = False) -> None:
        if self.name == "locked.svg":
            raise PermissionError("cannot unlink locked")
        real_unlink(self, missing_ok=missing_ok)

    def forbid_restore_write(self: Path, data: Any) -> int:
        if self.name == "locked.svg":
            raise AssertionError("restore overwrote locked file")
        return real_write_bytes(self, data)

    monkeypatch.setattr(Path, "unlink", deny_unlink)
    monkeypatch.setattr(Path, "write_bytes", forbid_restore_write)
    with pytest.raises(PermissionError, match="cannot unlink locked"):
        publish.apply(root, publish.plan(root, outputs))
    assert orphan.read_bytes() == b"locked-content"
    shutil.rmtree(orphan.parent, ignore_errors=True)
