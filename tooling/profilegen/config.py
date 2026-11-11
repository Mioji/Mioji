# SPDX-FileCopyrightText: 2026 Alexander (Mioji) <mioji.net@outlook.com>
# SPDX-License-Identifier: MIT

"""Profile and skill configuration under ``configs/``.

``profile.json`` declares identity, contacts, the section layout and the
project catalog; ``skills.json`` declares skill badges grouped under
locale headings. Both are parsed into immutable objects with closed
schemas. Cross-file references, such as locale keys and artwork files,
are resolved later by :mod:`profilegen.render`, which is the only place
that sees all inputs together.

Every identifier that ends up in a file name or URL fragment (contact
ids, section ids, artwork keys, skill slugs) is restricted to lowercase
ASCII, and uniqueness is checked here. Two entries mapping to the same
generated file would otherwise overwrite each other silently.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Final, Literal, get_args
from urllib.parse import urlsplit

from profilegen.errors import ConfigError
from profilegen.models import LOGIN_PATTERN, is_slug
from profilegen.validation import Validator, child, display_units, load_json

SectionComponent = Literal["projects", "activity", "statistics", "skills", "knowledge"]
ProjectStatus = Literal["active", "development", "archived"]
Visibility = Literal["public", "private"]

SECTION_COMPONENTS: Final[tuple[SectionComponent, ...]] = get_args(SectionComponent)
PROJECT_STATUSES: Final[tuple[ProjectStatus, ...]] = get_args(ProjectStatus)
VISIBILITIES: Final[tuple[Visibility, ...]] = get_args(Visibility)

MAX_PAGE_SIZE: Final = 12

_IDENTIFIER: Final = re.compile(r"[a-z][a-z0-9]*(?:-[a-z0-9]+)*")
_LOCALE_KEY: Final = re.compile(r"[a-z][a-z0-9_]*")
_PLAIN_SKILL_SLUG: Final = re.compile(r"[A-Za-z0-9]+(?: [A-Za-z0-9]+)*")
_SAFE_WEBP_PATH: Final = re.compile(r"[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)*\.webp")
_EMAIL: Final = re.compile(r"[A-Za-z0-9_.+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}")


@dataclass(frozen=True, slots=True)
class Banner:
    """Header artwork.

    Attributes:
        path: Repository-relative path of the WebP image.
        alt_key: Locale key of the alternative text.
        link: Target opened when the banner is clicked.
    """

    path: PurePosixPath
    alt_key: str
    link: str


@dataclass(frozen=True, slots=True)
class Contact:
    """One contact badge.

    Attributes:
        id: Identifier used as the badge file name.
        label: Text on the badge; not localised, because product names
            such as Telegram are the same in every language.
        mark: One to three characters drawn in the badge's left cell.
        href: ``https://`` or ``mailto:`` target.
        accent: Palette token of the mark and frame brackets.
        enabled: Whether the badge is rendered.
    """

    id: str
    label: str
    mark: str
    href: str
    accent: str
    enabled: bool


@dataclass(frozen=True, slots=True)
class Section:
    """One README section; list order is display order.

    Attributes:
        id: Anchor name and header file name.
        heading_key: Locale key of the heading.
        component: Content rendered below the heading.
        enabled: Whether the section is rendered. Disabled sections
            keep their locale keys referenced, so toggling a section
            never requires editing every catalogue.
    """

    id: str
    heading_key: str
    component: SectionComponent
    enabled: bool


@dataclass(frozen=True, slots=True)
class Project:
    """One catalog card.

    Attributes:
        repository: ``owner/name`` slug.
        description_key: Locale key of the description.
        status: Lifecycle label.
        visibility: ``public`` cards show the live star count;
            ``private`` cards never query GitHub and show a placeholder.
        background_key: File stem of the card artwork and its prompt.
    """

    repository: str
    description_key: str
    status: ProjectStatus
    visibility: Visibility
    background_key: str

    @property
    def title(self) -> str:
        """Repository name without the owner, used as the card title."""
        return self.repository.split("/", 1)[1]


@dataclass(frozen=True, slots=True)
class ProfileConfig:
    """Validated ``profile.json``.

    Attributes:
        username: GitHub login whose public data is displayed.
        banner: Header artwork.
        contacts: Contact badges in display order.
        sections: Sections in display order.
        projects: Catalog cards in display order.
        page_size: Cards shown before the remainder is folded into
            ``<details>`` groups of the same size.
    """

    username: str
    banner: Banner
    contacts: tuple[Contact, ...]
    sections: tuple[Section, ...]
    projects: tuple[Project, ...]
    page_size: int

    def string_keys(self) -> frozenset[str]:
        """Return every locale key referenced by this configuration."""
        return frozenset(
            (
                self.banner.alt_key,
                *(section.heading_key for section in self.sections),
                *(project.description_key for project in self.projects),
            )
        )


@dataclass(frozen=True, slots=True)
class Skill:
    """One skill badge.

    Attributes:
        label: Display name.
        mark: One to three display columns drawn in the badge's left
            cell.
        url: Documentation link opened by the badge.
        id: Optional explicit lowercase slug used as the asset file stem.
    """

    label: str
    mark: str
    url: str
    id: str | None = None

    @property
    def slug(self) -> str:
        """File stem derived from ``id`` or the label, such as ``cpp``."""
        return self.id if self.id is not None else self.label.lower().replace(" ", "-")


@dataclass(frozen=True, slots=True)
class SkillGroup:
    """Skills shown under one localised heading.

    Attributes:
        heading_key: Locale key of the heading.
        skills: Badges in display order.
    """

    heading_key: str
    skills: tuple[Skill, ...]


def https_url(v: Validator, value: object, where: str) -> str:
    """Validate an absolute ``https://`` URL without credentials.

    Args:
        v: Validator bound to the containing document.
        value: Raw decoded value.
        where: JSON path of the value.

    Returns:
        The URL.

    Raises:
        ConfigError: If the value is not such a URL.
    """
    url = v.string(value, where, max_length=300)
    if any(char.isspace() for char in url):
        v.fail(where, "URL must not contain whitespace")
    parts = urlsplit(url)
    if parts.scheme != "https" or not parts.hostname or "@" in parts.netloc:
        v.fail(where, "expected an https:// URL without credentials")
    return url


def _contact_href(v: Validator, value: object, where: str) -> str:
    href = v.string(value, where, max_length=300)
    if href.startswith("mailto:"):
        if _EMAIL.fullmatch(href.removeprefix("mailto:")) is None:
            v.fail(where, "expected mailto: followed by one plain address")
        return href
    return https_url(v, href, where)


def _mark(v: Validator, value: object, where: str) -> str:
    mark = v.string(value, where, max_length=3)
    if display_units(mark) > 3:
        v.fail(where, "mark exceeds 3 display columns")
    return mark


def _unique(v: Validator, values: list[str], where: str, what: str) -> None:
    seen: set[str] = set()
    for value in values:
        if value in seen:
            v.fail(where, f"duplicate {what} {value!r}")
        seen.add(value)


def load_profile(path: Path, *, source: str) -> ProfileConfig:
    """Load and validate ``profile.json``.

    Args:
        path: Location of the file.
        source: Repository-relative name used in error messages.

    Returns:
        The validated configuration.

    Raises:
        ConfigError: If the file is unreadable or violates the schema.
    """
    v = Validator(source, ConfigError)
    data = v.record(
        load_json(path, source=source, error=ConfigError),
        "",
        required=("identity", "contacts", "layout", "catalog"),
    )
    identity = v.record(data["identity"], "identity", required=("username", "banner"))
    banner = v.record(
        identity["banner"], "identity.banner", required=("asset_path", "alt_key", "link_target")
    )
    raw_banner = v.string(
        banner["asset_path"],
        "identity.banner.asset_path",
        pattern=_SAFE_WEBP_PATH,
        max_length=200,
    )
    banner_path = PurePosixPath(raw_banner)
    if any(part in {".", ".."} for part in banner_path.parts):
        v.fail("identity.banner.asset_path", "expected a relative path without '.' or '..'")

    contacts: list[Contact] = []
    for index, item in enumerate(v.array(data["contacts"], "contacts", max_items=8)):
        where = child("contacts", index)
        entry = v.record(
            item,
            where,
            required=("id", "label", "mark", "href", "accent"),
            optional=("enabled",),
        )
        contacts.append(
            Contact(
                id=v.string(entry["id"], child(where, "id"), pattern=_IDENTIFIER, max_length=32),
                label=v.string(entry["label"], child(where, "label"), max_length=24),
                mark=_mark(v, entry["mark"], child(where, "mark")),
                href=_contact_href(v, entry["href"], child(where, "href")),
                accent=v.string(entry["accent"], child(where, "accent"), pattern=_LOCALE_KEY),
                enabled=v.boolean(entry.get("enabled", True), child(where, "enabled")),
            )
        )
    _unique(v, [contact.id for contact in contacts], "contacts", "contact id")

    layout = v.record(data["layout"], "layout", required=("sections",))
    sections: list[Section] = []
    for index, item in enumerate(v.array(layout["sections"], "layout.sections", min_items=1)):
        where = child("layout.sections", index)
        entry = v.record(
            item, where, required=("id", "heading_key", "component"), optional=("enabled",)
        )
        sections.append(
            Section(
                id=v.string(entry["id"], child(where, "id"), pattern=_IDENTIFIER, max_length=32),
                heading_key=v.string(
                    entry["heading_key"], child(where, "heading_key"), pattern=_LOCALE_KEY
                ),
                component=v.choice(
                    entry["component"], child(where, "component"), SECTION_COMPONENTS
                ),
                enabled=v.boolean(entry.get("enabled", True), child(where, "enabled")),
            )
        )
    _unique(v, [section.id for section in sections], "layout.sections", "section id")
    _unique(v, [section.component for section in sections], "layout.sections", "component")

    catalog = v.record(data["catalog"], "catalog", required=("page_size", "projects"))
    projects: list[Project] = []
    for index, item in enumerate(v.array(catalog["projects"], "catalog.projects", min_items=1)):
        where = child("catalog.projects", index)
        entry = v.record(
            item,
            where,
            required=("repository", "description_key", "status", "visibility", "background_key"),
        )
        repository = v.string(entry["repository"], child(where, "repository"), max_length=201)
        if not is_slug(repository):
            v.fail(child(where, "repository"), f"{repository!r} is not a repository slug")
        projects.append(
            Project(
                repository=repository,
                description_key=v.string(
                    entry["description_key"], child(where, "description_key"), pattern=_LOCALE_KEY
                ),
                status=v.choice(entry["status"], child(where, "status"), PROJECT_STATUSES),
                visibility=v.choice(entry["visibility"], child(where, "visibility"), VISIBILITIES),
                background_key=v.string(
                    entry["background_key"],
                    child(where, "background_key"),
                    pattern=_IDENTIFIER,
                    max_length=64,
                ),
            )
        )
    _unique(
        v,
        [project.repository.casefold() for project in projects],
        "catalog.projects",
        "repository",
    )

    return ProfileConfig(
        username=v.string(identity["username"], "identity.username", pattern=LOGIN_PATTERN),
        banner=Banner(
            path=banner_path,
            alt_key=v.string(banner["alt_key"], "identity.banner.alt_key", pattern=_LOCALE_KEY),
            link=https_url(v, banner["link_target"], "identity.banner.link_target"),
        ),
        contacts=tuple(contacts),
        sections=tuple(sections),
        projects=tuple(projects),
        page_size=v.integer(
            catalog["page_size"], "catalog.page_size", minimum=1, maximum=MAX_PAGE_SIZE
        ),
    )


def load_skills(path: Path, *, source: str) -> tuple[SkillGroup, ...]:
    """Load and validate ``skills.json``.

    The document maps a locale heading key to an array of skills; JSON
    object order is display order.

    Args:
        path: Location of the file.
        source: Repository-relative name used in error messages.

    Returns:
        Skill groups in display order.

    Raises:
        ConfigError: If the file is unreadable, violates the schema or
            two skills share a slug.
    """
    v = Validator(source, ConfigError)
    groups: list[SkillGroup] = []
    for key, items in v.mapping(load_json(path, source=source, error=ConfigError), "").items():
        if _LOCALE_KEY.fullmatch(key) is None:
            v.fail(key, "group name must be a locale key")
        skills: list[Skill] = []
        for index, item in enumerate(v.array(items, key, min_items=1, max_items=24)):
            where = child(key, index)
            entry = v.record(item, where, required=("label", "mark", "url"), optional=("id",))
            label = v.string(entry["label"], child(where, "label"), max_length=32)
            if label != label.strip():
                v.fail(child(where, "label"), "unexpected leading or trailing spaces")
            raw_id = entry.get("id")
            if raw_id is None:
                if _PLAIN_SKILL_SLUG.fullmatch(label) is None:
                    v.fail(
                        child(where, "label"),
                        "label requires an explicit lowercase 'id' slug",
                    )
                skill_id = None
            else:
                skill_id = v.string(raw_id, child(where, "id"), pattern=_IDENTIFIER, max_length=32)
            skills.append(
                Skill(
                    label=label,
                    mark=_mark(v, entry["mark"], child(where, "mark")),
                    url=https_url(v, entry["url"], child(where, "url")),
                    id=skill_id,
                )
            )
        groups.append(SkillGroup(heading_key=key, skills=tuple(skills)))
    if not groups:
        v.fail("", "expected at least one skill group")
    _unique(v, [skill.slug for group in groups for skill in group.skills], "", "skill")
    return tuple(groups)
