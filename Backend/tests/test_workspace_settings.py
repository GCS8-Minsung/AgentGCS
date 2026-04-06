from app.routers.workspace import _normalize_settings


def test_normalize_settings_clamps_discussion_rounds():
    normalized_low = _normalize_settings({"discussion_rounds": 0})
    normalized_high = _normalize_settings({"discussion_rounds": 99})
    assert normalized_low["discussion_rounds"] == 2
    assert normalized_high["discussion_rounds"] == 5


def test_normalize_settings_restores_default_persona_and_caps_to_six():
    raw_personas = [
        {"id": f"p{i}", "name": f"Persona {i}", "stats": {"creativity": 70, "logic": 70, "critical_thinking": 70, "data_dependency": 70, "cautiousness": 70, "drive": 70}}
        for i in range(1, 10)
    ]
    normalized = _normalize_settings(
        {
            "personas": raw_personas,
            "active_persona_id": "p9",
        }
    )
    persona_ids = [row["id"] for row in normalized["personas"]]
    assert persona_ids[0] == "default-balanced"
    assert len(persona_ids) == 6
    assert "p9" not in persona_ids
    assert normalized["active_persona_id"] == "default-balanced"
