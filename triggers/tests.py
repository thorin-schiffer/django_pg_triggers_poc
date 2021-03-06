import django
from django.db import connection
from django.db.models import Func, F, Transform
from pytest import fixture
from django.db.models import IntegerField

from triggers.models import Book
from triggers.pl_python.builder import build_pl_function, install_function, plfunction, pl_functions, \
    build_pl_trigger_function, pltrigger, pl_triggers, load_env, load_project, load_django


@fixture
def book(db):
    return Book.objects.create(name='book')


@fixture
def pl_simple_function():
    with open('pl_python/simple_function.sql', 'r') as f:
        return f.read()


def test_simple_function(pl_simple_function, db):
    with connection.cursor() as cursor:
        cursor.execute(pl_simple_function)
        cursor.execute('select pl_max(10, 20)')
        row = cursor.fetchone()
    assert row[0] == 20


def pl_max(a: int,
          b: int) -> int:
    if a > b:
        return a
    return b


def test_generate_simple_pl_python_from_function(db):
    pl_python_function = build_pl_function(pl_max)
    with connection.cursor() as cursor:
        cursor.execute(pl_python_function)
        cursor.execute('select pl_max(10, 20)')
        row = cursor.fetchone()
    assert row[0] == 20


@fixture
def simple_function(db):
    install_function(pl_max)


def test_call_simple_function_from_django_orm(simple_function, book):
    result = Book.objects.annotate(
        max_value=Func(F('amount_sold'), F('amount_stock'), function='pl_max')
    )
    assert result[0].max_value == result[0].amount_stock


def test_custom_lookup_with_function(simple_function, book):
    def plsquare(a: int) -> int:
        return a*a
    install_function(plsquare)

    class PySquare(Transform):
        lookup_name = 'plsquare'
        function = 'plsquare'

    IntegerField.register_lookup(PySquare)
    assert Book.objects.filter(amount_stock__plsquare=400).exists()
    # of course also mixes with other lookups
    assert Book.objects.filter(amount_stock__plsquare__gt=10).exists()


def test_plfunction_decorator_registers():
    @plfunction
    def pl_max(a: int,
              b: int) -> int:
        if a > b:
            return a
        return b

    assert pl_max in pl_functions.values()


def pl_trigger(td, plpy):
    # mind triggers don't return anything
    td['new']['name'] = td['new']['name'] + 'test'
    td['new']['amount_sold'] = plpy.execute("SELECT count(*) FROM triggers_book")[0]['count']


def test_generate_trigger_function(db):
    pl_python_trigger_function = build_pl_trigger_function(
        pl_trigger,
        event="INSERT",
        when="BEFORE",
        table="triggers_book"
    )
    with connection.cursor() as cursor:
        cursor.execute(pl_python_trigger_function)
    book = Book.objects.create(name='book', amount_sold=1)
    book.refresh_from_db()
    assert book.name == 'booktest'
    assert book.amount_sold == 0


def test_pltrigger_decorator_registers():
    @pltrigger(event="INSERT",
               when="BEFORE",
               table="triggers_book")
    def pl_trigger(td, plpy):
        td['new']['name'] = td['new']['name'] + 'test'

    f, params = list(pl_triggers.values())[0]
    assert f.__name__ == 'pl_trigger'
    assert params == {'event': "INSERT", 'when': "BEFORE", 'table': "triggers_book"}


def test_use_env(db):
    load_env()

    def pl_test_use_env() -> str:
        import django
        return django.VERSION

    install_function(pl_test_use_env)
    with connection.cursor() as cursor:
        cursor.execute("select pl_test_use_env()")
        row = cursor.fetchone()
    assert row[0] == str(django.VERSION)


def test_import_project(db):
    load_project()

    def pl_test_import_project() -> int:
        import import_module
        return import_module.pl_max(10, 20)

    install_function(pl_test_import_project)
    with connection.cursor() as cursor:
        cursor.execute("select pl_test_import_project()")
        row = cursor.fetchone()
    assert row[0] == 20


def test_initialize_django_project(db):
    load_django("db_triggers_example.settings")

    def pl_test_import_project() -> int:
        from triggers.models import Book
        # still uses tcp connection with postgres itself
        return Book.objects.count()

    install_function(pl_test_import_project)
    with connection.cursor() as cursor:
        cursor.execute("select pl_test_import_project()")
        row = cursor.fetchone()
    assert row[0] == 1


@fixture
def pl_django(db):
    load_django('db_triggers_example.settings')


def test_trigger_model(pl_django):
    def pl_trigger(new: Book, old: Book, td, plpy):
        # don't use save method here, it will kill the database because of recursion
        new.name = new.name + 'test'

    pl_python_trigger_function = build_pl_trigger_function(
        pl_trigger,
        event="INSERT",
        when="BEFORE",
        model=Book,
    )
    with connection.cursor() as cursor:
        cursor.execute(pl_python_trigger_function)
    book = Book.objects.create(name='book')
    book.refresh_from_db()
    assert book.name == 'booktest'
