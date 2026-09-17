/**
 * Плагин opencode: сказать доске, что ход перешёл к человеку.
 *
 * Агент виден только в своём терминале, а человек, отойдя от экрана, держит
 * открытой доску. Про свои шаги агент рассказывает сам (`tasks/notify.py` по
 * правилам проекта), но два момента он назвать не может: встроенный вопрос и
 * запрос разрешения блокируют его ход до решения человека, а после решения
 * звать уже поздно.
 *
 * **Поводов два, и шина событий называет их сама:** `question.asked` — задан
 * вопрос, `permission.asked` — действие ждёт разрешения. Оба приходят в момент
 * показа, пока решение не принято.
 *
 * **Ни текст вопроса, ни команда не пересылаются.** В них может оказаться
 * содержимое работы или секрет; доске достаточно знать, что человека ждут.
 *
 * **Отпавший повод убирают с доски.** Решение по разрешению
 * (`permission.replied`), ответ на вопрос (`question.replied`) и конец хода
 * агента (`session.idle`) означают, что звать больше некого: висящая карточка
 * после этого врёт. В эти моменты плагин просит скрипт доски снять **зовы
 * среды** своей сессии — сообщения самого агента остаются: «работа готова» он
 * посылает прямо перед концом хода.
 *
 * Уведомление не ждут: хуки плагинов выполняются по очереди, и медленный
 * запуск Python задержал бы саму среду. Отказ доставки на работу не влияет.
 */
import { spawn } from "node:child_process"
import { existsSync } from "node:fs"
import { join } from "node:path"

// Тип события шины → что сказать и каким тоном. Разрешение — `warning`:
// дальше не идём; вопрос — `info`: нужен ответ, чтобы продолжать
const MOMENTS = {
  "permission.asked": ["opencode ждёт разрешения: подтвердите или отклоните запрос", "warning"],
  "question.asked": ["opencode ждёт вашего ответа: задан вопрос", "info"],
}

// Кто зовёт. Модель плагину неизвестна, а выдуманная в всплывашке врала бы
const AGENT = "opencode"

// События, после которых сказанное доске больше не ждёт человека. Решение по
// разрешению шина называет прямо; ответ на вопрос виден закрытием его вызова;
// `session.idle` — конец хода агента, он закрывает и остальные поводы
const DISMISS = new Set(["permission.replied", "question.replied", "session.idle"])
// Инструмент, которым задаётся вопрос: его закрытый вызов и есть ответ
const ASK_TOOL = "question"

// Питон зовут по-разному: лаунчера `py` может не быть, `python3` — не везде
const PYTHONS = process.platform === "win32"
  ? ["py", "python", "python3"]
  : ["python3", "python"]

function run(root, args, pythons = PYTHONS) {
  const [python, ...rest] = pythons
  if (!python) return
  const child = spawn(
    python,
    [join(root, "tasks", "notify.py"), ...args],
    {
      cwd: root,
      detached: true,
      stdio: "ignore",
      windowsHide: true,
      env: { ...process.env, PYTHONIOENCODING: "utf-8" },
    },
  )
  // Такого Python нет — пробуем следующий
  child.on("error", () => run(root, args, rest))
  child.unref()
}

function notify(root, text, level) {
  run(root, [text, "--agent", AGENT, "--level", level, "--scope", "env"])
}

function dismiss(root) {
  run(root, ["--dismiss", "env"])
}

// Ответ на вопрос: шина сообщает о закрытии вызова инструмента, и это
// единственный момент, когда точно известно, что человек ответил
function isDismissal(event) {
  if (DISMISS.has(event?.type)) return true
  return event?.type === "tool.execute.after"
    && event?.properties?.tool === ASK_TOOL
}

export const AttentionNotify = async ({ directory, worktree }) => ({
  event: async ({ event }) => {
    const moment = MOMENTS[event?.type]
    const dismissal = !moment && isDismissal(event)
    if (!moment && !dismissal) return
    // Только папки проекта: рабочая папка процесса может оказаться чужим
    // проектом, и карточка ушла бы не на ту доску
    const root = [directory, worktree]
      .find((dir) => dir && existsSync(join(dir, "tasks", "notify.py")))
    if (!root) return
    try {
      if (dismissal) dismiss(root)
      else notify(root, ...moment)
    } catch {
      // уведомление вспомогательно — среду не роняем
    }
  },
})
