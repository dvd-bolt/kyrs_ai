from __future__ import annotations

from collections.abc import Iterable
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator


class ProfileSectionTemplate(BaseModel):
    key: str
    title: str
    level: int = Field(default=1, ge=1, le=3)
    target_words: int = Field(default=700, ge=100, le=20_000)
    purpose: str
    required: bool = True


class ProfilePolicy(BaseModel):
    voice: str
    required_artifacts: list[str] = Field(default_factory=list)
    source_priorities: list[str] = Field(default_factory=list)
    allow_synthetic_data: bool = False
    synthetic_data_disclosure: Literal["document_and_internal"] = "document_and_internal"
    require_real_organisation_facts: bool = False
    minimum_sources: int = Field(default=10, ge=0, le=200)
    section_tolerance_fraction: float = Field(default=0.10, ge=0, le=0.5)


class WorkProfile(BaseModel):
    id: str
    version: str = "2026-08"
    display_name: str
    work_type: str
    domain_tags: list[str] = Field(default_factory=list)
    description: str
    sections: list[ProfileSectionTemplate]
    policy: ProfilePolicy
    prompt_rules: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def unique_section_keys(self) -> WorkProfile:
        keys = [section.key for section in self.sections]
        if len(keys) != len(set(keys)):
            raise ValueError("Profile section keys must be unique")
        return self


class ProfileRegistry:
    def __init__(self, profiles: Iterable[WorkProfile]) -> None:
        self._profiles = {profile.id: profile for profile in profiles}
        if not self._profiles:
            raise ValueError("At least one work profile is required")

    @staticmethod
    def _value(value: Any) -> str:
        return str(getattr(value, "value", value)).strip().lower()

    def get(self, profile_id: str) -> WorkProfile:
        try:
            return self._profiles[profile_id]
        except KeyError as exc:
            raise KeyError(f"Unknown PaperCraft profile: {profile_id}") from exc

    def all(self) -> list[WorkProfile]:
        return sorted(self._profiles.values(), key=lambda item: item.display_name)

    def resolve(self, work_type: Any, domain_profile: Any = "general") -> WorkProfile:
        work = self._value(work_type)
        domain = self._value(domain_profile)
        aliases = {
            "coursework_it": ("coursework", "it"),
            "coursework_finance": ("coursework", "finance"),
            "scientific_article": ("scientific_article", "general"),
            "school_project": ("school_project", "general"),
            "practice_report": ("practice_report", domain),
            "lab_report": ("practice_report", "it"),
            "industrial_report": ("practice_report", domain),
        }
        work, alias_domain = aliases.get(work, (work, domain))
        domain = domain if domain not in {"", "general", "none"} else alias_domain

        candidates = [profile for profile in self._profiles.values() if profile.work_type == work]
        for profile in candidates:
            if domain in profile.domain_tags:
                return profile
        if candidates:
            return candidates[0]
        return self.get("universal")


def _section(
    key: str, title: str, words: int, purpose: str, level: int = 1
) -> ProfileSectionTemplate:
    return ProfileSectionTemplate(
        key=key, title=title, target_words=words, purpose=purpose, level=level
    )


def default_profile_registry() -> ProfileRegistry:
    common_sources = [
        "official and normative sources",
        "primary research",
        "publisher or university publications",
        "verified technical documentation",
    ]
    profiles = [
        WorkProfile(
            id="coursework_it",
            display_name="Курсовая — IT и программирование",
            work_type="coursework",
            domain_tags=["it", "programming", "software"],
            description="Теория, анализ исходного кода, проектирование, реализация и тестирование.",
            sections=[
                _section("introduction", "ВВЕДЕНИЕ", 700, "Обосновать актуальность, цель и задачи"),
                _section("analysis", "АНАЛИЗ ПРЕДМЕТНОЙ ОБЛАСТИ", 2200, "Исследовать процессы и требования"),
                _section("design", "ПРОЕКТИРОВАНИЕ СИСТЕМЫ", 2500, "Описать архитектуру, данные и алгоритмы"),
                _section("implementation", "РЕАЛИЗАЦИЯ И ТЕСТИРОВАНИЕ", 2800, "Разобрать код и результаты испытаний"),
                _section("conclusion", "ЗАКЛЮЧЕНИЕ", 700, "Сопоставить результаты с задачами"),
                _section("bibliography", "СПИСОК ИСПОЛЬЗОВАННЫХ ИСТОЧНИКОВ", 100, "Перечислить использованные источники"),
                _section("appendix", "ПРИЛОЖЕНИЯ", 100, "Разместить листинги и дополнительные материалы"),
            ],
            policy=ProfilePolicy(
                voice="formal impersonal technical Russian",
                required_artifacts=["architecture diagram", "data model", "code listings", "test table"],
                source_priorities=common_sources,
                minimum_sources=12,
            ),
            prompt_rules=["Describe only code that exists in imported sources", "Link every listing to its source path and lines"],
        ),
        WorkProfile(
            id="coursework_finance",
            display_name="Курсовая — финансы и бухгалтерский учёт",
            work_type="coursework",
            domain_tags=["finance", "accounting", "economics"],
            description="Теория, проверяемые показатели, бухгалтерские проводки и финансовый анализ.",
            sections=[
                _section("introduction", "ВВЕДЕНИЕ", 700, "Определить цель, задачи, объект и информационную базу"),
                _section("theory", "ТЕОРЕТИЧЕСКИЕ ОСНОВЫ", 2300, "Раскрыть нормативную и методическую базу"),
                _section("analysis", "ОРГАНИЗАЦИОННО-ЭКОНОМИЧЕСКИЙ АНАЛИЗ", 2800, "Рассчитать и интерпретировать показатели"),
                _section("recommendations", "СОВЕРШЕНСТВОВАНИЕ УЧЁТА И АНАЛИЗА", 2200, "Разработать проверяемые предложения"),
                _section("conclusion", "ЗАКЛЮЧЕНИЕ", 700, "Сопоставить выводы с задачами"),
                _section("bibliography", "СПИСОК ИСПОЛЬЗОВАННЫХ ИСТОЧНИКОВ", 100, "Перечислить использованные источники"),
                _section("appendix", "ПРИЛОЖЕНИЯ", 100, "Разместить отчётность и расчётные таблицы"),
            ],
            policy=ProfilePolicy(
                voice="formal impersonal financial Russian",
                required_artifacts=["journal entries", "account balances", "three-year tables", "charts"],
                source_priorities=common_sources,
                minimum_sources=15,
            ),
            prompt_rules=["All monetary values must originate in the fact ledger", "Never invent a legal citation"],
        ),
        WorkProfile(
            id="coursework_general",
            display_name="Курсовая — общая тематика",
            work_type="coursework",
            domain_tags=["general", "science", "universal"],
            description="Универсальная исследовательская курсовая с теорией, анализом и практическими предложениями.",
            sections=[
                _section("introduction", "ВВЕДЕНИЕ", 700, "Обосновать актуальность, цель, задачи, объект и предмет"),
                _section("theory", "ТЕОРЕТИЧЕСКИЕ ОСНОВЫ", 2400, "Систематизировать проверенные подходы и понятия"),
                _section("analysis", "АНАЛИТИЧЕСКАЯ ЧАСТЬ", 2600, "Проанализировать проблему на фактах и источниках"),
                _section("practice", "ПРАКТИЧЕСКАЯ ЧАСТЬ", 2400, "Разработать и оценить практическое решение"),
                _section("conclusion", "ЗАКЛЮЧЕНИЕ", 700, "Сопоставить результаты с целью и задачами"),
                _section("bibliography", "СПИСОК ИСПОЛЬЗОВАННЫХ ИСТОЧНИКОВ", 100, "Перечислить только использованные источники"),
                _section("appendix", "ПРИЛОЖЕНИЯ", 100, "Разместить дополнительные материалы"),
            ],
            policy=ProfilePolicy(
                voice="formal evidence-led academic Russian",
                required_artifacts=["evidence table", "analytical table"],
                source_priorities=common_sources,
                minimum_sources=12,
            ),
            prompt_rules=["Separate established facts from interpretation", "Tie every conclusion to a stated task"],
        ),
        WorkProfile(
            id="scientific_article",
            display_name="Научная статья",
            work_type="scientific_article",
            domain_tags=["general"],
            description="Студенческая научная статья по ГОСТ Р 7.0.7-2021 и регламенту научки.md.",
            sections=[
                _section("abstract", "АННОТАЦИЯ", 150, "Кратко изложить цель, метод, результат и вывод (рус/англ)"),
                _section("introduction", "ВВЕДЕНИЕ", 300, "Описать актуальность, цель, объект и научную проблему"),
                _section("theory", "ТЕОРЕТИЧЕСКИЕ ОСНОВЫ", 500, "Рассмотреть теоретические подходы и научный контекст"),
                _section("practical", "ПРАКТИЧЕСКАЯ ЧАСТЬ И РАСЧЕТЫ", 700, "Представить эмпирический анализ, расчеты, таблицы и графики"),
                _section("conclusion", "ЗАКЛЮЧЕНИЕ", 450, "Сформулировать итоги, научную новизну и выводы"),
                _section("bibliography", "СПИСОК ЛИТЕРАТУРЫ", 100, "Перечислить цитируемые источники по ГОСТ"),
            ],
            policy=ProfilePolicy(
                voice="student researcher impersonal Russian with natural rhythm and no AI cliches",
                required_artifacts=["evidence table"],
                source_priorities=common_sources,
                minimum_sources=8,
            ),
            prompt_rules=[
                "Apply ГОСТ Р 7.0.7-2021, ГОСТ Р 7.0.99-2018, ГОСТ Р 7.0.5-2008 as the base",
                "Persona: студент — автор научной статьи. Писать только в безличной форме («было рассмотрено», «проанализировано», «показано»). Запрещено использовать местоимения «я» и «мы».",
                "Запрещено слово «Таким образом» — заменять на «в итоге», «вследствие этого», «это позволяет», «в результате».",
                "Запрещены клише: «важно отметить», «следует подчеркнуть», «в современном мире», «безусловно», «в заключение», «с одной стороны... с другой стороны».",
                "Burstiness & Perplexity: чередовать длинные сложные предложения (20-30 слов) с короткими рублеными (3-5 слов). Избегать шаблонных микро-выводов в конце каждого абзаца. Подавать материал связным нарративом.",
                "Include author details, UDC, Russian and English title, abstract (120-180 words) and keywords (5-8 words).",
                "Include practical empirical numbers, calculations, tables and charts. Synthetic demo data must be disclosed.",
            ],
        ),
        WorkProfile(
            id="practice_report",
            display_name="Отчёт по практике или лабораторной работе",
            work_type="practice_report",
            domain_tags=["general", "it", "finance"],
            description="Описание организации, выполненных этапов, реализации и результатов.",
            sections=[
                _section("introduction", "ВВЕДЕНИЕ", 600, "Определить цель и задачи практики"),
                _section("organisation", "ХАРАКТЕРИСТИКА БАЗЫ ПРАКТИКИ", 1200, "Описать подтверждённые сведения об организации"),
                _section("work", "ВЫПОЛНЕННЫЕ РАБОТЫ", 3000, "Описать этапы и созданные результаты"),
                _section("results", "ОЦЕНКА РЕЗУЛЬТАТОВ", 1500, "Проверить и интерпретировать результат"),
                _section("conclusion", "ЗАКЛЮЧЕНИЕ", 600, "Подвести итог выполнения задач"),
                _section("bibliography", "СПИСОК ИСПОЛЬЗОВАННЫХ ИСТОЧНИКОВ", 100, "Перечислить использованные источники"),
                _section("appendix", "ПРИЛОЖЕНИЯ", 100, "Разместить дневник, документы и листинги"),
            ],
            policy=ProfilePolicy(
                voice="formal impersonal report Russian",
                required_artifacts=["work log", "result evidence"],
                source_priorities=common_sources,
                require_real_organisation_facts=True,
                minimum_sources=8,
            ),
            prompt_rules=["Organisation and placement facts must come from user files or verified sources"],
        ),
        WorkProfile(
            id="school_project",
            display_name="Школьный проект",
            work_type="school_project",
            domain_tags=["general"],
            description="Понятный, доказательный проект с теоретической и практической частями.",
            sections=[
                _section("introduction", "ВВЕДЕНИЕ", 500, "Определить проблему, цель, задачи и гипотезу"),
                _section("theory", "ТЕОРЕТИЧЕСКАЯ ЧАСТЬ", 1500, "Объяснить основные понятия доступным языком"),
                _section("practice", "ПРАКТИЧЕСКАЯ ЧАСТЬ", 1800, "Описать проект, опрос или эксперимент"),
                _section("conclusion", "ЗАКЛЮЧЕНИЕ", 500, "Оценить гипотезу и выполнение задач"),
                _section("bibliography", "СПИСОК ЛИТЕРАТУРЫ", 100, "Перечислить использованные источники"),
                _section("appendix", "ПРИЛОЖЕНИЯ", 100, "Разместить анкету и дополнительные материалы"),
            ],
            policy=ProfilePolicy(
                voice="clear literate Russian suitable for grades 9-11",
                required_artifacts=["practical result", "data table", "chart"],
                source_priorities=common_sources,
                minimum_sources=9,
            ),
            prompt_rules=["Avoid unnecessary jargon", "Keep the practical method internally reproducible"],
        ),
        WorkProfile(
            id="universal",
            display_name="Универсальная работа",
            work_type="universal",
            domain_tags=["general"],
            description="Структура полностью определяется методичкой и заданием.",
            sections=[
                _section("introduction", "ВВЕДЕНИЕ", 600, "Определить цель и задачи"),
                _section("main", "ОСНОВНАЯ ЧАСТЬ", 4000, "Раскрыть тему по требованиям"),
                _section("conclusion", "ЗАКЛЮЧЕНИЕ", 600, "Подвести итог"),
                _section("bibliography", "СПИСОК ИСПОЛЬЗОВАННЫХ ИСТОЧНИКОВ", 100, "Перечислить источники"),
            ],
            policy=ProfilePolicy(
                voice="formal impersonal Russian",
                source_priorities=common_sources,
                minimum_sources=10,
            ),
        ),
    ]
    return ProfileRegistry(profiles)
