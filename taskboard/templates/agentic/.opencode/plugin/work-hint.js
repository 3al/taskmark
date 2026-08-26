/**
 * Плагин opencode: сказать агенту, что коммит — уже уход из работы.
 *
 * Скрипт задач видит только собственные вызовы, а коммит, push и запрос на
 * слияние проходят мимо него: работа уезжает наружу, пока задача числится в
 * разработке, и передача не случается. Плагин видит сам вызов инструмента.
 *
 * Решение принимает не он: есть ли задача в работе и что сказать — отвечает
 * `tasks/set_status.py --work-hint`. Здесь только повод спросить и место, куда
 * положить ответ. Иначе одно правило пришлось бы писать дважды — тут и в хуке
 * соседней среды.
 *
 * Подсказка, а не запрет: коммит в середине работы законен, и блокировка учила
 * бы её обходить. Поэтому `tool.execute.after` и дописывание к выводу, а не
 * исключение из `tool.execute.before`.
 */
import { spawnSync } from "node:child_process"
import { existsSync } from "node:fs"
import { join } from "node:path"

// Что считается уходом работы наружу
const OUTBOUND = /\bgit\s+(commit|push)\b/

// Питон зовут по-разному: лаунчера `py` может не быть, `python3` — не везде
const PYTHONS = process.platform === "win32"
  ? ["py", "python", "python3"]
  : ["python3", "python"]

function askScript(root) {
  const script = join(root, "tasks", "set_status.py")
  if (!existsSync(script)) return ""
  for (const python of PYTHONS) {
    // Просим скрипт печатать UTF-8: на Windows он иначе ответит в кодировке
    // консоли, и русская подсказка приедет кракозябрами
    const done = spawnSync(python, [script, "--work-hint"], {
      encoding: "utf8",
      timeout: 20000,
      env: { ...process.env, PYTHONIOENCODING: "utf-8" },
    })
    if (done.error || done.status !== 0 || !done.stdout) continue
    try {
      return JSON.parse(done.stdout).hint || ""
    } catch {
      return ""
    }
  }
  return ""
}

export const WorkHint = async ({ directory }) => ({
  "tool.execute.after": async (input, output) => {
    if (input.tool !== "bash") return
    if (!OUTBOUND.test(String(input.args?.command || ""))) return

    const hint = askScript(directory || process.cwd())
    if (!hint) return

    // Дописываем к выводу инструмента: агент читает его сразу за вызовом
    output.output = `${output.output}\n\n[taskboard] ${hint}`
  },
})
