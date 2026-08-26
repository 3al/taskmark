// Плагины и переопределения рендера markdown для файлов задач и справки.

import { statusStyleByLabel } from './statuses'

// Широкий блок должен ехать сам, а не тащить за собой текст: обёртка просит
// ширину по содержимому (окно под неё растёт), а упёршись в свой предел —
// прокручивается. Объект общий и неизменяемый: новый на каждый рендер
// заставлял react-markdown пересобирать всё дерево заново
export const mdComponents = {
  table: ({ node, ...props }) => (
    <div className="md-scroll-x"><table {...props} /></div>
  ),
}

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

// Кем подписан перевод статуса — источником, а не моделью. Зеркало
// TRANSITION_RE в backend/notes.py: там же сказано, почему одной стрелки для
// опознания мало и почему содержимое скобок не разбирается (имя модели задаёт
// человек, и скобки внутри него законны)
const MOVE_SOURCE = /·\s*(доска|скрипт(\s+вручную)?(\s*\([^·]+\))?)\s*·\s*$/
const MOVE_TEXT = /^([^·→,«»]+) → ([^·→,«»]+)$/

// Перевод статуса — событие, а не мысль агента: в хронологии их разделяет глаз,
// а не чтение. Поэтому статусы красятся цветом своих колонок, а сам пункт
// получает класс — он убирает маркер списка и приглушает строку.
//
// Плагин идёт ПОСЛЕ rehypeNoteMeta: тот уже унёс дату с подписью в свой span,
// и в тексте остаётся ровно переход. Разбирать полную строку самим значило бы
// повторять чужой разбор и ломаться от каждой его правки.
export function rehypeStatusMove() {
  return (tree) => {
    const visit = (node) => {
      for (const child of node.children || []) {
        if (child.type === 'element' && child.tagName === 'li') markMove(child)
        else visit(child)
      }
    }

    const chip = (label) => {
      const style = statusStyleByLabel(label)
      return {
        type: 'element', tagName: 'span',
        properties: { className: style ? ['status-chip', style.header] : ['status-chip'] },
        children: [{ type: 'text', value: label }],
      }
    }

    const markMove = (li) => {
      const host = li.children?.[0]?.tagName === 'p' ? li.children[0] : li
      const [meta, tail] = host.children || []
      if (!meta || meta.properties?.className?.[0] !== 'note-meta') return
      if (!MOVE_SOURCE.test(textOf(meta))) return
      if (!tail || tail.type !== 'text') return

      const m = MOVE_TEXT.exec(tail.value.trim())
      if (!m) return

      host.children.splice(1, 1, chip(m[1]), { type: 'text', value: ' → ' }, chip(m[2]))
      li.properties = li.properties || {}
      li.properties.className = [...(li.properties.className || []), 'status-move']
    }

    visit(tree)
  }
}
