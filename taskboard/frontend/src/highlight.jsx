// Подсветка совпадений поиска: и в обычном тексте (карточка), и в markdown
// (открытая задача, справка). Смысл один — человек ищет «где я это писал» и
// должен увидеть найденное, а не искать глазами второй раз.
//
// Запрос — строка (поиск по доске: одна фраза целиком) или список слов, уже
// свёрнутых сервером (справка: основы слов без окончаний). Свёртка та же, что
// на сервере, — нижний регистр и «ё» как «е»; длину строки она не меняет, и
// позиции совпадений в свёрнутом тексте годятся для исходного.
// Опции: `wholeWord` — выделять слово целиком, а не только совпавшую основу;
// `inCode` — подсвечивать и внутри `code` (блоки `pre` не трогаются никогда).

const fold = (text) => text.toLowerCase().replace(/ё/g, 'е')

const WORD_CHAR = /[\p{L}\p{N}_]/u

function needlesOf(query) {
  const list = Array.isArray(query) ? query : [query || '']
  return list.map((q) => fold(String(q).trim())).filter(Boolean)
}

// Отрезки [start, end) с совпадениями, по порядку и без пересечений.
// Запрос — литерал: в поиске люди пишут `api()` и `C++`, регулярка бы на них сломалась
function hitRanges(source, needles, wholeWord) {
  const low = fold(source)
  const ranges = []
  let from = 0
  for (;;) {
    let start = -1
    let end = -1
    for (const needle of needles) {
      const at = low.indexOf(needle, from)
      if (at < 0) continue
      if (start < 0 || at < start || (at === start && at + needle.length > end)) {
        start = at
        end = at + needle.length
      }
    }
    if (start < 0) return ranges
    if (wholeWord) {
      while (start > from && WORD_CHAR.test(source[start - 1])) start -= 1
      while (end < source.length && WORD_CHAR.test(source[end])) end += 1
    }
    ranges.push([start, end])
    from = end
  }
}

// Разбить строку на куски с подсветкой совпадений
export function highlight(text, query, { wholeWord = false } = {}) {
  const needles = needlesOf(query)
  if (!needles.length || !text) return text
  const source = String(text)
  const ranges = hitRanges(source, needles, wholeWord)
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
export function rehypeHighlight(query, { wholeWord = false, inCode = false } = {}) {
  const needles = needlesOf(query)
  return () => (tree) => {
    if (!needles.length) return
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
        const ranges = hitRanges(source, needles, wholeWord)
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
