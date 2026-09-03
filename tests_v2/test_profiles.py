from papercraft.profiles import default_profile_registry


def test_registry_resolves_every_supported_profile() -> None:
    registry = default_profile_registry()
    assert registry.resolve("coursework_it").id == "coursework"
    finance = registry.resolve("coursework", "finance")
    assert finance.id == "coursework"
    assert finance.policy.allow_synthetic_data
    assert any("CalculationResult" in rule for rule in finance.prompt_rules)
    assert registry.resolve("coursework", "general").id == "coursework"
    assert registry.resolve("scientific_article").id == "scientific_article"
    assert registry.resolve("practice_report", "it").id == "practice_report"
    assert registry.resolve("industrial_report", "finance").id == "practice_report"
    assert registry.resolve("school_project").id == "school_project"
    assert registry.resolve("unknown").id == "coursework"


def test_profiles_have_unique_sections_and_required_policies() -> None:
    profiles = default_profile_registry().all()
    assert {profile.id for profile in profiles} == {
        "coursework",
        "scientific_article",
        "practice_report",
        "school_project",
    }
    for profile in profiles:
        assert profile.sections
        assert len({section.key for section in profile.sections}) == len(profile.sections)
        assert profile.policy.section_tolerance_fraction == 0.10
