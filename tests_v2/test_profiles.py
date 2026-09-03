from papercraft.profiles import default_profile_registry


def test_registry_resolves_every_supported_profile() -> None:
    registry = default_profile_registry()
    assert registry.resolve("coursework_it").id == "coursework_it"
    finance = registry.resolve("coursework", "finance")
    assert finance.id == "coursework_finance"
    assert finance.policy.allow_synthetic_data
    assert any("CalculationResult" in rule for rule in finance.prompt_rules)
    assert registry.resolve("coursework", "general").id == "coursework_general"
    assert registry.resolve("scientific_article").id == "scientific_article"
    assert registry.resolve("practice_report", "it").id == "practice_report"
    assert registry.resolve("industrial_report", "finance").id == "practice_report"
    assert registry.resolve("school_project").id == "school_project"
    assert registry.resolve("unknown").id == "universal"


def test_profiles_have_unique_sections_and_required_policies() -> None:
    for profile in default_profile_registry().all():
        assert profile.sections
        assert len({section.key for section in profile.sections}) == len(profile.sections)
        assert profile.policy.section_tolerance_fraction == 0.10
