# uv run pytest -s chatbot/api/tests/test_survey_feedback.py

from chatbot.api.utils.survey_feedback import extract_survey_feedback


def test_extract_survey_feedback_with_explicit_rating_and_comment() -> None:
    result = extract_survey_feedback("5 Excelente experiencia, muy recomendable")

    assert result is not None
    assert result.rating == 5
    assert result.comment == "Excelente experiencia, muy recomendable"


def test_extract_survey_feedback_infers_rating_from_comment() -> None:
    result = extract_survey_feedback("Me encantó, estuvo espectacular")

    assert result is not None
    assert result.rating == 5
    assert result.comment == "Me encantó, estuvo espectacular"


def test_extract_survey_feedback_returns_none_for_non_feedback_message() -> None:
    result = extract_survey_feedback("Necesito cambiar mi reserva para mañana")

    assert result is None
