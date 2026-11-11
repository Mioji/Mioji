# SPDX-FileCopyrightText: 2026 Alexander (Mioji) <mioji.net@outlook.com>
# SPDX-License-Identifier: MIT

"""Assembly of all generated files from validated inputs.

Rendering is split into two steps with different failure modes:

* :func:`load_inputs` reads and cross-validates every committed input.
  Only this step touches the file system, and it rejects references
  that would otherwise fail midway, such as a project whose artwork or
  prompt is missing or a locale key nobody defines;
* :func:`render` is a pure function from inputs and snapshot to a
  mapping of repository-relative paths to file content. Nothing is
  written here; :mod:`profilegen.publish` does that transactionally.

Layout of the outputs:

* ``README.md`` renders the default locale with root-relative paths;
  every other locale gets ``locales/readme/<code>/README.md`` with
  ``../../../`` prefixes. The default locale has no copy under
  ``locales/``, because nothing would link to it;
* ``assets/generated/shared/`` holds badges that are identical in every
  locale, ``assets/generated/readme/<code>/`` the localised panels;
* the snapshot is re-serialised as well, which normalises hand edits
  and makes ``--check`` cover its formatting.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from html import escape
from pathlib import Path, PurePosixPath
from typing import Final

from profilegen import panels, paths
from profilegen.config import ProfileConfig, SectionComponent, SkillGroup, load_profile, load_skills
from profilegen.errors import ConfigError, SnapshotError
from profilegen.locales import Locale, load_locales
from profilegen.models import Snapshot, Summary
from profilegen.security import validate_svg, validate_webp, webp_data_uri
from profilegen.svg import badge, picture
from profilegen.theme import Theme, load_theme
from profilegen.validation import Validator, load_json, parse_placeholders

DEFAULT_LOCALE: Final = "en"
PANEL_ARTWORK: Final = ("summary", "activity", "statistics", "knowledge")
LICENSE_STRINGS: Final = frozenset(("license_code", "license_art"))
"""Locale strings of the license block that closes every README."""

CODE_LICENSE_NAME: Final = "MIT"
ART_LICENSE_NAME: Final = "CC BY-NC-ND 4.0"
ART_LICENSE_URL: Final = "https://creativecommons.org/licenses/by-nc-nd/4.0/"
# Both names are identifiers rather than prose, so they are not localised.
TEMPLATE_SLOTS: Final = frozenset(
    (
        "language_navigation",
        "banner_link",
        "banner_src",
        "banner_alt",
        "contact_links",
        "summary_strip",
        "sections",
        "license",
    )
)
"""Placeholders available to ``locales/readme/template.md``."""


@dataclass(frozen=True, slots=True)
class Inputs:
    """Everything :func:`render` needs apart from the snapshot.

    Attributes:
        profile: Profile configuration.
        skills: Skill groups.
        theme: Design tokens.
        locales: Locales, default first.
        template: README template text.
        panel_artwork: ``data:`` URIs keyed by panel name.
        project_artwork: ``data:`` URIs keyed by project background key.
    """

    profile: ProfileConfig
    skills: tuple[SkillGroup, ...]
    theme: Theme
    locales: Mapping[str, Locale]
    template: str
    panel_artwork: Mapping[str, str]
    project_artwork: Mapping[str, str]


def _contained_file(root: Path, relative: PurePosixPath) -> Path | None:
    root_resolved = root.resolve()
    target = (root_resolved / relative).resolve()
    if not target.is_relative_to(root_resolved) or not target.is_file():
        return None
    return target


def _read_bytes(path: Path, source: PurePosixPath, what: str) -> bytes:
    try:
        return path.read_bytes()
    except OSError as exc:
        raise ConfigError(f"{source}: cannot read {what}: {exc}") from exc


def _artwork(root: Path, image: PurePosixPath, prompt: PurePosixPath) -> str:
    """Load artwork, requiring its generation prompt next to it.

    The prompt requirement keeps provenance attached to every image: an
    asset cannot be added or replaced without recording how it was made.
    """
    if _contained_file(root, prompt) is None:
        raise ConfigError(f"{prompt}: generation prompt for {image} is missing")
    image_path = _contained_file(root, image)
    if image_path is None:
        raise ConfigError(f"{image}: artwork file is missing")
    payload = _read_bytes(image_path, image, "artwork")
    return webp_data_uri(payload, source=str(image))


def _template(root: Path) -> str:
    try:
        text = (root / paths.TEMPLATE).read_text(encoding="utf-8")
    except OSError as exc:
        raise ConfigError(f"{paths.TEMPLATE}: cannot read template: {exc}") from exc
    names = parse_placeholders(Validator(str(paths.TEMPLATE), ConfigError), text, "")
    unknown = sorted(names - TEMPLATE_SLOTS)
    if unknown:
        raise ConfigError(f"{paths.TEMPLATE}: unknown placeholders: {', '.join(unknown)}")
    return text


def load_inputs(root: Path) -> Inputs:
    """Read and cross-validate all committed inputs below ``root``.

    Args:
        root: Repository root.

    Returns:
        The validated inputs.

    Raises:
        ConfigError: If any input is invalid or references something
            that does not exist.
        SecurityPolicyError: If artwork is not a valid WebP file.
    """
    profile = load_profile(root / paths.PROFILE_CONFIG, source=str(paths.PROFILE_CONFIG))
    skills = load_skills(root / paths.SKILLS_CONFIG, source=str(paths.SKILLS_CONFIG))
    theme = load_theme(root / paths.THEME_CONFIG, source=str(paths.THEME_CONFIG))

    configured_keys = profile.string_keys() | {group.heading_key for group in skills}
    reserved = sorted(configured_keys & panels.PLACEHOLDERS.keys())
    if reserved:
        raise ConfigError(
            "configured string keys must not reuse reserved placeholder keys: "
            + ", ".join(reserved)
        )
    strings = panels.REQUIRED_STRINGS | LICENSE_STRINGS | configured_keys
    locales = load_locales(
        root / paths.LOCALES_DIR,
        source=str(paths.LOCALES_DIR),
        default=DEFAULT_LOCALE,
        strings=strings,
        lists=panels.REQUIRED_LISTS,
        placeholders=panels.PLACEHOLDERS,
    )
    for contact in profile.contacts:
        theme.color(contact.accent)
    banner = _contained_file(root, profile.banner.path)
    if banner is None:
        raise ConfigError(f"{paths.PROFILE_CONFIG}: banner {profile.banner.path} is missing")
    validate_webp(
        _read_bytes(banner, profile.banner.path, "banner"), source=str(profile.banner.path)
    )

    return Inputs(
        profile=profile,
        skills=skills,
        theme=theme,
        locales=locales,
        template=_template(root),
        panel_artwork={
            name: _artwork(
                root, paths.BACKGROUNDS_DIR / f"{name}.webp", paths.PROMPTS_DIR / f"{name}.md"
            )
            for name in PANEL_ARTWORK
        },
        project_artwork={
            key: _artwork(
                root,
                paths.PROJECT_BACKGROUNDS_DIR / f"{key}.webp",
                paths.PROJECT_PROMPTS_DIR / f"{key}.md",
            )
            for key in dict.fromkeys(project.background_key for project in profile.projects)
        },
    )


def load_snapshot(root: Path) -> Snapshot:
    """Read and validate the committed snapshot below ``root``."""
    source = str(paths.SNAPSHOT)
    return Snapshot.from_json(
        load_json(root / paths.SNAPSHOT, source=source, error=SnapshotError), source=source
    )


class _Document:
    """Output collector that validates every SVG on insertion.

    Validating at the single insertion point, rather than in each
    renderer, guarantees that no unchecked SVG can reach the outputs.
    """

    def __init__(self, inputs: Inputs, outputs: dict[str, str]) -> None:
        self.inputs = inputs
        self.outputs = outputs

    def add(self, path: PurePosixPath, content: str) -> None:
        """Validate ``content`` and record it under ``path``."""
        validate_svg(content, source=str(path))
        self.outputs[str(path)] = content


def _text_link(href: str, text: str) -> str:
    return f'<a href="{escape(href)}">{escape(text)}</a>'


def _badge_link(href: str, src: str, height: int, alt: str) -> str:
    return (
        f'<a href="{escape(href)}"><img src="{escape(src)}" '
        f'height="{height}" alt="{escape(alt)}"></a>'
    )


def _shared_badges(
    doc: _Document, enabled_components: frozenset[SectionComponent]
) -> dict[str, PurePosixPath]:
    """Render locale-independent badges once; return paths by key."""
    theme = doc.inputs.theme
    rendered: dict[str, PurePosixPath] = {}
    for code, locale in doc.inputs.locales.items():
        path = paths.SHARED_ASSETS_DIR / "languages" / f"{code}.svg"
        doc.add(
            path,
            badge(
                locale.language,
                code.split("-", 1)[0].upper(),
                "violet",
                theme.navigation_badge.canvas_width,
                theme,
            ),
        )
        rendered[f"language:{code}"] = path
    for contact in doc.inputs.profile.contacts:
        if contact.enabled:
            path = paths.SHARED_ASSETS_DIR / "contacts" / f"{contact.id}.svg"
            doc.add(
                path,
                badge(
                    contact.label,
                    contact.mark,
                    contact.accent,
                    theme.contact_badge.canvas_width,
                    theme,
                ),
            )
            rendered[f"contact:{contact.id}"] = path
    if "skills" in enabled_components:
        for group in doc.inputs.skills:
            for skill in group.skills:
                path = paths.SHARED_ASSETS_DIR / "skills" / f"{skill.slug}.svg"
                width = theme.skill_badge.width_for(skill.label)
                doc.add(path, badge(skill.label, skill.mark, "violet", width, theme))
                rendered[f"skill:{skill.slug}"] = path
    return rendered


def _cards(inputs: Inputs, summary: Summary) -> tuple[panels.Card, ...]:
    cards: list[panels.Card] = []
    for index, project in enumerate(inputs.profile.projects, 1):
        stars: int | None = None
        if project.visibility == "public":
            stars = summary.repository_stars.get(project.repository.casefold())
            if stars is None:
                raise ConfigError(
                    f"{paths.PROFILE_CONFIG}: public project {project.repository} is not in "
                    "the snapshot; run the generator with --fetch"
                )
        cards.append(panels.Card(project=project, index=index, stars=stars))
    return tuple(cards)


def _locale_panels(
    doc: _Document,
    locale: Locale,
    cards: tuple[panels.Card, ...],
    summary: Summary,
    enabled_components: frozenset[SectionComponent],
) -> dict[str, PurePosixPath]:
    """Render the localised panels; return asset bases by key."""
    inputs, theme = doc.inputs, doc.inputs.theme
    art = inputs.panel_artwork
    base_dir = paths.locale_assets_dir(locale.code)
    bases: dict[str, PurePosixPath] = {}

    def emit(key: str, base: PurePosixPath, render_variant: Callable[[bool], str]) -> None:
        for compact in (False, True):
            suffix = "-compact" if compact else ""
            doc.add(base.with_name(base.name + suffix + ".svg"), render_variant(compact))
        bases[key] = base

    emit(
        "summary",
        base_dir / "dashboard" / "summary",
        lambda compact: panels.summary_strip(
            cards, summary, locale, theme, art["summary"], compact=compact
        ),
    )
    if "activity" in enabled_components:
        emit(
            "activity",
            base_dir / "dashboard" / "activity",
            lambda compact: panels.activity(
                summary, locale, theme, art["activity"], compact=compact
            ),
        )
    if "statistics" in enabled_components:
        emit(
            "statistics",
            base_dir / "dashboard" / "profile",
            lambda compact: panels.statistics(
                summary, locale, theme, art["statistics"], compact=compact
            ),
        )
    if "knowledge" in enabled_components:
        emit(
            "knowledge",
            base_dir / "dashboard" / "knowledge",
            lambda compact: panels.knowledge(locale, theme, art["knowledge"], compact=compact),
        )

    def card_variant(card: panels.Card) -> Callable[[bool], str]:
        artwork = inputs.project_artwork[card.project.background_key]
        return lambda compact: panels.project_card(card, locale, theme, artwork, compact=compact)

    def header_variant(number: int, title: str) -> Callable[[bool], str]:
        return lambda compact: panels.section_header(number, title, theme, compact=compact)

    def skill_group_variant(number: int, group: SkillGroup) -> Callable[[bool], str]:
        return lambda compact: panels.skill_group_header(
            number, locale.text(group.heading_key), len(group.skills), theme, compact=compact
        )

    if "projects" in enabled_components:
        for card in cards:
            emit(
                f"project:{card.index}",
                base_dir / "projects" / f"{card.index:02}",
                card_variant(card),
            )
    if "skills" in enabled_components:
        for number, group in enumerate(inputs.skills, 1):
            emit(
                f"skills:{group.heading_key}",
                base_dir / "skills" / group.heading_key,
                skill_group_variant(number, group),
            )
    enabled = [section for section in inputs.profile.sections if section.enabled]
    for number, section in enumerate(enabled, 1):
        emit(
            f"section:{section.id}",
            base_dir / "sections" / section.id,
            header_variant(number, locale.text(section.heading_key)),
        )
    return bases


def _readme(
    inputs: Inputs,
    locale: Locale,
    summary: Summary,
    cards: tuple[panels.Card, ...],
    shared: Mapping[str, PurePosixPath],
    bases: Mapping[str, PurePosixPath],
    prefix: str,
) -> str:
    """Fill the template for one locale and one relative location."""
    profile, theme = inputs.profile, inputs.theme
    github = f"https://github.com/{profile.username}"

    def figure(key: str, alt: str, href: str) -> str:
        return picture(
            str(bases[key]),
            alt,
            href,
            asset_prefix=prefix,
            compact_max_width=theme.compact_max_width,
        )

    def readme_link(code: str) -> str:
        target = paths.ROOT_README if code == DEFAULT_LOCALE else paths.locale_readme(code)
        return prefix + str(target)

    navigation = " ".join(
        _badge_link(
            readme_link(code),
            prefix + str(shared[f"language:{code}"]),
            theme.navigation_badge.display_height,
            other.language,
        )
        for code, other in inputs.locales.items()
    )
    contacts = [contact for contact in profile.contacts if contact.enabled]
    contact_links = " ".join(
        _badge_link(
            contact.href,
            prefix + str(shared[f"contact:{contact.id}"]),
            theme.contact_badge.display_height,
            contact.label,
        )
        for contact in contacts
    )

    sections = [section for section in profile.sections if section.enabled]
    enabled_components = frozenset(section.component for section in sections)
    content: dict[SectionComponent, str] = {}
    if "projects" in enabled_components:
        cards_html = [
            figure(
                f"project:{card.index}",
                _card_alt(card, locale),
                f"https://github.com/{card.project.repository}",
            )
            for card in cards
        ]
        size = profile.page_size
        groups = ["\n\n".join(cards_html[:size])]
        for start in range(size, len(cards_html), size):
            label = locale.text(
                "more_projects", start=start + 1, end=min(start + size, len(cards_html))
            )
            groups.append(
                f"<details>\n<summary><strong>{escape(label)}</strong></summary>\n\n"
                + "\n\n".join(cards_html[start : start + size])
                + "\n\n</details>"
            )
        content["projects"] = "\n\n".join(groups)
    if "activity" in enabled_components:
        content["activity"] = figure(
            "activity", panels.activity_alt(summary, locale), f"{github}?tab=overview"
        )
    if "statistics" in enabled_components:
        content["statistics"] = figure("statistics", panels.statistics_alt(summary, locale), github)
    skills_anchor = next(
        (f"#{section.id}" for section in sections if section.component == "skills"), github
    )
    if "skills" in enabled_components:
        content["skills"] = "\n\n".join(
            figure(f"skills:{group.heading_key}", locale.text(group.heading_key), skills_anchor)
            + "\n\n<p>"
            + "&nbsp; ".join(
                _badge_link(
                    skill.url,
                    prefix + str(shared[f"skill:{skill.slug}"]),
                    theme.skill_badge.display_height,
                    skill.label,
                )
                for skill in group.skills
            )
            + "</p>"
            for group in inputs.skills
        )
    if "knowledge" in enabled_components:
        # The knowledge panel invites conversation, so it links to the
        # first enabled contact and falls back to the GitHub profile.
        content["knowledge"] = figure(
            "knowledge", panels.knowledge_alt(locale), contacts[0].href if contacts else github
        )
    body = "\n\n".join(
        f'<a id="{section.id}"></a>\n\n'
        + figure(f"section:{section.id}", locale.text(section.heading_key), f"#{section.id}")
        + "\n\n"
        + content[section.component]
        for section in sections
    )
    projects_anchor = next(
        (f"#{section.id}" for section in sections if section.component == "projects"), github
    )

    code_license = _text_link(prefix + "LICENSES/MIT.txt", CODE_LICENSE_NAME)
    art_license = _text_link(ART_LICENSE_URL, ART_LICENSE_NAME)
    license_block = (
        '<p align="center"><sub>'
        f"{escape(locale.text('license_code'))}: {code_license} · "
        f"{escape(locale.text('license_art'))}: {art_license} · "
        f'<a href="{prefix}LICENSE">LICENSE</a>'
        "</sub></p>"
    )

    rendered = inputs.template.format_map(
        {
            "language_navigation": f'<p align="center">{navigation}</p>',
            "banner_link": escape(profile.banner.link),
            "banner_src": escape(prefix + str(profile.banner.path)),
            "banner_alt": escape(locale.text(profile.banner.alt_key)),
            "contact_links": f'<p align="center">{contact_links}</p>' if contact_links else "",
            "summary_strip": figure(
                "summary", panels.summary_alt(cards, summary, locale), projects_anchor
            ),
            "sections": body,
            "license": license_block,
        }
    )
    return re.sub(r"\n{3,}", "\n\n", rendered)


def _card_alt(card: panels.Card, locale: Locale) -> str:
    project = card.project
    description = locale.text(project.description_key).rstrip(".。")
    stars = locale.text("private_stars_caption") if card.stars is None else card.stars
    return (
        f"{project.repository}. {panels.status_label(project, locale)}. {description}. "
        f"{locale.text('project_stars_caption')}: {stars}"
    )


def render(inputs: Inputs, snapshot: Snapshot) -> dict[str, str]:
    """Render every generated file.

    Args:
        inputs: Validated inputs from :func:`load_inputs`.
        snapshot: Validated snapshot.

    Returns:
        File content keyed by repository-relative POSIX path, in a
        deterministic order.

    Raises:
        ConfigError: If the snapshot belongs to another account or lacks
            a public catalog repository.
        SecurityPolicyError: If a generated SVG fails validation, which
            indicates a renderer bug or hostile input.
    """
    if snapshot.username != inputs.profile.username:
        raise ConfigError(
            f"{paths.SNAPSHOT}: snapshot belongs to {snapshot.username}, "
            f"configuration to {inputs.profile.username}; run with --fetch"
        )
    outputs: dict[str, str] = {str(paths.SNAPSHOT): snapshot.dumps()}
    doc = _Document(inputs, outputs)
    enabled_components = frozenset(
        section.component for section in inputs.profile.sections if section.enabled
    )
    shared = _shared_badges(doc, enabled_components)
    summary = Summary.from_snapshot(snapshot)
    cards = _cards(inputs, summary)
    for code, locale in inputs.locales.items():
        bases = _locale_panels(doc, locale, cards, summary, enabled_components)
        if code == DEFAULT_LOCALE:
            outputs[str(paths.ROOT_README)] = _readme(
                inputs, locale, summary, cards, shared, bases, ""
            )
        else:
            outputs[str(paths.locale_readme(code))] = _readme(
                inputs, locale, summary, cards, shared, bases, "../../../"
            )
    return outputs
