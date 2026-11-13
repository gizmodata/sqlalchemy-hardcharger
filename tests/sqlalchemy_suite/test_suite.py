"""SQLAlchemy's dialect compliance suite, as it ships with the installed SQLAlchemy.

Every test runs unless ``sqlalchemy_hardcharger.requirements`` says Hardcharger lacks the feature
it needs. The few tests below are switched off one by one, each for a stated property of DuckDB
that the test's assertion can't hold under; ``tests/integration`` checks the same behaviour with
what DuckDB really returns.
"""

from sqlalchemy.testing import config
from sqlalchemy.testing import suite as _suite
from sqlalchemy.testing.suite import *  # noqa: F401, F403

NAMES = (
    "DuckDB names constraints itself (<table>_<columns>_pkey, _key, _fkey, _check) and doesn't "
    "keep the name a constraint was declared with, which this test reads back"
)
LENGTHS = "DuckDB's VARCHAR has no length (VARCHAR(50) and CHAR(50) are stored as VARCHAR)"


def _unsupported(reason):  # noqa: ANN001, ANN202
    def test(self):  # noqa: ANN001, ANN202
        config.skip_test(reason)

    return test


class ComponentReflectionTest(_suite.ComponentReflectionTest):
    test_get_foreign_keys = _unsupported(NAMES)
    test_get_foreign_keys_quoted_name = _unsupported(NAMES)
    test_get_multi_foreign_keys = _unsupported(NAMES)
    test_get_unique_constraints = _unsupported(NAMES)
    test_get_unique_constraints_quoted_name = _unsupported(NAMES)
    test_get_multi_unique_constraints = _unsupported(NAMES)
    test_get_temp_table_unique_constraints = _unsupported(NAMES)
    test_get_multi_check_constraints = _unsupported(NAMES)
    test_reflect_table_temp_table = _unsupported(LENGTHS)


class ComponentReflectionTestExtra(_suite.ComponentReflectionTestExtra):
    test_check_constraint_inline = _unsupported(NAMES)
    test_check_constraint_standalone = _unsupported(NAMES)
    test_check_constraint_mixed = _unsupported(NAMES)
    test_check_constraint_parenthesized_expressions = _unsupported(NAMES)
    test_string_length_reflection = _unsupported(LENGTHS)
    test_reflect_expression_based_indexes = _unsupported(
        "DuckDB stores an index expression in its own spelling (COALESCE in capitals), and "
        "this test compares the text exactly"
    )


class LongNameBlowoutTest(_suite.LongNameBlowoutTest):
    test_long_convention_name = _unsupported(NAMES)


class IsolationLevelTest(_suite.IsolationLevelTest):
    test_dialect_user_setting_is_restored = _unsupported(
        "the test needs two transactional isolation levels; DuckDB has one (snapshot)"
    )


class WindowFunctionTest(_suite.WindowFunctionTest):
    _ORDER = (
        "the test reads a window query's rows back in table order without an ORDER BY, an "
        "order DuckDB doesn't promise"
    )
    test_window_range = _unsupported(_ORDER)
    test_window_range_numeric = _unsupported(_ORDER)
