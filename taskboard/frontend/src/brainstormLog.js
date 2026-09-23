// Протокол брейншторма по ролям: какой кусок файла — ответ какого агента.
//
// Формат протокола задают скиллы brainstorm и brainstorm-team: сессия
// («# Сессия: …»), раунды («## Раунд N: …»), ответы ролей под заголовками
// `##` / `###` с именем роли. Ответ агента — свободный Markdown со своими
// заголовками, поэтому блок роли кончается не на следующем заголовке, а на
// следующей роли, раунде или сессии. Строки внутри блока кода не разбираются:
// «### Архитектор» в примере формата — не роль.

// Три роли — три цвета. Третья, оценивающая, называется по-разному: Оппонент
// у brainstorm-team, Критик у brainstorm, — но роль у них одна
const ROLES = [
  ['архитектор', 'architect'],
  ['прагматик', 'pragmatist'],
  ['оппонент', 'judge'],
  ['критик', 'judge'],
]

const HEADING = /^(#{1,6})\s+(.*?)\s*#*\s*$/
const FENCE = /^\s{0,3}(`{3,}|~{3,})/

// Начинается ли заголовок словом целиком. `\b` тут не годится: в JS он
// считает буквой только латиницу, и «раунд» перед пробелом границы не имеет
function startsWord(title, word) {
  const name = title.trim().toLowerCase()
  return name.startsWith(word) && !/[\p{L}\p{N}]/u.test(name.charAt(word.length))
}

function roleOf(title) {
  const name = title.replace(/[*_`]/g, '').trim().toLowerCase()
  return ROLES.find(([word]) => name.startsWith(word))?.[1] || null
}

// Начинает ли строка новый кусок: роль — её ключ, служебная граница — null,
// обычная строка — undefined
function boundary(lines, i) {
  const line = lines[i].replace(/\r$/, '')
  const m = HEADING.exec(line)
  if (m) {
    const level = m[1].length
    if ((level === 2 || level === 3) && roleOf(m[2])) return roleOf(m[2])
    if (level === 2 && startsWord(m[2], 'раунд')) return null
    if (level === 1 && startsWord(m[2], 'сессия')) return null
    return undefined
  }
  // Разделитель перед новой сессией — часть сессии, а не хвост последнего ответа
  if (/^-{3,}\s*$/.test(line)) {
    const next = lines.slice(i + 1).find((l) => l.trim())
    const h = next && HEADING.exec(next.replace(/\r$/, ''))
    if (h && h[1].length === 1 && startsWord(h[2], 'сессия')) return null
  }
  return undefined
}

// Куски протокола подряд: [{role, start, text}], role — ключ цвета или null,
// start — номер первой строки куска в файле (с единицы). Склеенные через
// перевод строки куски дают исходный текст без потерь
export function roleSegments(text) {
  const lines = text.split('\n')
  const out = []
  let fence = null
  lines.forEach((line, i) => {
    let role
    if (fence) {
      const m = FENCE.exec(line)
      if (m && m[1][0] === fence[0] && m[1].length >= fence.length) fence = null
    } else {
      const m = FENCE.exec(line)
      if (m) fence = m[1]
      else role = boundary(lines, i)
    }
    const last = out[out.length - 1]
    // Служебная граница после служебного же текста куска не рвёт
    const starts = role !== undefined && !(role === null && last && last.role === null)
    if (!last || starts) out.push({ role: role ?? null, start: i + 1, lines: [line] })
    else last.lines.push(line)
  })
  return out.map(({ role, start, lines: part }) => ({ role, start, text: part.join('\n') }))
}

// Текст куска для отдельного рендера: пустые строки впереди сдвигают его на
// своё место в файле, и номера строк заголовков совпадают с файлом — по ним
// поиск ведёт к найденному месту. Пустые строки в начале Markdown ничего не рисуют
export function segmentSource(segment) {
  return '\n'.repeat(segment.start - 1) + segment.text
}
