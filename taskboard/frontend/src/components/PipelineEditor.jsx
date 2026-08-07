import { useState } from 'react'
import { COLOR_STYLE } from '../statuses'
import { TASK_TYPES } from '../taskTypes'

// Роли действий: подписи бейджей рядом со статусом-целью
const ACTION_LABEL = {
  create: 'создание',
  pick: 'очередь',
  start: 'в работу',
  return: 'возврат',
  release_draft: 'черновик релиза',
  release_lock: 'в релиз',
}

// Поля формы действий. Обязательные заполнены всегда, необязательные нужны
// только тем, кто выпускает версии, — им доступен пустой вариант
const ACTION_FIELDS = [
  { name: 'create', title: 'Новая задача' },
  { name: 'start', title: 'Взять в работу' },
  { name: 'return', title: 'Вернуть после замечаний' },
  { name: 'pick', title: 'Брать работу из', empty: 'авто (перед «в работу»)' },
  { name: 'release_draft', title: 'Готовить заметки релиза', empty: 'не используется' },
  { name: 'release_lock', title: 'Отобрано в релиз', empty: 'не используется' },
]

// Требование читается человеком по формулировке, а не по идентификатору: тот
// служебный — им требование гасят и его пишут в файл задачи
const reqText = (req, predicates) =>
  req.ask || req.name || predicates?.[req.check]?.label || req.check || req.id

// Чем именно требование проверяется. Формулировка отвечает «что должно быть», но
// не «как это узнают»: по строке «проверку подтвердил человек» не видно, ждут
// нажатия человека или заполненной секции — а от этого зависит, кому её закрывать
const reqKind = (req, predicates) => {
  const spec = predicates?.[req.check]
  if (!spec) return req.check || 'неизвестная проверка'
  const param = spec.param ? String(req[spec.param] || '').trim() : ''
  // Фраза с параметром приходит из словаря готовым шаблоном: склеенная здесь из
  // подписи и значения читалась бы «секция есть в файле задачи: «Изменение…»»
  return param && spec.phrase ? spec.phrase.replace('{}', param) : spec.label
}

// Тип задачи по-человечески. Незнакомый ключ (правка конфига руками, чужой
// проект) показываем как есть: подставленное имя соседнего типа соврало бы
const typeLabel = (key) => TASK_TYPES[key]?.label || key

// Типы, к которым требование не относится. Чипы, а не поле ввода: `except_types`
// хранит ключи каталога, и набирать их руками — тот же промах, что писать
// «обсуждение» вместо `discussion`, только молча (исключение читается, ни на что
// не находится, требование применяется ко всем).
function TypeExceptions({ value, onChange }) {
  const chosen = value || []
  const toggle = (key) => onChange(chosen.includes(key)
    ? chosen.filter((k) => k !== key)
    : [...chosen, key])

  return (
    <div className="flex flex-wrap items-center gap-1">
      <span className="text-[10px] text-zinc-400 mr-0.5">Не касается задач:</span>
      {Object.entries(TASK_TYPES).map(([key, meta]) => (
        <button key={key} onClick={() => toggle(key)}
                title={chosen.includes(key)
                  ? `Требование не относится к задачам «${meta.label}»`
                  : `Исключить задачи «${meta.label}» из этого требования`}
                className={`text-[10px] rounded px-1.5 py-0.5 border ${chosen.includes(key)
                  ? 'border-amber-600/70 bg-amber-500/10 text-amber-300'
                  : 'border-zinc-600/60 text-zinc-500 hover:text-zinc-200'}`}>
          {meta.label}
        </button>
      ))}
    </div>
  )
}

// Форма своего требования. Список проверок приходит от бэкенда (`predicates`):
// зашитый в JS перечень разошёлся бы с движком молча, и редактор предлагал бы
// то, чего скрипт не умеет
function RequirementForm({ predicates, onAdd }) {
  const [check, setCheck] = useState('confirm')
  const [name, setName] = useState('')
  const [ask, setAsk] = useState('')
  const [id, setId] = useState('')
  const [skipTypes, setSkipTypes] = useState([])
  const spec = predicates?.[check] || {}
  const ready = id.trim() && (!spec.param || name.trim())

  const submit = () => {
    if (!ready) return
    onAdd({
      id: id.trim(), check,
      ...(spec.param ? { [spec.param]: name.trim() } : {}),
      ...(ask.trim() ? { ask: ask.trim() } : {}),
      ...(skipTypes.length ? { except_types: skipTypes } : {}),
    })
    setName(''); setAsk(''); setId(''); setSkipTypes([])
  }

  // Поля внутри раскрытой панели темнее её фона: панель светлее строки статуса,
  // и поля на ней должны читаться вложенными, а не сливаться с обычной формой
  const input = 'bg-zinc-900 border border-zinc-600/70 rounded px-2 py-1 text-xs focus:outline-none focus:border-sky-500'
  return (
    // Форма — свой блок: селект и поля под ним читались на одном уровне, и
    // связь «поля зависят от выбранной проверки» приходилось соображать
    <div className="rounded-md border border-zinc-600/50 bg-zinc-800/40 p-2 space-y-1.5">
      <div className="text-[10px] text-zinc-400">Добавить своё требование</div>
      <select className={`${input} w-full`} value={check}
              onChange={(e) => setCheck(e.target.value)}>
        {Object.entries(predicates || {}).map(([key, meta]) => (
          <option key={key} value={key}>{meta.label}</option>
        ))}
      </select>
      {spec.hint && <div className="text-[10px] text-zinc-400">{spec.hint}</div>}
      {/* Поля с отступом и линией слева: видно, что их состав задаёт выбор выше */}
      <div className="flex gap-1.5 pl-2.5 border-l-2 border-zinc-600/50">
        {spec.param && (
          <input className={`${input} flex-1`} value={name}
                 placeholder={spec.param_label}
                 onChange={(e) => setName(e.target.value)} />
        )}
        {/* Формулировка — не у всех проверок: у секции её заголовок и есть
            человеческий текст, и псевдоним к нему был бы тем же самым дважды.
            Где она нужна, решает словарь предикатов, а не вёрстка */}
        {spec.ask_label && (
          <input className={`${input} flex-1`} value={ask}
                 placeholder={spec.ask_label}
                 title="Этот текст вы увидите в списке требований и в диалоге переноса на доске"
                 onChange={(e) => setAsk(e.target.value)} />
        )}
        {/* Единственное место, где человек имеет дело со служебным именем: оно
            попадёт во frontmatter задачи (`confirmed: verified`), и осмысленное
            там читается лучше сгенерированного */}
        <input className={`${input} w-28`} value={id} placeholder="служебное имя"
               title="Под этим именем требование записывается в файл задачи — например verified. Латиницей, без пробелов"
               onChange={(e) => setId(e.target.value)} />
        <button onClick={submit} disabled={!ready}
                className="text-xs px-2 rounded border border-zinc-500/70 text-zinc-200 hover:border-sky-500 hover:text-white disabled:opacity-30">
          Добавить
        </button>
      </div>
      {/* Исключение — не про то, как проверять, а про то, кого проверять:
          поэтому вне блока полей предиката, они от выбора проверки не зависят */}
      <div className="pl-2.5">
        <TypeExceptions value={skipTypes} onChange={setSkipTypes} />
      </div>
    </div>
  )
}

// Редактор жизненного цикла: порядок статусов, цели действий скиллов и
// требования этапов. Порядок задаёт маршрут (что идёт за чем), а не запреты:
// прыжки вперёд и возвраты назад законны в любом случае.
export default function PipelineEditor({ pipeline, actions, catalog, sources, requires,
                                         predicates, onChange, onOpenHelp }) {
  const keys = pipeline.map((s) => s.key)
  const available = catalog.filter((c) => !keys.includes(c.key))

  // Чем заполнен маршрут — видно в поле выбора. Ручная правка сбрасывает его:
  // после неё в форме уже не тот маршрут, что у источника (и заодно источник
  // можно выбрать повторно, чтобы вернуться к нему)
  const [picked, setPicked] = useState('')

  // Подписи и требования передают только те правки, которые их меняют:
  // у остальных ключ приходит `undefined`, и форма оставляет своё. Иначе
  // перестановка статуса стрелкой стирала бы подставленное источником
  const emit = (nextPipeline, nextActions = actions, nextStatuses, nextRequires) => {
    setPicked('')
    onChange({ pipeline: nextPipeline, actions: nextActions,
               statuses: nextStatuses, requires: nextRequires })
  }

  // Готовый маршрут подставляется в форму, а не сохраняется: дальше его можно
  // поправить, а применится он обычным «Сохранить» — с переносом задач из
  // выключаемых статусов, как при ручной правке
  const applySource = (value) => {
    const source = sources?.[Number(value)]
    if (!source) return
    // Требования — часть жизненного цикла, как подписи: копируя маршрут соседнего
    // проекта, человек ждёт и его проверок. Пустые у источника (пресет их не
    // несёт) значат «требований нет» и старые вытесняют — маршрут заменён целиком
    emit(source.pipeline, source.actions, source.statuses || {}, source.requires || {})
    setPicked(value)
  }

  const move = (index, delta) => {
    const next = [...pipeline]
    const target = index + delta
    if (target < 0 || target >= next.length) return
    ;[next[index], next[target]] = [next[target], next[index]]
    emit(next)
  }

  const remove = (index) => {
    const dropped = pipeline[index].key
    const next = pipeline.filter((_, i) => i !== index)
    // Действие не может указывать на выключенный статус — сбрасываем на авто
    const cleaned = Object.fromEntries(
      Object.entries(actions).filter(([, value]) => value !== dropped))
    emit(next, cleaned)
  }

  // Статус из каталога встаёт на своё каноническое место, а не в конец:
  // local_testing между разработкой и ревью, cancelled — последним.
  // Порядок потом всё равно можно поправить стрелками
  const add = (key) => {
    const meta = catalog.find((c) => c.key === key)
    if (!meta) return
    const order = catalog.map((c) => c.key)
    const at = pipeline.findIndex((s) => {
      const i = order.indexOf(s.key)
      return i > order.indexOf(key)  // кастомные статусы (-1) не сдвигаем
    })
    const next = [...pipeline]
    next.splice(at === -1 ? next.length : at, 0, meta)
    emit(next)
  }

  const setAction = (name, value) =>
    emit(pipeline, { ...actions, [name]: value || undefined })

  // Требования этапа. Открыт один статус за раз: развёрнутые все сразу
  // превращают редактор в простыню, а настраивают их по одному
  const [openReq, setOpenReq] = useState(null)

  const emitRequires = (next) => {
    setPicked('')
    onChange({ pipeline, actions, requires: next })
  }

  const declaredOn = (key) => (requires || {})[key] || []

  // Рекомендации каталога **материализуются** в конфиг проекта, а не действуют
  // «на лету»: обновление инструмента не должно включать проверки у тех, кто их
  // не объявлял. Уже объявленные (по id) из подсказок убираем
  const recommendedOn = (status) => {
    const ids = declaredOn(status.key).map((r) => String(r.id || '').toLowerCase())
    return (status.recommends || [])
      .filter((r) => !ids.includes(String(r.id || '').toLowerCase()))
  }

  const addRequirement = (key, req) => {
    const next = { ...(requires || {}) }
    next[key] = [...declaredOn(key), req]
    emitRequires(next)
  }

  const dropRequirement = (key, id) => {
    const next = { ...(requires || {}) }
    const left = declaredOn(key).filter((r) => r.id !== id)
    if (left.length) next[key] = left
    else delete next[key]
    emitRequires(next)
  }

  // Исключение правится у уже объявленного требования, а не только задаётся при
  // создании: требование живёт дольше формы, и понимание «это обсуждений не
  // касается» приходит позже — обычно на первом ложном отказе
  const setExceptTypes = (key, id, types) => {
    const next = { ...(requires || {}) }
    next[key] = declaredOn(key).map((r) => {
      if (r.id !== id) return r
      // Пустой список не храним: ключа нет — требование ко всем типам, и лишний
      // `except_types: []` в конфиге читался бы как незаконченная настройка
      const { except_types: _drop, ...rest } = r
      return types.length ? { ...rest, except_types: types } : rest
    })
    emitRequires(next)
  }

  // Какое исключение раскрыто. Один за раз: чипы всех требований сразу
  // превращают список в простыню, а правят их по одному
  const [openExcept, setOpenExcept] = useState('')

  const label = 'block text-xs text-zinc-500 mb-1'
  const field = 'w-full bg-zinc-800 border border-zinc-700 rounded-lg px-2 py-1.5 text-sm focus:outline-none focus:border-sky-500'

  const presets = (sources || []).filter((s) => s.kind === 'preset')
  const projects = (sources || []).filter((s) => s.kind === 'project')

  return (
    <div className="space-y-3">
      {sources?.length > 0 && (
        <div>
          <span className={label}>Взять готовый маршрут</span>
          <select className={field} value={picked}
                  onChange={(e) => applySource(e.target.value)}>
            <option value="">Заполнить из…</option>
            {presets.length > 0 && (
              <optgroup label="Пресеты">
                {presets.map((s) => (
                  <option key={s.name} value={sources.indexOf(s)}>
                    {s.name} — {s.hint}
                  </option>
                ))}
              </optgroup>
            )}
            {projects.length > 0 && (
              <optgroup label="Другие проекты">
                {projects.map((s) => (
                  <option key={s.name} value={sources.indexOf(s)}>
                    {s.name} ({s.pipeline.map((p) => p.label).join(' → ')})
                  </option>
                ))}
              </optgroup>
            )}
          </select>
          <div className="text-[11px] text-zinc-600 mt-1">
            Подставит статусы, действия и требования этапов в форму — сохранение
            по кнопке ниже
          </div>
        </div>
      )}

      <div>
        <span className={label}>Статусы по порядку — он задаёт ожидаемый маршрут задачи</span>
        <div className="space-y-1">
          {pipeline.map((status, i) => {
            const style = COLOR_STYLE[status.color] || COLOR_STYLE.zinc
            const roles = Object.entries(actions)
              .filter(([, value]) => value === status.key)
              .map(([name]) => ACTION_LABEL[name])
              .filter(Boolean)
            const declared = declaredOn(status.key)
            const recommended = recommendedOn(status)
            const open = openReq === status.key
            return (
              <div key={status.key}
                   className="bg-zinc-800/50 border border-zinc-700/60 rounded-lg px-2 py-1.5">
                <div className="flex items-center gap-2">
                  <span className={`w-2 h-2 rounded-full shrink-0 ${style.dot}`} />
                  <span className="text-sm truncate">{status.label}</span>
                  <span className="text-[11px] text-zinc-600 truncate">{status.key}</span>
                  {status.offramp && (
                    <span className="text-[10px] text-zinc-500 border border-zinc-700 rounded px-1">
                      вне маршрута
                    </span>
                  )}
                  {roles.map((role) => (
                    <span key={role}
                          className="text-[10px] text-sky-300 border border-sky-800/60 rounded px-1">
                      {role}
                    </span>
                  ))}
                  <div className="ml-auto flex items-center gap-0.5 shrink-0">
                    {/* Съезд с маршрута требований не имеет: из отмены не выходят */}
                    {!status.offramp && (
                      <button onClick={() => setOpenReq(open ? null : status.key)}
                              title="Что должно быть выполнено, чтобы уйти с этапа"
                              className={`px-1.5 mr-2 text-xs rounded ${
                                open ? 'bg-zinc-700/60 text-zinc-100' :
                                declared.length ? 'text-amber-300' : 'text-zinc-500'
                              } hover:text-zinc-200`}>
                        ✓{declared.length || ''}
                        {recommended.length > 0 && <span className="text-sky-400">•</span>}
                      </button>
                    )}
                    <button onClick={() => move(i, -1)} disabled={i === 0}
                            title="Выше"
                            className="px-1.5 text-zinc-500 hover:text-zinc-200 disabled:opacity-30">↑</button>
                    <button onClick={() => move(i, 1)} disabled={i === pipeline.length - 1}
                            title="Ниже"
                            className="px-1.5 text-zinc-500 hover:text-zinc-200 disabled:opacity-30">↓</button>
                    <button onClick={() => remove(i)}
                            title={`Убрать «${status.label}» из маршрута — это не сворачивание, статус исчезнет из пайплайна`}
                            className="px-1.5 text-zinc-500 hover:text-rose-400">✕</button>
                  </div>
                </div>

                {open && (
                  // Панель светлее строки статуса: раскрытая часть — отдельный
                  // слой, и на одном оттенке с формой она не считывалась вовсе
                  <div className="mt-2 -mx-1 px-3 py-2.5 rounded-lg bg-zinc-700/30 border border-zinc-600/40 space-y-2">
                    {/* Сказано вслух: требование проверяется на выходе, а не на
                        входе. На живой настройке это сбивало дважды — человек
                        ждал срабатывания при переносе в статус */}
                    {/* Своя кнопка «Свернуть»: без неё рука ищет ближайшее
                        похожее на закрытие — крестик в ряду выше, а он убирает
                        статус из маршрута, и список схлопывается ровно как от
                        сворачивания */}
                    <div className="flex items-start gap-2">
                      <div className="text-[11px] text-zinc-300">
                        Чтобы уйти с этапа «{status.label}», должно быть выполнено:
                      </div>
                      <button onClick={() => setOpenReq(null)}
                              className="ml-auto shrink-0 text-[10px] text-zinc-400 hover:text-zinc-200">
                        Свернуть
                      </button>
                    </div>
                    {declared.length === 0 && recommended.length === 0 && (
                      <div className="text-[11px] text-zinc-400">
                        ничего — этап проходится без проверок
                      </div>
                    )}
                    {declared.map((req) => {
                      const editing = openExcept === `${status.key}:${req.id}`
                      const skip = req.except_types || []
                      return (
                      // Служебное имя (`id`) в строке не показываем: человек с ним
                      // дела не имеет — оно нужно агенту и всплывает во frontmatter
                      // задачи. Остаётся в подсказке, чтобы было где посмотреть
                      <div key={req.id}>
                        <div className="flex items-center gap-2 text-xs"
                             title={`Служебное имя: ${req.id} — под ним требование записывается в файл задачи`}>
                          <span className="text-emerald-400 shrink-0">✓</span>
                          <span className="truncate">{reqText(req, predicates)}</span>
                          {/* Чем проверяется и к кому не относится — видно в строке:
                              иначе требование в списке не самодокументируемо, и
                              понять его можно только вспомнив, как заводил */}
                          <span className="text-[10px] text-zinc-300 border border-zinc-500/70 rounded px-1 shrink-0">
                            {reqKind(req, predicates)}
                          </span>
                          {/* Кнопка, а не подпись, и она есть всегда: раньше бейдж
                              «кроме: …» только читался, а задать исключение было
                              нечем — приходилось править tasks/.taskboard.json.
                              «Все типы» без исключений тоже сказано вслух, иначе
                              настройку не найти, пока не увидишь у чужого требования */}
                          <button onClick={() => setOpenExcept(editing ? '' : `${status.key}:${req.id}`)}
                                  title="Типы задач, к которым требование не относится"
                                  className={`text-[10px] rounded px-1 shrink-0 border hover:border-sky-500 ${
                                    skip.length ? 'border-amber-700/70 text-amber-300/90'
                                                : 'border-zinc-700 text-zinc-500'}`}>
                            {skip.length ? `кроме: ${skip.map(typeLabel).join(', ')}` : 'все типы'}
                          </button>
                          <button onClick={() => dropRequirement(status.key, req.id)}
                                  title="Убрать требование"
                                  className="ml-auto px-1 text-zinc-500 hover:text-rose-400 shrink-0">✕</button>
                        </div>
                        {editing && (
                          <div className="mt-1 mb-1.5 pl-5">
                            <TypeExceptions value={skip}
                                            onChange={(types) => setExceptTypes(status.key, req.id, types)} />
                          </div>
                        )}
                      </div>
                      )
                    })}
                    {/* Откуда взялась строка, должно быть видно без наведения:
                        иначе рекомендация читается как чьё-то забытое требование */}
                    {recommended.length > 0 && (
                      <div className="text-[11px] text-zinc-300 pt-0.5">
                        Рекомендуется для этого этапа:
                      </div>
                    )}
                    {recommended.map((req) => (
                      <div key={req.id} className="flex items-center gap-2 text-xs text-zinc-400">
                        <span className="text-sky-400 shrink-0">•</span>
                        <span className="truncate">{reqText(req, predicates)}</span>
                        <span className="text-[10px] text-zinc-400 border border-zinc-600/70 rounded px-1 shrink-0">
                          {reqKind(req, predicates)}
                        </span>
                        {/* Исключения поставки видно до включения: у рекомендаций
                            релизного хвоста это «кроме: Обсуждение», и знать об
                            этом лучше заранее, а не по отсутствию долга */}
                        {req.except_types?.length > 0 && (
                          <span className="text-[10px] text-zinc-500 border border-zinc-700 rounded px-1 shrink-0"
                                title="К задачам этих типов требование не относится">
                            кроме: {req.except_types.map(typeLabel).join(', ')}
                          </span>
                        )}
                        <button onClick={() => addRequirement(status.key, req)}
                                className="ml-auto shrink-0 text-[11px] px-1.5 rounded border border-zinc-500/70 text-zinc-200 hover:border-sky-500 hover:text-white">
                          Сделать обязательным
                        </button>
                      </div>
                    ))}
                    <RequirementForm predicates={predicates}
                                     onAdd={(req) => addRequirement(status.key, req)} />
                  </div>
                )}
              </div>
            )
          })}
        </div>
      </div>

      {available.length > 0 && (
        <div>
          <span className={label}>Добавить статус</span>
          <select className={field} value="" onChange={(e) => add(e.target.value)}>
            <option value="">Выберите статус…</option>
            {available.map((c) => (
              <option key={c.key} value={c.key}>{c.label} ({c.key})</option>
            ))}
          </select>
        </div>
      )}

      <div className="grid grid-cols-2 gap-3">
        {ACTION_FIELDS.map(({ name, title, empty }) => (
          <div key={name}>
            <span className={label}>{title}</span>
            <select className={field} value={actions[name] || ''}
                    onChange={(e) => setAction(name, e.target.value)}>
              {empty && <option value="">{empty}</option>}
              {pipeline.map((s) => (
                <option key={s.key} value={s.key}>{s.label}</option>
              ))}
            </select>
          </div>
        ))}
      </div>

      <div className="text-[11px] text-zinc-600 space-y-1">
        <div>
          Порядок — ожидаемый маршрут задачи, а не запрет: шаг через статус и возврат
          назад законны. Скиллы берут цели отсюда, поэтому выключенный статус исчезает
          и из их вариантов.
        </div>
        <div>
          Выключение статуса с задачами спросит, куда их перенести.
          {onOpenHelp && (
            <button className="ml-1 underline hover:text-zinc-400"
                    onClick={() => onOpenHelp('lifecycle')}>
              Подробнее о жизненном цикле
            </button>
          )}
        </div>
      </div>
    </div>
  )
}
