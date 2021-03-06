import inspect
from distutils.sysconfig import get_python_lib
from functools import wraps
from textwrap import dedent

from django.conf import settings
from django.db import connection

type_mapper = {
    int: "integer",
    str: "varchar",
    inspect._empty: "void"
}


def remove_decorator(source_code, name):
    start = source_code.find(f"@{name}")
    end = source_code.find("def")
    if start < 0:
        return source_code
    return source_code[:start] + source_code[end:]


def build_pl_function(f):
    name = f.__name__
    signature = inspect.signature(f)
    try:
        args = []
        for arg, specs in signature.parameters.items():
            if specs.annotation not in type_mapper:
                raise RuntimeError(f"Unknown type {specs.annotation}")
            args.append(f"{arg} {type_mapper[specs.annotation]}")
    except KeyError as ex:
        raise RuntimeError(f"{ex}:"
                           f"Function {f} must be fully annotated to be translated to pl/python")

    header = f"CREATE OR REPLACE FUNCTION {name} ({','.join(args)}) RETURNS {type_mapper[signature.return_annotation]}"

    body = remove_decorator(inspect.getsource(f), "plfunction")
    return f"""{header}
AS $$
{dedent(body)}
return {name}({','.join(signature.parameters.keys())})
$$ LANGUAGE plpython3u
"""


def build_pl_trigger_function(f, event, when, table=None, model=None):
    if not table and not model:
        raise RuntimeError("Either model or table must be set for trigger installation")

    name = f.__name__
    if model:
        meta = model.objects.model._meta
        table = meta.db_table
        model_name = meta.object_name
        app_name = meta.app_label
        import_statement = f"""
from django.apps import apps
from django.forms.models import model_to_dict
{model_name} = apps.get_model('{app_name}', '{model_name}')
new = {model_name}(**TD['new'])
old = {model_name}(**TD['old']) if TD['old'] else None 
"""
        call_statement = f"{name}(new, old, TD, plpy)"
        back_convert_statement = f"""
TD['new'].update(model_to_dict(new))
if TD['old']:
    TD['old'].update(model_to_dict(old))
"""
    else:
        import_statement = back_convert_statement = ""
        call_statement = f"{name}(TD, plpy)"

    header = f"CREATE OR REPLACE FUNCTION {name}() RETURNS TRIGGER"

    body = remove_decorator(inspect.getsource(f), "pltrigger")
    return f"""
BEGIN;
{header}
AS $$
{import_statement}
{dedent(body)}
{call_statement}
{back_convert_statement}
return 'MODIFY'
$$ LANGUAGE plpython3u;

DROP TRIGGER IF EXISTS {name + '_trigger'} ON {table} CASCADE; 
CREATE TRIGGER {name + '_trigger'}
{when} {event} ON {table}
FOR EACH ROW
EXECUTE PROCEDURE {name}();
END;
"""


def install_function(f, trigger_params=None):
    trigger_params = trigger_params or {}
    pl_python_function = build_pl_trigger_function(f, **trigger_params) if trigger_params else build_pl_function(f)
    with connection.cursor() as cursor:
        cursor.execute(pl_python_function)


pl_functions = {}
pl_triggers = {}


def plfunction(f):
    @wraps(f)
    def installed_func(*args, **kwargs):
        return f(*args, **kwargs)

    module = inspect.getmodule(installed_func)
    pl_functions[f"{module.__name__}.{installed_func.__qualname__}"] = installed_func
    return installed_func


def pltrigger(**trigger_parameters):
    def _pl_trigger(f):
        @wraps(f)
        def installed_func(*args, **kwargs):
            return f(*args, **kwargs)

        module = inspect.getmodule(installed_func)
        pl_triggers[f"{module.__name__}.{installed_func.__qualname__}"] = installed_func, trigger_parameters
        return installed_func

    return _pl_trigger


@plfunction
def pl_load_path(syspath: str):
    import sys
    sys.path.append(syspath)


def load_env():
    """
    Installs and loads the virtualenv of this project into the postgres interpreter.
    """
    install_function(pl_load_path)
    path = get_python_lib()
    with connection.cursor() as cursor:
        cursor.execute(f"select pl_load_path('{path}')")


def load_project():
    install_function(pl_load_path)
    path = settings.BASE_DIR

    with connection.cursor() as cursor:
        cursor.execute(f"select pl_load_path('{path}')")


@plfunction
def pl_load_django(project_dir: str, django_settings_module: str):
    import os, sys
    from django.core.wsgi import get_wsgi_application
    os.environ.setdefault('DJANGO_SETTINGS_MODULE', django_settings_module)
    sys.path.append(project_dir)
    get_wsgi_application()


def load_django(setting_module):
    load_env()
    load_project()
    install_function(pl_load_django)
    with connection.cursor() as cursor:
        cursor.execute(f"select pl_load_django('{settings.BASE_DIR}', '{setting_module}')")


@plfunction
def pl_python_version() -> str:
    from platform import python_version
    return python_version()


def get_python_info():
    install_function(pl_python_version)
    with connection.cursor() as cursor:
        cursor.execute(f"select pl_python_version()")
        info = {
            'version': cursor.fetchone()[0]
        }
    return info
