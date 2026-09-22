// Подсветка совпадений поиска: и в обычном тексте (карточка), и в markdown
// (открытая задача, справка). Смысл один — человек ищет «где я это писал» и
// должен увидеть найденное, а не искать глазами второй раз.
//
// Запрос — строка (поиск по доске: подстрока как есть) или список слов, уже
// свёрнутых сервером (справка, логи). Список ищется по тем же правилам, что на
// сервере (`backend/text_match.py`): с `phrase` — слова подряд через любые
// пробелы, иначе каждое слово с начала слова текста, а слово в 1–2 буквы —
// только целиком. Свёртка та же — нижний регистр и «ё» как «е»; длину строки
// она не меняет, и позиции совпадений в свёрнутом тексте годятся для исходного.
// Опции: `wholeWord` — выделять слово до конца, а не только совпавшую основу;
// `inCode` — подсвечивать и внутри `code` (блоки `pre` не трогаются никогда).

const fold = (text) => text.toLowerCase().replace(/ё/g, 'е')

const WORD_CHAR = /[\p{L}\p{N}]/u
const SHORT_WORD = 2

// Запрос — литерал: в поиске люди пишут `api()` и `C++`, регулярка бы на них сломалась
const escapeRe = (text) => text.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')

// Начало слова текста; короткий хвост — целым словом. Подчёркивание и точка
// границей считаются: `status` находит `set_status`
function bounded(pattern, first, last) {
  let out = pattern
  if (WORD_CHAR.test(first[0])) out = `(?<![\\p{L}\\p{N}])${out}`
  if (last.length <= SHORT_WORD && WORD_CHAR.test(last[last.length - 1])) out += '(?![\\p{L}\\p{N}])'
  return new RegExp(out, 'gu')
}

function patternsOf(query, phrase) {
  if (!Array.isArray(query)) {
    const needle = fold(String(query || '').trim())
    return needle ? [new RegExp(escapeRe(needle), 'gu')] : []
  }
  const words = query.map((q) => fold(String(q).trim())).filter(Boolean)
  if (!words.length) return []
  if (phrase) return [bounded(words.map(escapeRe).join('\\s+'), words[0], words[words.length - 1])]
  return words.map((w) => bounded(escapeRe(w), w, w))
}

// Отрезки [start, end) с совпадениями, по порядку и без пересечений
function hitRanges(source, patterns, wholeWord) {
  const low = fold(source)
  const ranges = []
  let from = 0
  for (;;) {
    let start = -1
    let end = -1
    for (const pattern of patterns) {
      pattern.lastIndex = from
      const m = pattern.exec(low)
      if (!m || !m[0].length) continue
      if (start < 0 || m.index < start || (m.index === start && m.index + m[0].length > end)) {
        start = m.index
        end = m.index + m[0].length
      }
    }
    if (start < 0) return ranges
    if (wholeWord) {
      while (end < source.length && WORD_CHAR.test(source[end])) end += 1
    }
    ranges.push([start, end])
    from = end
  }
}

// Разбить строку на куски с подсветкой совпадений
export function highlight(text, query, { wholeWord = false, phrase = false } = {}) {
  const patterns = patternsOf(query, phrase)
  if (!patterns.length || !text) return text
  const source = String(text)
  const ranges = hitRanges(source, patterns, wholeWord)
  if (!ranges.length) return text
  const parts = []
  let from = 0
  for (const [start, end] of ranges) {
    if (start > from) parts.push(source.slice(from, start))
    parts.push(
      <mark key={start} className="search-hit">
        {source.slice(start, end)}
      </mark>,
    )
    from = end
  }
  if (from < source.length) parts.push(source.slice(from))
  return parts
}

// rehype-плагин для react-markdown: оборачивает совпадения в <mark> прямо в
// дереве, поэтому подсветка работает в любом узле — абзаце, списке, таблице,
// заголовке. Свой обход вместо unist-util-visit: это десять строк, а лишняя
// зависимость в package.json нужна была бы только ради них
export function rehypeHighlight(query, { wholeWord = false, inCode = false, phrase = false } = {}) {
  const patterns = patternsOf(query, phrase)
  return () => (tree) => {
    if (!patterns.length) return
    const walk = (node) => {
      if (!node.children) return
      const next = []
      for (const child of node.children) {
        // Блок кода не трогаем: подсветка внутри <pre> ломает моноширинную вёрстку
        if (child.type === 'element'
            && (child.tagName === 'pre' || (!inCode && child.tagName === 'code'))) {
          next.push(child)
          continue
        }
        if (child.type !== 'text') {
          walk(child)
          next.push(child)
          continue
        }
        const source = child.value
        const ranges = hitRanges(source, patterns, wholeWord)
        if (!ranges.length) {
          next.push(child)
          continue
        }
        let from = 0
        for (const [start, end] of ranges) {
          if (start > from) next.push({ type: 'text', value: source.slice(from, start) })
          next.push({
            type: 'element',
            tagName: 'mark',
            properties: { className: ['search-hit'] },
            children: [{ type: 'text', value: source.slice(start, end) }],
          })
          from = end
        }
        if (from < source.length) next.push({ type: 'text', value: source.slice(from) })
      }
      node.children = next
    }
    walk(tree)
  }
}
