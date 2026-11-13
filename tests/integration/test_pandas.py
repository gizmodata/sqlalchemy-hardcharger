"""pandas through the dialect: to_sql and read_sql against a real server."""

import datetime

import pandas as pd
from sqlalchemy import inspect, text


def test_to_sql_and_read_sql_round_trip(engine, name):
    frame = pd.DataFrame(
        {
            "id": [1, 2, 3],
            "name": ["ada", "grace", None],
            "score": [1.5, None, 3.25],
            "seen": [datetime.datetime(2026, 1, 1, 12, 0), datetime.datetime(2026, 1, 2), None],
            "ok": [True, False, True],
        }
    )
    try:
        written = frame.to_sql(name=name, con=engine, index=False, if_exists="fail")
        assert written in (3, None, -1)
        assert inspect(engine).has_table(table_name=name)

        back = pd.read_sql(sql=text(f"SELECT * FROM {name} ORDER BY id"), con=engine)
        assert back["id"].tolist() == [1, 2, 3]
        assert back["name"].tolist()[:2] == ["ada", "grace"] and pd.isna(back["name"][2])
        assert back["score"][0] == 1.5 and pd.isna(back["score"][1])
        assert back["seen"][0] == pd.Timestamp("2026-01-01 12:00")
        assert back["ok"].tolist() == [True, False, True]

        frame.to_sql(
            name=name, con=engine, index=False, if_exists="append", method="multi", chunksize=2
        )
        assert pd.read_sql_table(table_name=name, con=engine).shape == (6, 5)

        frame.head(1).to_sql(name=name, con=engine, index=False, if_exists="replace")
        assert pd.read_sql_query(sql=f"SELECT count(*) AS n FROM {name}", con=engine)["n"][0] == 1
    finally:
        with engine.begin() as conn:
            conn.execute(text(f"DROP TABLE IF EXISTS {name}"))
