import { useRef } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { mdComponents, rehypeNoteMeta } from '../markdown'
import { FORM_FIELD } from '../fields'

// Минималистичный редактор markdown: сырой текст + короткая панель разметки +
// предпросмотр. Правится **исходный текст файла**, и ничего не переформатируется
// само: текст, набранный человеком, попадает в файл дословно. Оформление целиком
// остаётся за автором и за агентом, панель лишь избавляет от запоминания
// синтаксиса.
//
// Компонент не знает, что именно правит: описание задачи, критерии, поле формы
// или заметку. Всё, что ему нужно, — текст и что делать по сохранению.

const MARKUP_BUTTON =
  'w-6 h-6 flex items-center justify-center rounded border border-transparent ' +
  'text-zinc-500 hover:text-zinc-200 hover:border-zinc-700 hover:bg-zinc-800/60 transition'

const TOGGLE = 'hover:text-zinc-300 transition'

// Строка забора блока кода. Отступ перед ним допустим (блок внутри пункта
// списка), длина забора — от трёх знаков
const FENCE = /^\s*```/

export default function MarkdownEditor({
  value,
  onChange,
  onSave,
  onCancel,
  saving = false,
  error = null,
  preview = false,
  onPreviewChange,
  autoFocus = true,
  minRows = 6,
  maxRows = 24,
  hint = 'Ctrl+Enter — сохранить, Esc — отменить',
}) {
  const ref = useRef(null)

  // Заменить кусок текста и оставить его выделенным: после кнопки руки остаются
  // на клавиатуре, а не целятся мышью в новое место
  const put = (text, from, to, replacement, selectFrom, selectTo) => {
    onChange(text.slice(0, from) + replacement + text.slice(to))
    requestAnimationFrame(() => {
      ref.current?.focus()
      ref.current?.setSelectionRange(selectFrom, selectTo)
    })
  }

  // Обернуть выделение — или снять разметку, если она уже стоит: одна и та же
  // кнопка ставит и убирает, как в любом редакторе
  const applyWrap = (before, after = before) => {
    const el = ref.current
    if (!el) return
    const text = el.value
    let from = el.selectionStart
    let to = el.selectionEnd
    // Двойной клик по слову захватывает пробел за ним — знаки ставим по словам,
    // иначе внутрь `кода` попадает хвостовой пробел
    while (to > from && /\s/.test(text[to - 1])) to--
    while (from < to && /\s/.test(text[from])) from++
    const picked = text.slice(from, to)

    // Разметка внутри выделения: выделили `код` целиком со знаками
    if (picked.length >= before.length + after.length &&
        picked.startsWith(before) && picked.endsWith(after)) {
      const bare = picked.slice(before.length, picked.length - after.length)
      return put(text, from, to, bare, from, from + bare.length)
    }
    // Ссылка снимается по форме, а не по точному хвосту: адрес внутри свой
    const link = before === '[' && picked.match(/^\[([\s\S]*)\]\([^)]*\)$/)
    if (link) return put(text, from, to, link[1], from, from + link[1].length)
    // Разметка вокруг выделения: выделили слово внутри уже размеченного куска
    if (text.slice(from - before.length, from) === before &&
        text.slice(to, to + after.length) === after) {
      return put(text, from - before.length, to + after.length, picked,
                 from - before.length, from - before.length + picked.length)
    }
    put(text, from, to, before + picked + after,
        from + before.length, from + before.length + picked.length)
  }

  // Списки и заголовки живут строкой, а не куском текста: префикс ставится на
  // все захваченные строки и снимается повторным нажатием
  const applyLinePrefix = (prefix, pattern) => {
    const el = ref.current
    if (!el) return
    const { selectionStart: from, selectionEnd: to, value: text } = el
    const lineStart = text.lastIndexOf('\n', from - 1) + 1
    const lineEnd = text.indexOf('\n', to) < 0 ? text.length : text.indexOf('\n', to)
    const lines = text.slice(lineStart, lineEnd).split('\n')
    const marked = lines.every((line) => !line.trim() || pattern.test(line))
    const changed = lines
      .map((line) => (!line.trim() ? line
        : marked ? line.replace(pattern, '$1') : `${prefix}${line}`))
      .join('\n')
    put(text, lineStart, lineEnd, changed, lineStart, lineStart + changed.length)
  }

  const applyList = () => applyLinePrefix('- ', /^(\s*)[-*] /)
  const applyHeading = () => applyLinePrefix('### ', /^(\s*)#{1,6} /)

  // Блок кода — не обёртка вокруг куска строки, а свои строки вокруг куска
  // текста: забор ``` markdown видит только с начала строки. Поэтому выделение
  // сначала расширяется до целых строк, как у списка, а знаки встают отдельными
  // строками — иначе кнопка молча ставила бы забор посреди абзаца
  const applyCodeBlock = () => {
    const el = ref.current
    if (!el) return
    const { selectionStart: from, selectionEnd: to, value: text } = el
    const lineStart = text.lastIndexOf('\n', from - 1) + 1
    const lineEnd = text.indexOf('\n', to) < 0 ? text.length : text.indexOf('\n', to)
    const lines = text.slice(lineStart, lineEnd).split('\n')

    // Забор внутри выделения: захватили блок целиком, вместе со знаками
    if (lines.length >= 2 && FENCE.test(lines[0]) && FENCE.test(lines[lines.length - 1])) {
      const bare = lines.slice(1, -1).join('\n')
      return put(text, lineStart, lineEnd, bare, lineStart, lineStart + bare.length)
    }

    // Забор вокруг выделения: курсор стоит внутри уже готового блока
    const prevEnd = lineStart - 1
    const prevStart = prevEnd > 0 ? text.lastIndexOf('\n', prevEnd - 1) + 1 : 0
    const nextStart = lineEnd + 1
    const nextEnd = text.indexOf('\n', nextStart) < 0 ? text.length : text.indexOf('\n', nextStart)
    if (lineStart > 0 && nextStart <= text.length &&
        FENCE.test(text.slice(prevStart, prevEnd)) && FENCE.test(text.slice(nextStart, nextEnd))) {
      const body = text.slice(lineStart, lineEnd)
      return put(text, prevStart, nextEnd, body, prevStart, prevStart + body.length)
    }

    const body = text.slice(lineStart, lineEnd)
    const block = `\`\`\`\n${body}\n\`\`\``
    put(text, lineStart, lineEnd, block, lineStart + 4, lineStart + 4 + body.length)
  }

  const onKeyDown = (e) => {
    // Поле многострочное: Enter — перенос, сохраняет Ctrl+Enter
    if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) {
      e.preventDefault()
      if (!saving) onSave?.()
    }
    // Esc гасим здесь: иначе слушатель окна закроет всю модалку вместе с правкой
    if (e.key === 'Escape') {
      e.stopPropagation()
      onCancel?.()
    }
    // Привычные сочетания браузер отдаёт нам: в textarea они ничего не значат,
    // а руки их всё равно набирают. Русская раскладка даёт те же клавиши
    if ((e.ctrlKey || e.metaKey) && !e.altKey) {
      if (e.key === 'b' || e.key === 'и') { e.preventDefault(); applyWrap('**') }
      if (e.key === 'i' || e.key === 'ш') { e.preventDefault(); applyWrap('*') }
    }
  }

  const rows = Math.min(maxRows, Math.max(minRows, (value || '').split('\n').length + 1))

  return (
    <>
      <div className="flex items-center gap-3 mb-1 text-[11px] text-zinc-500">
        {/* Набор намеренно короткий: панель нужна, чтобы не вспоминать синтаксис,
            а не чтобы заменить markdown интерфейсом */}
        {!preview && (
          <div className="flex items-center gap-1">
            <button onClick={() => applyWrap('**')} title="Жирный (Ctrl+B)"
                    className={`${MARKUP_BUTTON} font-bold`}>B</button>
            <button onClick={() => applyWrap('*')} title="Курсив (Ctrl+I)"
                    className={`${MARKUP_BUTTON} italic font-serif`}>I</button>
            <button onClick={() => applyWrap('`')} title="Код"
                    className={`${MARKUP_BUTTON} font-mono`}>`</button>
            <button onClick={applyCodeBlock} title="Блок кода"
                    className={`${MARKUP_BUTTON} w-auto px-1.5 font-mono text-[10px]`}>```</button>
            <button onClick={applyList} title="Список"
                    className={MARKUP_BUTTON}>≡</button>
            <button onClick={applyHeading} title="Подзаголовок"
                    className={`${MARKUP_BUTTON} font-mono`}>#</button>
            <button onClick={() => applyWrap('[', '](url)')} title="Ссылка"
                    className={`${MARKUP_BUTTON} w-auto px-1.5`}>ссылка</button>
          </div>
        )}
        {/* Правят сырой markdown — результат надо видеть до сохранения */}
        <button className={`ml-auto ${preview ? TOGGLE : 'text-zinc-300'}`}
                onClick={() => onPreviewChange?.(false)}>
          Текст
        </button>
        <button className={preview ? 'text-zinc-300' : TOGGLE}
                onClick={() => onPreviewChange?.(true)}>
          Как выглядит
        </button>
      </div>

      {preview ? (
        // Рамка и отступы — как у поля, чтобы режимы не прыгали, но **без
        // подложки**: текст показывается стилями `.md-body` на фоне окна, то
        // есть ровно так, как задача выглядит в чтении
        <div className="rounded-lg border border-zinc-700 px-3 py-2">
          <ReactMarkdown remarkPlugins={[remarkGfm]} rehypePlugins={[rehypeNoteMeta]}
                         components={mdComponents}>
            {(value || '').trim() || '_(пусто)_'}
          </ReactMarkdown>
        </div>
      ) : (
        <textarea
          ref={ref}
          // Оформление — общее с полями формы создания задачи (fields.js):
          // светлая подложка, обычный шрифт, тот же кегль. Моноширинный markdown
          // не требует, а рядом с рендером он читался мельче и жёстче
          className={`${FORM_FIELD} leading-relaxed resize-y`}
          value={value}
          rows={rows}
          onChange={(e) => onChange(e.target.value)}
          onKeyDown={onKeyDown}
          autoFocus={autoFocus}
        />
      )}

      {/* Кнопки — справа под полем: правка кончается там же, где кончается
          строка текста, а слева остаётся место подсказке и ошибке */}
      <div className="flex items-center gap-2 mt-2">
        {error
          ? <span className="text-xs text-rose-400">{error}</span>
          : <span className="text-[11px] text-zinc-600">{hint}</span>}
        <button
          onClick={onSave}
          disabled={saving}
          className="ml-auto w-6 h-6 flex items-center justify-center text-base text-zinc-500
            hover:text-emerald-400 disabled:opacity-40"
          title="Сохранить (Ctrl+Enter)"
        >✓</button>
        <button
          onClick={onCancel}
          className="w-6 h-6 flex items-center justify-center text-base text-zinc-500 hover:text-rose-400"
          title="Отменить (Esc)"
        >✕</button>
      </div>
    </>
  )
}
