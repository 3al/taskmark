// Плагины markdown для файлов задач.

// Метка заметки агента: «2026-07-28 21:05 · Claude Opus 5 · суть».
// Дата и модель — служебная шапка строки; выделять её незачем, а вот
// приглушить стоит, иначе она спорит с сутью, ради которой заметку и читают.
const NOTE_DATE = /^\d{4}-\d{2}-\d{2} \d{2}:\d{2}$/
// Хвост метки: « · Модель · » до начала сути
const NOTE_TAIL = /^(\s*·\s*[^·]+·\s*)/

function textOf(node) {
  if (node.type === 'text') return node.value
  return (node.children || []).map(textOf).join('')
}

// Приглушить метку в пунктах списка. Шаблон узкий (дата со временем в начале
// строки), поэтому обычные списки в описаниях — «- **Абзац — одна мысль.** …» —
// под него не попадают и остаются как есть
export function rehypeNoteMeta() {
  return (tree) => {
    const visit = (node) => {
      for (const child of node.children || []) {
        if (child.type === 'element' && child.tagName === 'li') markNote(child)
        else visit(child)
      }
    }

    const markNote = (li) => {
      // В «просторном» списке содержимое обёрнуто в <p>
      const host = li.children?.[0]?.tagName === 'p' ? li.children[0] : li
      const [head, tail] = host.children || []
      if (!head || head.type !== 'element' || head.tagName !== 'strong') return
      if (!NOTE_DATE.test(textOf(head).trim())) return

      const meta = { type: 'element', tagName: 'span',
                     properties: { className: ['note-meta'] },
                     children: [{ type: 'text', value: textOf(head) }] }

      if (tail && tail.type === 'text') {
        const m = NOTE_TAIL.exec(tail.value)
        if (m) {
          // Модель и разделители — часть метки, поэтому уезжают внутрь span
          meta.children.push({ type: 'text', value: m[1] })
          tail.value = tail.value.slice(m[1].length)
        }
      }
      host.children[0] = meta
    }

    visit(tree)
  }
}
