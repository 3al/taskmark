// Подсветка совпадений поиска: и в обычном тексте (карточка), и в markdown
// (открытая задача). Смысл один — человек ищет «где я это писал» и должен
// увидеть найденное, а не искать глазами второй раз.

// Разбить строку на куски с подсветкой совпадений. Запрос — литерал:
// в поиске люди пишут `api()` и `C++`, регулярка бы на них сломалась
export function highlight(text, query) {
  const needle = (query || '').trim().toLowerCase()
  if (!needle || !text) return text
  const source = String(text)
  const low = source.toLowerCase()
  const parts = []
  let from = 0
  let at = low.indexOf(needle)
  while (at >= 0) {
    if (at > from) parts.push(source.slice(from, at))
    parts.push(
      <mark key={at} className="bg-amber-400/30 text-amber-100 rounded px-0.5">
        {source.slice(at, at + needle.length)}
      </mark>,
    )
    from = at + needle.length
    at = low.indexOf(needle, from)
  }
  if (from < source.length) parts.push(source.slice(from))
  return parts
}

// rehype-плагин для react-markdown: оборачивает совпадения в <mark> прямо в
// дереве, поэтому подсветка работает в любом узле — абзаце, списке, таблице,
// заголовке. Свой обход вместо unist-util-visit: это десять строк, а лишняя
// зависимость в package.json нужна была бы только ради них
export function rehypeHighlight(query) {
  const needle = (query || '').trim().toLowerCase()
  return () => (tree) => {
    if (!needle) return
    const walk = (node) => {
      if (!node.children) return
      const next = []
      for (const child of node.children) {
        // Код не трогаем: подсветка внутри <pre> ломает моноширинную вёрстку
        if (child.type === 'element' && (child.tagName === 'code' || child.tagName === 'pre')) {
          next.push(child)
          continue
        }
        if (child.type !== 'text') {
          walk(child)
          next.push(child)
          continue
        }
        const source = child.value
        const low = source.toLowerCase()
        if (!low.includes(needle)) {
          next.push(child)
          continue
        }
        let from = 0
        let at = low.indexOf(needle)
        while (at >= 0) {
          if (at > from) next.push({ type: 'text', value: source.slice(from, at) })
          next.push({
            type: 'element',
            tagName: 'mark',
            properties: { className: ['search-hit'] },
            children: [{ type: 'text', value: source.slice(at, at + needle.length) }],
          })
          from = at + needle.length
          at = low.indexOf(needle, from)
        }
        if (from < source.length) next.push({ type: 'text', value: source.slice(from) })
      }
      node.children = next
    }
    walk(tree)
  }
}
