"""Tests for `hand_typed_figures` (agents/_llm_codegen.py): figures written as text in
the narrative of a sub-analysis. Pure function, no sandbox, no model.

Real run in production, 2026-10-12: the text of a diagnostic sub-analysis said "sem
parceiro 30,5%, com parceiro 23,5%"; the table computed by the same code said 33.0 and
19.7.
"""

from ai_etl.agents._llm_codegen import hand_typed_figures, typed_figures_error


def test_reports_the_figures_of_the_real_failure() -> None:
    code = (
        "predictions_df = df.groupby('Partner')['churn_flag'].mean().reset_index()\n"
        'narrative = "A taxa de cancelamento é de 26,6% entre os homens e 27,5% entre as "'
        ' "mulheres. Sem parceiro: 30,5%."\n'
    )
    assert hand_typed_figures(code) == ["26,6", "27,5", "30,5"]


def test_reports_a_percentage_and_a_decimal_typed_inside_an_f_string() -> None:
    code = 'narrative = f"Homens: {male:.1f}%, mulheres: 27.5% e 62% no primeiro mês"\n'
    assert hand_typed_figures(code) == ["27.5", "62%"]


def test_accepts_a_narrative_whose_figures_all_come_from_fields() -> None:
    code = (
        "rate = gold_df['churn_rate'].max() * 100\n"
        'narrative = f"A maior taxa é {rate:.2f}% ({top}), entre {n} métodos."\n'
    )
    assert hand_typed_figures(code) == []


def test_accepts_format_and_percent_formatting() -> None:
    code = (
        'narrative = "Taxa de {:.1f}% em {} grupos".format(rate, n)\n'
        'narrative += " e média de %.2f" % mean\n'
    )
    assert hand_typed_figures(code) == []


def test_accepts_whole_numbers_without_a_percent_sign() -> None:
    code = 'narrative = "Os 5 principais produtos de 2025 em 12 meses"\n'
    assert hand_typed_figures(code) == []


def test_sees_concatenation_join_augmented_and_conditional_assignments() -> None:
    code = (
        'narrative = "Parte A: " + "10%"\n'
        'narrative += " ".join(["cresce", "3,5", "pontos"])\n'
        'narrative = "alta de 2.5" if up else f"queda de {x}"\n'
    )
    assert hand_typed_figures(code) == ["10%", "3,5", "2.5"]


def test_ignores_other_variables_and_code_that_does_not_parse() -> None:
    assert hand_typed_figures('title = "Top 10% de clientes"\nnarrative = f"{a}"\n') == []
    assert hand_typed_figures('narrative = "45.2%"\ndef broken(:\n') == []


def test_error_message_names_the_figures_and_the_data() -> None:
    message = typed_figures_error(["26,6", "27,5"], "predictions_df")
    assert "26,6, 27,5" in message
    assert "predictions_df" in message
    assert "f-string" in message
