import { useEffect, useState } from 'react'
import { api } from '../api'
import { FORM_FIELD } from '../fields'
import { useListKeys } from '../listKeys'
import { TASK_TYPES } from '../taskTypes'
import TaskPicker from './TaskPicker'
import MarkdownEditor from './MarkdownEditor'
import EpicField from './EpicField'

// Модалка создания задачи (вызов create_task.py через API).
// Рубрику бэклога выбирать не нужно: её задаёт тип задачи (TASK-124)
//
// `source` — копируемая задача: форма открывается предзаполненной её данными
// (TASK-128). Копия — это **новая работа с тем же содержанием**, поэтому берёт
// она только то, что писал человек: название, описание, критерии, тип, эпик и
// простой. Комментарии, история коммитов и подтверждения этапов принадлежат
// оригиналу, а не замыслу, и в копию не едут — как и его место на доске:
// копия начинает с бэклога, где начинают все
export default function NewTaskModal({ onClose, onCreated, source = null }) {
  const [form, setForm] = useState({
    title: source?.title || '',
    description: source?.description || '',
    criteria: source?.criteria || '',
    blocked_by: '',
    epic: source?.epic || '',
    epic_name: '',
    task_type: source?.task_type || 'feature',
    target: 'backlog',
    queue_position: 'end',
  })
  // Простой оригинала едет с копией, но остаётся видимым и снимаемым до
  // создания: он говорит, чего ждёт работа, а копию нередко заводят как раз
  // затем, чтобы не ждать
  const [inherited, setInherited] = useState({
    blocked_by: source?.blocked_by || [],
    paused: source?.paused || '',
  })
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)
  // Задача-блокер: поле хранит номер, но блокировать можно только существующую
  // задачу — иначе новая задача рождается со ссылкой в никуда
  const [blockedTask, setBlockedTask] = useState(null)
  // Ключ эпика, разобранный полем: в поле лежит «ключ · название» (читается и
  // служит подсказкой само), а на бэкенд уходит только ключ. Подсказки и имя
  // нового эпика — работа EpicField, общего с окном задачи
  const [epicKey, setEpicKey] = useState('')
  // Пресеты критериев: дефолт виден заранее (поле предзаполнено первым),
  // свой вариант можно сохранить — он появится во всех проектах
  const [presets, setPresets] = useState([])
  // Пользовательские пресеты: только их можно удалять (встроенные — поставка)
  const [customPresets, setCustomPresets] = useState([])
  const [presetsOpen, setPresetsOpen] = useState(false)
  // Предпросмотр описания: режим поля, а не всей формы
  const [descPreview, setDescPreview] = useState(false)

  useEffect(() => {
    api.criteriaPresets()
      .then((d) => {
        const items = d.presets || []
        setPresets(items)
        setCustomPresets(d.custom || [])
        // Не затираем уже введённый текст. У копии критерии — оригинала:
        // пустые там означают «их не писали», и дефолт подставил бы копии то,
        // чего в источнике нет
        setForm((f) => (f.criteria || source ? f : { ...f, criteria: items[0] || '' }))
      })
      .catch(() => { /* старый сервер без пресетов */ })
  }, [])

  const applyPresets = (d) => {
    setPresets(d.presets || [])
    setCustomPresets(d.custom || [])
  }

  const pickPreset = (preset) => {
    setForm({ ...form, criteria: preset })
    setPresetsOpen(false)
  }

  // Стрелки, Enter и Esc — общие для всех списков интерфейса
  const presetKeys = useListKeys({
    items: presets, open: presetsOpen,
    onPick: pickPreset, onClose: () => setPresetsOpen(false),
  })

  // Подсказки эпика — кастомный тёмный список (как у пресетов критериев), а не
  // нативный datalist: браузер рисует его белым, и он выбивается из темы.
  // Свободный ввод сохраняется: список лишь подсказывает известные ключи
  useEffect(() => {
    const onKey = (e) => e.key === 'Escape' && !busy && onClose()
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose, busy])

  const set = (key) => (e) => setForm({ ...form, [key]: e.target.value })

  const submit = async () => {
    if (!form.title.trim()) {
      setError('Название обязательно')
      return
    }
    if (form.blocked_by.trim() && !blockedTask) {
      setError('Задача-блокер не найдена — выберите её из списка')
      return
    }
    setBusy(true)
    setError(null)
    try {
      // Унаследованные блокеры и выбранный в поле — один список: у задачи
      // блокировка одна на всех, а не «своя» и «копированная»
      const blockers = [...new Set(
        [...inherited.blocked_by, form.blocked_by.trim()].filter(Boolean))]
      const payload = {
        ...form,
        title: form.title.trim(),
        blocked_by: blockers.join(', '),
        paused: inherited.paused,
      }
      // В поле может лежать «ключ · название» — на бэкенд уходит только ключ
      if (epicKey) payload.epic = epicKey
      const result = await api.createTask(payload)
      onCreated(result.id)
      onClose()
    } catch (e) {
      setError(e.message)
    } finally {
      setBusy(false)
    }
  }

  // Оформление полей — общее (fields.js): те же поля правятся в карточке задачи
  const field = FORM_FIELD
  const radio = 'accent-sky-500'

  return (
    <div
      className="fixed inset-0 bg-black/60 backdrop-blur-sm flex items-center justify-center z-50 p-4"
    >
      {/* Высота ограничена экраном, а поля прокручиваются внутри: описание
          растёт под текст (у копии — под текст оригинала), и без предела окно
          уезжало шапкой и кнопками за края экрана. Ширины взамен дано больше:
          длинные строки лучше уложить, чем удлинять окно */}
      <div
        className="bg-zinc-900 border border-zinc-700 rounded-2xl w-full max-w-2xl max-h-[90vh]
          flex flex-col shadow-2xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="shrink-0 px-5 py-4 border-b border-zinc-800 text-lg font-semibold">
          {/* Копия — тоже новая задача, но человек должен видеть, с чего она
              списана: поля предзаполнены, и без номера непонятно, откуда */}
          {source ? <>Копия задачи <span className="font-mono text-base text-zinc-400">{source.id}</span></> : 'Новая задача'}
        </div>

        <div className="flex-1 overflow-y-auto px-5 py-4 space-y-3">
          <input className={field} placeholder="Название (обязательно)" value={form.title} onChange={set('title')} autoFocus />
          {/* Тот же редактор, что и в открытой задаче: разметку негде было
              подсмотреть, а результат — увидеть до создания задачи. Кнопки поля
              скрыты (создаёт и отменяет форма), Ctrl+Enter создаёт задачу.
              Текст уходит в файл дословно, поэтому предпросмотр показывает
              ровно то, что там окажется */}
          <MarkdownEditor
            value={form.description}
            onChange={(v) => setForm({ ...form, description: v })}
            onSave={submit}
            onCancel={onClose}
            saving={busy}
            preview={descPreview}
            onPreviewChange={setDescPreview}
            autoFocus={false}
            actions={false}
            minRows={5}
            maxRows={16}
            placeholder="Описание — что сделать и зачем. Абзацы через пустую строку, перечисления списком"
            hint="Ctrl+Enter — создать; текст сохраняется как есть"
          />
          <div>
            <input className={field} placeholder="Критерии приёмки (опционально)" value={form.criteria} onChange={set('criteria')} />
            <div className="flex items-center gap-2 mt-1">
              {presets.length > 0 && (
                // Нажатие приходит от кнопки-переключателя: фокус после клика
                // остаётся на ней, поэтому слушатель стоит на обёртке
                <div className="relative" onKeyDown={presetKeys.onKeyDown}>
                  <button
                    type="button"
                    className="rounded-lg px-2 py-1 text-[11px] text-zinc-400 hover:text-zinc-200 border border-dashed border-zinc-600 hover:border-zinc-400 focus:outline-none"
                    onClick={() => setPresetsOpen(!presetsOpen)}
                  >
                    Заполнить критерий приёмки из пресета ▾
                  </button>
                  {presetsOpen && (
                    <>
                      {/* Прозрачная подложка: клик мимо закрывает список */}
                      <div className="fixed inset-0 z-10" onClick={() => setPresetsOpen(false)} />
                      <div className="absolute z-20 mt-1 w-72 max-h-48 overflow-y-auto bg-zinc-800 border border-zinc-700 rounded-lg shadow-xl">
                        {presets.map((p, i) => (
                          <div key={p} className="flex items-center"
                               onMouseEnter={() => presetKeys.setActive(i)}>
                            <button
                              type="button"
                              className={`flex-1 text-left px-3 py-1.5 text-xs text-zinc-300
                                ${i === presetKeys.active ? 'bg-zinc-700' : ''}`}
                              onClick={() => pickPreset(p)}
                            >
                              {p}
                            </button>
                            {customPresets.includes(p) && (
                              <button
                                type="button"
                                title="Удалить пресет"
                                className="px-2 text-zinc-500 hover:text-rose-400"
                                onClick={async () => {
                                  try {
                                    applyPresets(await api.deleteCriteriaPreset(p))
                                  } catch { /* список просто останется прежним */ }
                                }}
                              >
                                ×
                              </button>
                            )}
                          </div>
                        ))}
                      </div>
                    </>
                  )}
                </div>
              )}
              {form.criteria.trim() && !presets.includes(form.criteria.trim()) && (
                <button
                  type="button"
                  className="text-[11px] text-sky-400 hover:text-sky-300"
                  onClick={async () => {
                    try {
                      applyPresets(await api.saveCriteriaPreset(form.criteria.trim()))
                    } catch { /* не критично: пресет просто не сохранится */ }
                  }}
                >
                  Сохранить как пресет
                </button>
              )}
            </div>
          </div>
          {/* Поле формы оформляет форма: в колонке крупных полей компактное
              смотрелось бы чужим */}
          <TaskPicker
            value={form.blocked_by}
            onChange={(v, found) => { setBlockedTask(found); setForm({ ...form, blocked_by: v }) }}
            inputClassName={field}
            placeholder="Заблокировано задачей (TASK-NNN, опционально)"
          />

          {/* Простой, доставшийся от оригинала. Показываем до создания и даём
              снять: иначе копия молча рождается стоящей, а узнаётся это уже на
              доске — по маркеру, который никто не ставил */}
          {source && (inherited.blocked_by.length > 0 || inherited.paused) && (
            <div className="flex flex-wrap items-center gap-2 text-[11px]">
              <span className="text-zinc-500">От {source.id}:</span>
              {inherited.blocked_by.map((id) => (
                <span key={id}
                      className="flex items-center gap-1 rounded-md border border-zinc-700 px-2 py-0.5 text-zinc-300">
                  ждёт <span className="font-mono">{id}</span>
                  <button
                    type="button"
                    title="Не наследовать блокировку"
                    className="text-zinc-500 hover:text-rose-400"
                    onClick={() => setInherited({
                      ...inherited,
                      blocked_by: inherited.blocked_by.filter((b) => b !== id),
                    })}
                  >
                    ×
                  </button>
                </span>
              ))}
              {inherited.paused && (
                <span className="flex items-center gap-1 max-w-full rounded-md border border-zinc-700 px-2 py-0.5 text-zinc-300">
                  <span className="truncate" title={inherited.paused}>пауза: {inherited.paused}</span>
                  <button
                    type="button"
                    title="Не наследовать паузу"
                    className="text-zinc-500 hover:text-rose-400"
                    onClick={() => setInherited({ ...inherited, paused: '' })}
                  >
                    ×
                  </button>
                </span>
              )}
            </div>
          )}

          <EpicField
            value={form.epic}
            name={form.epic_name}
            onChange={(text, key) => { setForm({ ...form, epic: text }); setEpicKey(key) }}
            onNameChange={(value) => setForm({ ...form, epic_name: value })}
            inputClassName={field}
          />

          <div className="flex gap-3">
            <label className="flex-1 text-xs text-zinc-500">
              Тип
              {/* Список типов — из общего каталога: второй, вписанный в форму,
                  разъехался бы со скриптом при первом же новом типе */}
              <select className={`${field} mt-1`} value={form.task_type} onChange={set('task_type')}>
                {Object.entries(TASK_TYPES).map(([key, meta]) => (
                  <option key={key} value={key}>{meta.label}</option>
                ))}
              </select>
            </label>
            <div className="flex-1 text-xs text-zinc-500">
              Куда
              <div className={`${field} mt-1 flex items-center gap-3`}>
                <label className="flex items-center gap-1.5 text-sm text-zinc-300 cursor-pointer">
                  <input
                    type="radio"
                    name="target"
                    checked={form.target === 'backlog'}
                    onChange={() => setForm({ ...form, target: 'backlog' })}
                    className={radio}
                  />
                  Бэклог
                </label>
                <label className="flex items-center gap-1.5 text-sm text-zinc-300 cursor-pointer">
                  <input
                    type="radio"
                    name="target"
                    checked={form.target === 'queue'}
                    onChange={() => setForm({ ...form, target: 'queue' })}
                    className={radio}
                  />
                  В очередь
                </label>
              </div>
            </div>
          </div>

          {form.target === 'queue' && (
            <label className="block text-xs text-zinc-500">
              Позиция в очереди
              <select className={`${field} mt-1`} value={form.queue_position} onChange={set('queue_position')}>
                <option value="end">В конец очереди</option>
                <option value="start">В начало очереди</option>
              </select>
            </label>
          )}

          {error && <div className="text-sm text-rose-400">{error}</div>}
        </div>

        <div className="shrink-0 px-5 py-4 border-t border-zinc-800 flex justify-end gap-2">
          <button onClick={onClose} className="px-4 py-2 text-sm text-zinc-400 hover:text-zinc-200">
            Отмена
          </button>
          <button
            onClick={submit}
            disabled={busy}
            className="px-4 py-2 text-sm font-medium bg-sky-600 hover:bg-sky-500 disabled:opacity-50 rounded-lg"
          >
            {busy ? 'Создаю…' : (source ? 'Создать копию' : 'Создать')}
          </button>
        </div>
      </div>
    </div>
  )
}
