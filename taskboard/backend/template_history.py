"""История шаблонов: основа слияния там, где слепка нет.

Слепок отвечает на вопрос «из чего разворачивали». Его нет у проектов,
развёрнутых раньше самого механизма, — и это как раз те проекты, где тексты
правили годами и слияние нужнее всего. Бывает и хуже: скиллы проекта могут
быть **старше инструмента** и никогда из него не разворачиваться — шаблоны
поставки произошли от них, а не наоборот.

Обоим случаям помогает одно и то же: перебрать исторические версии файла
шаблона (они лежат в репозитории инструмента, установленного git-клоном),
привести каждую к тому виду, в котором её развернули бы в этот проект, и взять
ближайшую по содержанию.

**Это подбор, а не происхождение.** Совпадение точное — факт: файл не правили,
и он ровно та версия шаблона. Приблизительное — только основа трёхстороннего
слияния: годная (шаблон и правленый файл — родня), но состояние элемента по ней
не переименовывается, а пользователю называются версия и процент совпадения,
чтобы он судил сам.
"""

from __future__ import annotations

import difflib
import hashlib
import re
import subprocess
from pathlib import Path

from backend import baseline

# Корень клона инструмента: backend/ → taskboard/ → сам репозиторий
TOOL_ROOT = Path(__file__).resolve().parent.parent.parent

# Дно, ниже которого «ближайшая» ревизия — случайный файл, а слияние с ней даст
# шум вместо результата. Порог намеренно низкий: он отсекает бессмыслицу, а не
# решает за человека, стоит ли сливать. Насколько основа близка, всегда сказано
# числом рядом с кнопкой — невидимая планка, молча убирающая выбор, и делает
# поведение непредсказуемым
MIN_RATIO = 0.2

# Ниже этой похожести слияние состоится, но конфликтов будет много — об этом
# предупреждают заранее
NOISY_RATIO = 0.7

# Тег выпуска — тот же формат, что у проверки обновлений
_TAG_RE = re.compile(r"^v\d+\.\d+\.\d+$")

# История инструмента внутри процесса не меняется: она меняется обновлением,
# а оно перезапускает сервер
_CACHE: dict[str, list[tuple[str, str]]] = {}
_VERSIONS: dict[str, str | None] = {}
# Найденный предок: ключ — файл шаблона и отпечаток развёрнутого текста.
# Состояния пересчитываются на каждой загрузке доски, а подбор перебирает всю
# историю файла построчным сравнением — без кэша это десятки секунд на проекте
# с полутора десятками правленных скиллов
_GUESSES: dict[tuple[str, str], dict | None] = {}


def _git(*args: str, stdin: bytes | None = None,
         timeout: int = 60) -> subprocess.CompletedProcess | None:
    try:
        return subprocess.run(["git", *args], cwd=str(TOOL_ROOT), input=stdin,
                              capture_output=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError):
        return None


def available() -> bool:
    """Есть ли история: инструмент поставлен клоном, а не архивом."""
    if not (TOOL_ROOT / ".git").exists():
        return False
    proc = _git("rev-parse", "--git-dir", timeout=10)
    return proc is not None and proc.returncode == 0


def _rel(path: Path) -> str | None:
    """Путь файла шаблона относительно корня инструмента (в git-виде)."""
    try:
        return path.resolve().relative_to(TOOL_ROOT).as_posix()
    except ValueError:
        return None


def _blobs(rel: str, shas: list[str]) -> dict[str, str]:
    """Содержимое файла в перечисленных коммитах — одним вызовом git.

    `cat-file --batch` вместо `show` на каждую ревизию: вызовов и так два на
    файл, а история длиной в два десятка коммитов иначе превращалась бы
    в два десятка запусков процесса на каждую проверку доски.
    """
    proc = _git("cat-file", "--batch",
                stdin="".join(f"{sha}:{rel}\n" for sha in shas).encode())
    if proc is None or proc.returncode != 0:
        return {}
    out, pos, found = proc.stdout, 0, {}
    for sha in shas:
        end = out.find(b"\n", pos)
        if end < 0:
            break
        header = out[pos:end].decode("utf-8", errors="replace").split()
        pos = end + 1
        if len(header) < 3:
            continue  # «<oid> missing»: в этом коммите файла ещё (или уже) нет
        size = int(header[2])
        found[sha] = out[pos:pos + size].decode("utf-8", errors="replace")
        pos += size + 1  # содержимое и перевод строки после него
    return found


def revisions(path: Path) -> list[tuple[str, str]]:
    """(коммит, текст шаблона) от свежих к старым. Пусто — истории нет."""
    rel = _rel(path)
    if rel is None or not available():
        return []
    if rel in _CACHE:
        return _CACHE[rel]
    proc = _git("log", "--format=%H", "--", rel)
    shas = (proc.stdout.decode("utf-8", errors="replace").split()
            if proc is not None and proc.returncode == 0 else [])
    blobs = _blobs(rel, shas) if shas else {}
    _CACHE[rel] = [(sha, blobs[sha]) for sha in shas if sha in blobs]
    return _CACHE[rel]


def release_of(commit: str) -> str | None:
    """Выпуск, в котором эта версия шаблона приехала к пользователям.

    Первый тег, содержащий коммит, — а не `VERSION` на самом коммите: там
    лежит ещё прошлый выпуск (файл поднимают отдельным релизным коммитом), и
    предок оказался бы подписан версией, в которой его не было.
    Ответ кэшируется: состояние элементов пересчитывается на каждой загрузке
    доски, и запуск git ради неизменной строки там лишний.
    """
    if commit in _VERSIONS:
        return _VERSIONS[commit]
    proc = _git("tag", "--contains", commit, "--sort=v:refname", timeout=15)
    tags = (proc.stdout.decode("utf-8", errors="replace").split()
            if proc is not None and proc.returncode == 0 else [])
    released = next((t for t in tags if _TAG_RE.match(t)), None)
    # Тега нет — ревизия ещё не выпущена: назвать её нечем, и выдумывать номер
    # хуже, чем промолчать
    _VERSIONS[commit] = released.lstrip("v") if released else None
    return _VERSIONS[commit]


def guess_base(template: Path, deployed: str, transform=None) -> dict | None:
    """Ближайшая по содержанию версия шаблона к развёрнутому тексту.

    transform приводит текст шаблона к развёрнутому виду (для скиллов —
    вырезание блоков выключенных возможностей): сравнивать надо с тем, что
    в этот проект действительно положили бы.

    Возвращает {text, commit, version, exact, ratio, usable} или None, если
    истории нет и сравнивать не с чем. `exact` — совпадение, а не подбор: файл
    после развёртывания не правили. `usable` — годится ли основа для слияния;
    ближайшая версия возвращается **и когда не годится**, потому что человеку
    нужно назвать причину отказа, а не молча убрать кнопку.
    """
    rel = _rel(template)
    key = (rel or str(template), hashlib.sha256(deployed.encode("utf-8")).hexdigest())
    if key in _GUESSES:
        return _GUESSES[key]

    history = revisions(template)
    if not history:
        _GUESSES[key] = None
        return None

    best: tuple[float, str, str] | None = None
    for commit, raw in history:
        text = transform(raw) if transform else raw
        if baseline.same_text(deployed, text):
            _GUESSES[key] = {"text": text, "commit": commit, "version": release_of(commit),
                             "exact": True, "ratio": 1.0, "usable": True}
            return _GUESSES[key]
        ratio = difflib.SequenceMatcher(None, deployed.splitlines(),
                                        text.splitlines()).ratio()
        if best is None or ratio > best[0]:
            best = (ratio, commit, text)

    if best is None:
        _GUESSES[key] = None
        return None
    _GUESSES[key] = {"text": best[2], "commit": best[1], "version": release_of(best[1]),
                     "exact": False, "ratio": round(best[0], 3),
                     "usable": best[0] >= MIN_RATIO}
    return _GUESSES[key]
