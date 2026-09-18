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
 * **Реплика человека снимает и сообщения агента**: ответив в терминале, он уже
 * прочёл сказанное в чате. Её видно сообщением пользователя на шине — роль
 * приходит в разных обёртках, поэтому смотрим все известные места.
 *
 * **Считается только новое сообщение человека, а не всякое обновление его
 * записи.** Шина шлёт `message.updated` по той же реплике и дальше, уже внутри
 * хода агента: приняв это за новый ответ, плагин гасил карточку, показанную
 * секунду назад. Поэтому реплика узнаётся по идентификатору сообщения, и
 * повторы того же идентификатора молчат; сообщения без него не гасят ничего —
 * ложный отзыв хуже несостоявшегося.
 *
 * **Сессию среда не кладёт в окружение — её кладёт плагин.** `OPENCODE_SESSION_ID`
 * не приходит ни в шелл инструментов, ни в процесс плагина, поэтому скрипт
 * доски не мог пометить свои карточки, а отзыв не находил, что гасить.
 * Идентификатор виден в событиях шины: плагин запоминает последний и передаёт
 * его своим вызовам скрипта и — через штатный хук `shell.env` — всем
 * шелл-вызовам среды, чтобы сообщения самого агента тоже были помечены.
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
// Переменная, по которой скрипт доски узнаёт сессию агента
const SESSION_VAR = "OPENCODE_SESSION_ID"
// События сообщений: среди них приходит и реплика человека
const MESSAGE_EVENTS = new Set(["message.updated", "message.part.updated"])

// Питон зовут по-разному: лаунчера `py` может не быть, `python3` — не везде
const PYTHONS = process.platform === "win32"
  ? ["py", "python", "python3"]
  : ["python3", "python"]

function run(root, args, session, pythons = PYTHONS) {
  const [python, ...rest] = pythons
  if (!python) return
  const env = { ...process.env, PYTHONIOENCODING: "utf-8" }
  // Метка сессии: без неё карточка уходит безымянной, и отзывать потом нечего
  if (session) env[SESSION_VAR] = session
  const child = spawn(
    python,
    [join(root, "tasks", "notify.py"), ...args],
    { cwd: root, detached: true, stdio: "ignore", windowsHide: true, env },
  )
  // Такого Python нет — пробуем следующий
  child.on("error", () => run(root, args, session, rest))
  child.unref()
}

function notify(root, text, level, session) {
  run(root, [text, "--agent", AGENT, "--level", level, "--scope", "env"], session)
}

function dismiss(root, session, scope = "env") {
  run(root, ["--dismiss", scope], session)
}

// Ответ на вопрос: шина сообщает о закрытии вызова инструмента, и это
// единственный момент, когда точно известно, что человек ответил
function isDismissal(event) {
  if (DISMISS.has(event?.type)) return true
  return event?.type === "tool.execute.after"
    && event?.properties?.tool === ASK_TOOL
}

// Идентификатор сессии лежит то на верхнем уровне события, то внутри его
// содержимого: у сообщений — в `info`, у вызовов инструментов — рядом с ними
function sessionOf(event) {
  const props = event?.properties || {}
  return props.sessionID ?? props.info?.sessionID ?? props.part?.sessionID ?? ""
}

// Идентификатор самого сообщения: по нему отличают новую реплику человека от
// повторного обновления уже известной
function messageIdOf(event) {
  const props = event?.properties || {}
  return props.info?.id ?? props.message?.id ?? props.part?.messageID
    ?? props.messageID ?? ""
}

// Реплика человека. Роль лежит в разных обёртках в зависимости от события,
// поэтому смотрим все известные места: промах здесь означает, что сообщения
// агента не погаснут вовсе
function isUserReply(event) {
  if (!MESSAGE_EVENTS.has(event?.type)) return false
  const props = event?.properties || {}
  const role = props.info?.role ?? props.message?.role ?? props.part?.role
    ?? props.role
  return role === "user"
}

export const AttentionNotify = async ({ directory, worktree }) => {
  // Последняя известная сессия: события шины её называют, окружение — нет.
  // Живёт в замыкании плагина, то есть столько же, сколько сама среда
  let session = ""
  // Последняя реплика человека, которую уже отработали: та же запись приходит
  // обновлениями и дальше, внутри хода агента
  let repliedTo = ""

  return {
    event: async ({ event }) => {
      const seen = sessionOf(event)
      if (seen) session = seen
      const moment = MOMENTS[event?.type]
      // Реплика человека снимает всё сказанное сессией, остальные моменты —
      // только зовы среды
      let reply = false
      if (!moment && isUserReply(event)) {
        const id = messageIdOf(event)
        // Без идентификатора отличить новую реплику от обновления нельзя, а
        // ошибиться здесь значит погасить только что показанную карточку
        if (id && id !== repliedTo) {
          repliedTo = id
          reply = true
        } else {
          return
        }
      }
      const dismissal = reply || (!moment && isDismissal(event))
      if (!moment && !dismissal) return
      // Только папки проекта: рабочая папка процесса может оказаться чужим
      // проектом, и карточка ушла бы не на ту доску
      const root = [directory, worktree]
        .find((dir) => dir && existsSync(join(dir, "tasks", "notify.py")))
      if (!root) return
      try {
        if (dismissal) dismiss(root, session, reply ? "all" : "env")
        else notify(root, ...moment, session)
      } catch {
        // уведомление вспомогательно — среду не роняем
      }
    },

    // Сообщения агент шлёт сам, из шелла среды: без этой подстановки они
    // приходят без метки, и ответ человека их не снимает
    "shell.env": async (_input, output) => {
      if (session && output?.env) output.env[SESSION_VAR] = session
    },
  }
}
