"""Сеанс 1С закрывается сразу после операции - и при успехе, и при ошибке.

    python tests/com_release_check.py
"""
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

events = []
fake = types.ModuleType("pythoncom")
fake.CoInitialize = lambda: events.append("init")
fake.CoUninitialize = lambda: events.append("uninit")
sys.modules["pythoncom"] = fake

from avtootchet.common import com  # noqa: E402


class Conn:
    """Как объект 1С: сеанс жив, пока жив объект."""
    def __init__(self):
        events.append("open")

    def __del__(self):
        events.append("close")


def connect():
    com.co_initialize()
    return Conn()


def ok():
    conn = connect()
    sel = [conn]          # выборка тоже держит соединение
    return f"строк: {len(sel)}"


def fail():
    conn = connect()
    raise RuntimeError(f"1С: ошибка запроса ({type(conn).__name__})")


def wrapped_fail():
    conn = connect()
    try:
        raise ValueError("внутри")
    except ValueError as e:
        raise RuntimeError("снаружи") from e


for fn, want_err in ((ok, None), (fail, RuntimeError), (wrapped_fail, RuntimeError)):
    events.clear()
    res, err = com.run_released(fn)
    assert (err is None) == (want_err is None) and (want_err is None or isinstance(err, want_err)), (fn, err)
    assert events == ["init", "open", "close", "uninit"], (fn.__name__, events)
    assert err is None or err.__traceback__ is None, "стек ошибки очищен"

# объект проверки хранит self.conn - отпускается в finally
events.clear()


class Probe:
    def run(self):
        self.conn = connect()
        try:
            return "ok"
        finally:
            self.conn = None


p = Probe()
assert com.run_released(p.run) == ("ok", None)
assert events == ["init", "open", "close", "uninit"], events

# два подключения в одной операции - COM выключается дважды
events.clear()
com.run_released(lambda: (connect(), connect()) and "x")
assert events.count("init") == 2 and events.count("uninit") == 2 and events.index("uninit") > events.index("close")

# без подключения ничего не выключаем
events.clear()
com.run_released(lambda: 1)
assert events == [], events

# отчёт по складу (запуск планировщиком): ошибка 1С - сеанс закрыт до выхода из build
from avtootchet.owner import stock_job  # noqa: E402


class Src(stock_job.ComSource):
    def __init__(self):
        pass

    def _check_settings(self):
        pass

    def _connect(self):
        return connect()


stock_job.make_source = lambda _s: Src()
stock_job.stock_settings = lambda _s: {"days": 180, "exclude": "", "months": 0, "article": ""}
stock_job.load_stock = lambda conn, *a: (_ for _ in ()).throw(RuntimeError("Поле не найдено"))
events.clear()
try:
    stock_job.build({})
except stock_job.SourceError as e:
    assert "Поле не найдено" in str(e)
else:
    raise AssertionError("ошибка должна дойти до пользователя")
assert events == ["init", "open", "close", "uninit"], events
print("OK: сеансы 1С закрываются сразу")
