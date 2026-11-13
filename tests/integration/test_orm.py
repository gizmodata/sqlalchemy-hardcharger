"""The ORM against a real server: auto-incrementing keys, relationships, bulk work."""

import datetime
import decimal
import uuid

import pytest
from sqlalchemy import ForeignKey, Identity, Sequence, String, func, inspect, select, text
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, relationship

from sqlalchemy_hardcharger import insert


@pytest.fixture
def models(engine, name):
    prefix = name  # (a class body with a `name` attribute can't see the fixture's `name`)

    class Base(DeclarativeBase):
        pass

    class Author(Base):
        __tablename__ = f"{prefix}_authors"
        id: Mapped[int] = mapped_column(primary_key=True)
        name: Mapped[str] = mapped_column(String(50), unique=True)
        token: Mapped[uuid.UUID] = mapped_column(default=uuid.uuid4)
        joined: Mapped[datetime.datetime] = mapped_column(server_default=func.now())
        balance: Mapped[decimal.Decimal | None]
        books: Mapped[list["Book"]] = relationship(back_populates="author")

    class Book(Base):
        __tablename__ = f"{prefix}_books"
        id: Mapped[int] = mapped_column(primary_key=True)
        author_id: Mapped[int] = mapped_column(ForeignKey(f"{prefix}_authors.id"))
        title: Mapped[str]
        author: Mapped[Author] = relationship(back_populates="books")

    Base.metadata.create_all(bind=engine)
    yield Base, Author, Book
    Base.metadata.drop_all(bind=engine)


def test_plain_integer_primary_key_auto_increments(engine, models):
    _, Author, _ = models
    with Session(engine) as session:
        ada = Author(name="Ada")
        session.add(ada)
        session.flush()
        assert ada.id == 1  # assigned by the table's sequence, fetched with RETURNING
        grace = Author(name="Grace")
        session.add(grace)
        session.commit()
        assert (ada.id, grace.id) == (1, 2)
        assert isinstance(ada.token, uuid.UUID)
        assert isinstance(ada.joined, datetime.datetime)  # the server default came back


def test_the_sequence_belongs_to_the_table(engine, models, name):
    Base, _, _ = models
    inspector = inspect(engine)
    assert f"{name}_authors_id_seq" in inspector.get_sequence_names()
    assert f"{name}_books_id_seq" in inspector.get_sequence_names()
    Base.metadata.drop_all(bind=engine)
    remaining = inspect(engine).get_sequence_names()
    assert f"{name}_authors_id_seq" not in remaining
    assert f"{name}_books_id_seq" not in remaining


def test_create_all_twice_is_harmless(engine, models):
    Base, Author, _ = models
    with Session(engine) as session:
        session.add(Author(name="Ada"))
        session.commit()
    Base.metadata.create_all(bind=engine)  # tables exist: nothing is created or reset
    with Session(engine) as session:
        grace = Author(name="Grace")
        session.add(grace)
        session.commit()
        assert grace.id == 2  # the sequence carried on


def test_relationships_and_joins(engine, models):
    _, Author, Book = models
    with Session(engine) as session:
        ada = Author(name="Ada", books=[Book(title="Notes"), Book(title="Sketch")])
        session.add(ada)
        session.commit()
        assert [book.author_id for book in ada.books] == [ada.id, ada.id]

    with Session(engine) as session:
        rows = session.execute(
            select(Author.name, func.count(Book.id)).join(Book).group_by(Author.name)
        ).all()
        assert rows == [("Ada", 2)]
        book = session.scalars(select(Book).where(Book.title == "Sketch")).one()
        assert book.author.name == "Ada"  # lazy load


def test_bulk_add_assigns_each_object_its_own_key(engine, models):
    _, Author, _ = models
    names = [f"author {i:03d}" for i in range(250)]
    with Session(engine) as session:
        authors = [Author(name=n, balance=decimal.Decimal(i)) for i, n in enumerate(names)]
        session.add_all(authors)
        session.commit()
        ids = [a.id for a in authors]
        assert sorted(ids) == list(range(1, 251))
        # each object holds the key of ITS row, not just some row's
        stored = dict(session.execute(select(Author.id, Author.name)).all())
        assert all(stored[a.id] == a.name for a in authors)


def test_bulk_insert_statement_with_returning(engine, models):
    _, Author, _ = models
    with Session(engine) as session:
        returned = session.execute(
            Author.__table__.insert().returning(Author.id, Author.name),
            [{"name": f"n{i}", "token": uuid.uuid4()} for i in range(50)],
        ).all()
        session.commit()
    assert len(returned) == 50
    assert {row.name for row in returned} == {f"n{i}" for i in range(50)}
    assert len({row.id for row in returned}) == 50


def test_update_and_delete(engine, models):
    _, Author, _ = models
    with Session(engine) as session:
        ada = Author(name="Ada", balance=decimal.Decimal("10.50"))
        session.add(ada)
        session.commit()
        ada.balance += decimal.Decimal("1.25")
        session.commit()
        session.refresh(ada)
        assert ada.balance == decimal.Decimal("11.75")
        session.delete(ada)
        session.commit()
        assert session.scalar(select(func.count()).select_from(Author)) == 0


def test_upsert(engine, models):
    _, Author, _ = models
    table = Author.__table__
    with engine.begin() as conn:
        conn.execute(table.insert().values(name="Ada", token=uuid.uuid4(), balance=1))
        statement = insert(table).values(name="Ada", token=uuid.uuid4(), balance=5)
        conn.execute(
            statement.on_conflict_do_update(
                index_elements=[table.c.name], set_={"balance": statement.excluded.balance}
            )
        )
        assert conn.execute(select(table.c.balance)).scalars().all() == [decimal.Decimal("5.000")]
        conn.execute(statement.on_conflict_do_nothing())
        assert conn.execute(select(func.count()).select_from(table)).scalar() == 1


def test_identity_and_explicit_sequence(engine, name):
    class Base(DeclarativeBase):
        pass

    class Invoice(Base):
        __tablename__ = f"{name}_invoices"
        id: Mapped[int] = mapped_column(Identity(start=1000, increment=5), primary_key=True)
        note: Mapped[str]

    class Ticket(Base):
        __tablename__ = f"{name}_tickets"
        id: Mapped[int] = mapped_column(
            Sequence(f"{name}_ticket_numbers", start=7), primary_key=True
        )
        note: Mapped[str]

    Base.metadata.create_all(bind=engine)
    try:
        with Session(engine) as session:
            invoices = [Invoice(note="a"), Invoice(note="b")]
            tickets = [Ticket(note="a"), Ticket(note="b")]
            session.add_all([*invoices, *tickets])
            session.commit()
            assert sorted(i.id for i in invoices) == [1000, 1005]
            assert sorted(t.id for t in tickets) == [7, 8]
    finally:
        Base.metadata.drop_all(bind=engine)
    assert f"{name}_ticket_numbers" not in inspect(engine).get_sequence_names()


def test_insert_without_returning_pre_fetches_the_key(url, name):
    from sqlalchemy import Column, Integer, MetaData, Table, create_engine

    engine = create_engine(url=url)
    metadata = MetaData()
    table = Table(
        name,
        metadata,
        Column("id", Integer, primary_key=True),
        Column("v", String),
        implicit_returning=False,
    )
    metadata.create_all(bind=engine)
    try:
        with engine.begin() as conn:
            first = conn.execute(table.insert().values(v="a"))
            second = conn.execute(table.insert().values(v="b"))
            assert first.inserted_primary_key == (1,)
            assert second.inserted_primary_key == (2,)
    finally:
        metadata.drop_all(bind=engine)
        engine.dispose()


def test_deleting_parent_and_children_needs_two_transactions(engine, models):
    """DuckDB checks a foreign key against rows deleted earlier in the same transaction."""
    from sqlalchemy import exc

    _, Author, Book = models
    with Session(engine) as session:
        session.add(Author(name="Ada", books=[Book(title="Notes")]))
        session.commit()

    with Session(engine) as session:
        session.execute(Book.__table__.delete())
        with pytest.raises(exc.IntegrityError, match="still referenced by a foreign key"):
            session.execute(Author.__table__.delete())
        session.rollback()

    with Session(engine) as session:
        session.execute(Book.__table__.delete())
        session.commit()  # the children are gone for good ...
        session.execute(Author.__table__.delete())  # ... so now the parent can go
        session.commit()
        assert session.scalar(text(f"SELECT count(*) FROM {Author.__tablename__}")) == 0
