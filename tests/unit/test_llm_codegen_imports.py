"""Tests for `drop_redundant_imports` and `import_error_hint` (agents/_llm_codegen.py).

Pure functions: no sandbox, no model. The behaviour they protect was found in real
runs on 2026-10-12: analysis code that began with `import pandas as pd` failed every
attempt, because the sandbox has no import mechanism.
"""

from ai_etl.agents._llm_codegen import (
    IMPORT_ERROR_HINT,
    drop_redundant_imports,
    import_error_hint,
)

PROVIDED = {
    "pd": "pandas",
    "np": "numpy",
    "px": "plotly",
    "go": "plotly",
    "math": "math",
    "LinearRegression": "sklearn",
}


def test_drops_the_imports_seen_in_the_real_failure() -> None:
    code = (
        "import pandas as pd\n"
        "import plotly.express as px\n"
        "\n"
        "gold_df = df.groupby('hour')['total_amount'].mean().reset_index()\n"
        "fig = px.bar(gold_df, x='hour', y='total_amount')\n"
    )
    cleaned = drop_redundant_imports(code, PROVIDED)
    assert "import" not in cleaned
    assert cleaned.startswith("gold_df = df.groupby")
    assert "fig = px.bar" in cleaned


def test_drops_from_imports_and_plain_module_imports_that_are_provided() -> None:
    code = (
        "from sklearn.linear_model import LinearRegression\n"
        "import plotly.graph_objects as go\n"
        "import numpy as np, math\n"
        "model = LinearRegression()\n"
    )
    assert drop_redundant_imports(code, PROVIDED) == "model = LinearRegression()"


def test_keeps_an_import_of_something_the_sandbox_does_not_provide() -> None:
    code = "import requests\nfrom datetime import datetime\nx = 1\n"
    assert drop_redundant_imports(code, PROVIDED) == code


def test_keeps_an_import_that_binds_a_provided_name_to_another_package() -> None:
    code = "import polars as pd\nx = 1\n"
    assert drop_redundant_imports(code, PROVIDED) == code


def test_keeps_an_import_that_binds_a_name_not_provided() -> None:
    # `import pandas` binds `pandas`, and `import plotly.express` binds `plotly`:
    # neither name exists in the sandbox, so removing the line would not help.
    code = "import pandas\nimport plotly.express\nx = 1\n"
    assert drop_redundant_imports(code, PROVIDED) == code


def test_keeps_a_statement_when_one_of_its_names_is_not_provided() -> None:
    code = "from sklearn.linear_model import LinearRegression, Lasso\nx = 1\n"
    assert drop_redundant_imports(code, PROVIDED) == code


def test_leaves_a_line_shared_with_another_statement_alone() -> None:
    code = "import pandas as pd; x = 1\ny = 2\n"
    assert drop_redundant_imports(code, PROVIDED) == code


def test_handles_an_import_split_over_several_lines() -> None:
    code = "from sklearn.linear_model import (\n    LinearRegression,\n)\nx = 1\n"
    assert drop_redundant_imports(code, PROVIDED) == "x = 1"


def test_does_not_touch_imports_inside_a_function() -> None:
    code = "def f():\n    import pandas as pd\n    return pd\n"
    assert drop_redundant_imports(code, PROVIDED) == code


def test_returns_code_that_does_not_parse_unchanged() -> None:
    code = "import pandas as pd\ndef broken(:\n"
    assert drop_redundant_imports(code, PROVIDED) == code


def test_returns_code_without_imports_unchanged() -> None:
    code = "gold_df = df.head()\n"
    assert drop_redundant_imports(code, PROVIDED) == code


def test_import_error_hint_only_for_the_sandbox_import_error() -> None:
    assert import_error_hint("ImportError: __import__ not found") == IMPORT_ERROR_HINT
    assert import_error_hint("KeyError: 'revenue'") == ""
