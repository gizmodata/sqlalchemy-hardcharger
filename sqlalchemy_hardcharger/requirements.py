"""What Hardcharger supports, for SQLAlchemy's dialect compliance suite
(``sqlalchemy.testing.suite``, run by ``pytest tests/sqlalchemy_suite``).

Each property the suite leaves closed by default and Hardcharger supports is opened here; each
it leaves open and Hardcharger (DuckDB 2.0) does not support is closed, with the reason.
"""

from sqlalchemy.testing import exclusions
from sqlalchemy.testing.requirements import SuiteRequirements


class Requirements(SuiteRequirements):
    # -- open: supported -----------------------------------------------------------------------

    def _open(self):  # noqa: ANN202
        return exclusions.open()

    create_table_as = property(_open)
    create_temp_table_as = property(_open)
    table_ddl_if_exists = property(_open)
    index_ddl_if_exists = property(_open)
    uuid_data_type = property(_open)
    table_value_constructor = property(_open)
    boolean_col_expressions = property(_open)
    nullsordering = property(_open)
    intersect = property(_open)
    except_ = property(_open)
    window_functions = property(_open)
    ctes = property(_open)
    ctes_with_update_delete = property(_open)
    ctes_with_values = property(_open)
    ctes_on_dml = property(_open)
    tuple_in = property(_open)
    views = property(_open)
    schemas = property(_open)
    implicit_default_schema = property(_open)
    schema_create_delete = property(_open)
    comment_reflection = property(_open)
    comment_reflection_full_unicode = property(_open)
    temp_table_names = property(_open)
    has_temp_table = property(_open)
    temporary_views = property(_open)
    create_or_replace_view = property(_open)
    check_constraint_reflection = property(_open)
    inline_check_constraint_reflection = property(_open)
    unicode_ddl = property(_open)
    datetime_interval = property(_open)
    datetime_literals = property(_open)
    datetime_timezone = property(_open)
    timestamp_microseconds = property(_open)
    datetime_historic = property(_open)
    date_historic = property(_open)
    autocommit = property(_open)
    isolation_level = property(_open)
    array_type = property(_open)
    json_type = property(_open)
    server_defaults = property(_open)
    expression_server_defaults = property(_open)
    aggregate_order_by = property(_open)
    precision_numerics_retains_significant_digits = property(_open)
    infinity_floats = property(_open)
    update_from = property(_open)
    delete_from = property(_open)
    mod_operator_as_percent_sign = property(_open)
    order_by_label_with_expression = property(_open)
    computed_columns = property(_open)
    computed_columns_virtual = property(_open)
    computed_columns_reflect_persisted = property(_open)
    supports_distinct_on = property(_open)
    regexp_match = property(_open)
    regexp_replace = property(_open)
    fetch_first = property(_open)
    fetch_no_order_by = property(_open)
    supports_bitwise_or = property(_open)
    supports_bitwise_and = property(_open)
    supports_bitwise_not = property(_open)
    supports_bitwise_xor = property(_open)
    supports_bitwise_shift = property(_open)
    reflect_indexes_with_expressions = property(_open)
    indexes_with_expressions = property(_open)
    window_range = property(_open)
    window_range_numeric = property(_open)
    window_range_non_numeric = property(_open)
    precision_numerics_enotation_small = property(_open)
    datetime_timezone_historic = property(_open)
    temp_table_comment_reflection = property(_open)
    default_schema_name_switch = property(_open)
    float_or_double_precision_behaves_generically = property(_open)
    fetch_expression = property(_open)
    fetch_offset_with_options = property(_open)
    non_updating_cascade = property(_open)

    del _open

    # -- closed: not supported -----------------------------------------------------------------

    @property
    def on_update_cascade(self):
        # FOREIGN KEY ... ON UPDATE/ON DELETE CASCADE, SET NULL and SET DEFAULT are refused.
        return exclusions.closed()

    @property
    def get_isolation_level_values(self):
        return exclusions.open()

    def get_isolation_levels(self, config):
        return {"default": "SNAPSHOT", "supported": ["SNAPSHOT", "AUTOCOMMIT"]}

    @property
    def order_by_collation(self):
        return exclusions.open()

    def get_order_by_collation(self, config):
        return "NOCASE"
