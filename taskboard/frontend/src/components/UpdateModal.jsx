import { useEffect, useState } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { api } from '../api'
import { mdComponents } from '../markdown'
import CopyButton from './CopyButton'

// Окно обновления: какая версия стоит, вышла ли новая и как обновиться.
//
// Инструмент до сих пор был полностью локальным, поэтому проверка обновлений —
// единственное место, где он выходит в сеть, и включается она только с согласия.
// Пока согласия нет, окно показывает вопрос, а не молча ходит на сервер.
//
// Обновление применяется кнопкой, но не этим процессом: сервер выходит,
// а git-операцию выполняет дочерний лаунчер (он же ставит зависимости и
// проверяет результат). Кнопка показывается, только если обновление сейчас
// возможно; иначе на её месте — причины и команда для ручного выполнения.

function formatChecked(ts) {
  if (!ts) return null
  try {
    return new Date(ts * 1000).toLocaleString('ru-RU', {
      day: '2-digit', month: '2-digit', year: 'numeric',
      hour: '2-digit', minute: '2-digit',
    })
  } catch { return null }
}

// Режимы проверки. Показываются всегда: пользователь должен видеть, ходит ли
// инструмент в сеть, и уметь это изменить, не разыскивая настройку
const MODES = [
  { id: 'auto', label: 'Автоматически', hint: 'Раз в сутки, фоном.' },
  { id: 'manual', label: 'Вручную', hint: 'Приложение само в сеть не выходит — только по кнопке «Проверить сейчас».' },
  { id: 'off', label: 'Выключена', hint: 'Проверка отключена — приложение в сеть не выходит. Обновиться вручную по-прежнему можно.' },
]

// Сколько выпусков показывать сразу после обновления. Пропустивший год
// получил бы стену текста; остальные доступны кнопкой
const NEWS_SHOWN = 5

// Что делать пользователю, у которого обновиться одной командой нельзя
const INSTALL_HINT = {
  nogit: 'Копия развёрнута из git, но сам git в PATH не найден — установите его ' +
    'или обновитесь вручную, скачав новую версию.',
  plain: 'Копия развёрнута из архива, а не из git — обновление одной командой ' +
    'недоступно: скачайте новую версию и распакуйте её поверх, сохранив папку ' +
    'taskboard/.venv (она пересоздастся сама, если её потерять).',
}

export default function UpdateModal({ onClose, onOpenSettings }) {
  const [status, setStatus] = useState(null)
  const [plan, setPlan] = useState(null)
  const [news, setNews] = useState(null)
  const [allNews, setAllNews] = useState(false)
  const [busy, setBusy] = useState(false)
  const [applying, setApplying] = useState(false)
  const [error, setError] = useState(null)

  useEffect(() => {
    // Только чтение кэша: открытие окна само по себе запросов наружу не шлёт
    api.updateStatus().then(setStatus).catch((e) => setError(e.message))
  }, [])

  // Готовность к обновлению спрашиваем, только когда есть что ставить:
  // проверка трогает git, и делать её просто так незачем
  useEffect(() => {
    if (!status?.update_available) { setPlan(null); return }
    api.updatePlan().then(setPlan).catch(() => setPlan(null))
  }, [status?.update_available, status?.latest?.version])

  // Что изменилось за всё пропущенное: обновление могло перепрыгнуть
  // несколько выпусков, и показать надо каждый, а не только последний.
  // Но и не все сразу: забывший про программу на год получит стену текста,
  // поэтому по умолчанию — свежие, остальные по кнопке
  useEffect(() => {
    const done = status?.last_result
    if (!done?.at || !done.ok) { setNews(null); return }
    api.changelog(done.from, allNews ? 0 : NEWS_SHOWN)
      .then((d) => setNews({ sections: d.sections || [], total: d.total || 0 }))
      .catch(() => setNews(null))
  }, [status?.last_result?.at, allNews])

  useEffect(() => {
    const onKey = (e) => e.key === 'Escape' && onClose()
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  const check = async () => {
    setBusy(true)
    setError(null)
    try {
      setStatus(await api.updateCheck())
    } catch (e) {
      setError(e.message)
    } finally {
      setBusy(false)
    }
  }

  // Ответ на вопрос о согласии. «Автоматически» заодно проверяет сразу,
  // иначе пользователь остался бы с пустым окном до следующего запуска
  const setMode = async (mode) => {
    setBusy(true)
    setError(null)
    try {
      await api.saveConfig({ update_check: mode })
      setStatus(mode === 'auto' ? await api.updateCheck() : await api.updateStatus())
    } catch (e) {
      setError(e.message)
    } finally {
      setBusy(false)
    }
  }

  // Обновление: сервер уходит вниз, лаунчер применяет git и поднимает его
  // заново. Ждём ровно того же, чего ждёт перезапуск из настроек
  const apply = async () => {
    setApplying(true)
    setError(null)
    try {
      await api.updateApply()
    } catch (e) {
      // Сервер мог умереть раньше, чем ответил, — это норма для этой операции
      if (!/fetch|network|failed/i.test(e.message)) {
        const blockers = e.blockers || e.detail?.blockers
        setError(blockers ? blockers.join('; ') : e.message)
        setApplying(false)
        return
      }
    }
    for (let i = 0; i < 180; i++) {
      await new Promise((r) => setTimeout(r, 1000))
      try {
        await api.health()
        location.reload()
        return
      } catch { /* ещё обновляется */ }
    }
    setError('Сервер не поднялся после обновления — запустите его вручную')
    setApplying(false)
  }

  const dismissResult = async () => {
    try { await api.updateSeen() } catch { /* не страшно */ }
    setStatus({ ...status, last_result: null })
  }

  const latest = status?.latest
  // Итог считаем настоящим только с отметкой времени: пустой объект в JS
  // истинен, и окно рисовало плашку о провале, которого не было
  const result = status?.last_result?.at ? status.last_result : null
  const checked = formatChecked(status?.checked_at)
  const btn = 'px-3 py-1.5 rounded-lg text-sm border border-zinc-700 text-zinc-300 ' +
    'hover:bg-zinc-800 hover:text-zinc-100 transition disabled:opacity-50'

  return (
    <div
      className="fixed inset-0 bg-black/60 backdrop-blur-sm flex items-center justify-center z-50 p-4"
      onClick={onClose}
    >
      <div
        className="bg-zinc-900 border border-zinc-700 rounded-2xl w-full max-w-2xl max-h-[85vh]
          flex flex-col shadow-2xl overflow-hidden"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center gap-3 px-5 py-3 border-b border-zinc-800">
          <div className="text-lg font-semibold text-zinc-300">Обновление</div>
          {status?.version && (
            <div className="text-xs text-zinc-500">установлена версия {status.version}</div>
          )}
          <button
            onClick={onClose}
            className="ml-auto text-zinc-400 hover:text-zinc-200 text-xl leading-none px-2"
            title="Закрыть (Esc)"
          >
            ×
          </button>
        </div>

        <div className="flex-1 overflow-y-auto px-5 py-4 text-sm text-zinc-300">
          {error && <div className="text-rose-400 mb-3">{error}</div>}
          {!status && !error && <div className="text-zinc-400">Загрузка…</div>}

          {/* Чем кончилось обновление: сервер к этому моменту уже перезапущен,
              и человек иначе не узнает ни об успехе, ни о провале с откатом */}
          {result && (
            <div className={`mb-4 rounded-xl border p-4 ${result.ok
              ? 'border-emerald-800/60 bg-emerald-950/20'
              : 'border-rose-800/60 bg-rose-950/20'}`}>
              <div className={`font-medium mb-1 ${result.ok ? 'text-emerald-300' : 'text-rose-300'}`}>
                {result.ok
                  ? `Обновлено до версии ${result.version}`
                  : 'Обновление не применилось'}
              </div>
              {!result.ok && result.error && (
                <p className="text-zinc-400">{result.error}</p>
              )}

              {/* Все пропущенные выпуски, а не только последний: обновление
                  могло перепрыгнуть несколько версий */}
              {result.ok && news?.sections?.length > 0 && (
                <>
                  {news.total > news.sections.length && (
                    <p className="mt-2 text-zinc-400">
                      Вы пропустили версий: {news.total}. Ниже — последние
                      {' '}{news.sections.length}.
                    </p>
                  )}
                  <div className="mt-3 space-y-3 max-h-72 overflow-y-auto pr-1">
                    {news.sections.map((s) => (
                      <div key={s.version}>
                        <div className="text-zinc-300">
                          {s.version}
                          {s.date && <span className="text-zinc-500 text-xs ml-2">{s.date}</span>}
                        </div>
                        <div className="md-body md-tint-zinc text-sm mt-1">
                          <ReactMarkdown remarkPlugins={[remarkGfm]} components={mdComponents}>
                            {s.body}
                          </ReactMarkdown>
                        </div>
                      </div>
                    ))}
                  </div>
                  {news.total > news.sections.length && (
                    <button className={`${btn} mt-2`} onClick={() => setAllNews(true)}>
                      Показать все {news.total}
                    </button>
                  )}
                </>
              )}

              {/* Требования этапа включает человек — обновление не трогает чужие
                  проекты молча. Обратная сторона: узнать о механизме неоткуда,
                  а возможность, о которой надо догадаться, для большинства не
                  существует. Плашка уже показывается один раз и сама себя гасит,
                  поэтому нового места здесь не заводится — только путь отсюда
                  туда, где настраивают */}
              <div className="mt-3 flex flex-wrap gap-2">
                <button className={btn} onClick={dismissResult}>Понятно</button>
                {/* Заливкой, а не рамкой: рядом с «Понятно» одинаковая кнопка
                    читается вторым способом закрыть плашку, а это единственное
                    место, откуда про механизм вообще узнают */}
                {result.ok && onOpenSettings && (
                  <button
                    className="px-3 py-1.5 rounded-lg text-sm border border-sky-700/70
                      bg-sky-950/40 text-sky-200 hover:bg-sky-900/50 hover:text-sky-100
                      transition"
                    onClick={() => { dismissResult(); onOpenSettings('lifecycle') }}>
                    Настроить требования этапов →
                  </button>
                )}
              </div>
            </div>
          )}

          {/* Пояснение показывается, пока пользователь не ответил. Ответ —
              любой из режимов ниже, в том числе «вручную»: иначе вопрос висел бы
              вечно у того, кто просто нажал «проверить» */}
          {status?.mode === 'ask' && (
            <div className="mb-4 rounded-xl border border-sky-800/60 bg-sky-950/30 p-4">
              <div className="font-medium text-sky-200 mb-1">Проверять обновления?</div>
              <p className="text-zinc-400">
                Taskmark работает локально и в сеть не выходит. Чтобы узнавать о новых
                версиях, ему нужно раз в сутки запрашивать один файл с описанием релиза.
                Наружу уходит только сам запрос: ни о проекте, ни о задачах, ни о вас
                не сообщается ничего. Выберите режим ниже — решение можно изменить в
                любой момент.
              </p>
            </div>
          )}

          {status && (
            <div className="mb-4">
              <div className="text-zinc-400 mb-2">Проверка обновлений</div>
              <div className="flex flex-wrap gap-2">
                {MODES.map(({ id, label, hint }) => (
                  <button
                    key={id}
                    disabled={busy}
                    onClick={() => setMode(id)}
                    title={hint}
                    className={`px-3 py-1.5 rounded-lg text-sm border transition disabled:opacity-50
                      ${status.mode === id
                        ? 'border-sky-600 bg-sky-950/50 text-sky-200'
                        : 'border-zinc-700 text-zinc-400 hover:bg-zinc-800 hover:text-zinc-200'}`}
                  >
                    {label}
                  </button>
                ))}
              </div>
              <p className="text-xs text-zinc-500 mt-2">
                {MODES.find((m) => m.id === status.mode)?.hint
                  || 'Режим не выбран — фоновых запросов нет.'}
              </p>
            </div>
          )}

          {status && status.mode !== 'ask' && !status.update_available && !status.error && (
            <div className={latest ? 'text-emerald-400' : 'text-zinc-400'}>
              {latest
                ? 'У вас последняя версия.'
                : 'Сведений о новых версиях пока нет — нажмите «Проверить сейчас».'}
            </div>
          )}

          {status?.update_available && (
            <div>
              {/* Цвета из гаммы приложения: emerald — как подтверждение у кнопки
                  копирования, amber — как маркер стоящих задач на доске */}
              <div className="text-base text-amber-300 mb-1">
                Доступна версия {latest.version}
                {latest.date && <span className="text-zinc-400 text-sm ml-2">от {latest.date}</span>}
              </div>

              {/* Кнопка — только когда обновление действительно возможно.
                  Иначе показываем, что именно мешает: чинить преграды по одной,
                  каждый раз нажимая кнопку заново, — худший сценарий */}
              {plan?.ok && (
                <div className="mt-3">
                  <button
                    className="px-3 py-1.5 rounded-lg text-sm border border-emerald-700/70
                      text-emerald-300 hover:bg-emerald-950/40 transition disabled:opacity-50"
                    onClick={apply}
                    disabled={applying}
                  >
                    {applying ? 'Обновляю…' : 'Обновить и перезапустить'}
                  </button>
                  <p className="text-xs text-zinc-400 mt-2">
                    Сервер остановится, обновление применит отдельный процесс, затем
                    страница перезагрузится сама. Займёт до минуты.
                  </p>
                </div>
              )}

              {plan && !plan.ok && plan.blockers?.length > 0 && (
                <div className="mt-3 rounded-lg border border-zinc-800 bg-zinc-950 px-3 py-2">
                  <div className="text-zinc-400 mb-1">Обновить кнопкой сейчас нельзя:</div>
                  <ul className="list-disc pl-5 text-xs text-zinc-400 space-y-1">
                    {plan.blockers.map((b) => <li key={b}>{b}</li>)}
                  </ul>
                </div>
              )}

              {status.command ? (
                <div className="mt-3">
                  <div className="text-zinc-400 mb-1">
                    {plan?.ok ? 'Или вручную:' : 'Обновиться одной командой:'}
                  </div>
                  <div className="flex items-start gap-2 rounded-lg bg-zinc-950 border border-zinc-800 px-3 py-2">
                    <code className="flex-1 text-xs text-zinc-300 break-all">{status.command}</code>
                    <CopyButton text={status.command} title="Скопировать команду" />
                  </div>
                  <p className="text-xs text-zinc-400 mt-2">
                    Выполните её в папке инструмента и перезапустите сервер:
                    Настройки → «Сервер». Если git откажет — значит в копии есть
                    свои правки или коммиты; тогда обновляйтесь вручную, чтобы их не потерять.
                  </p>
                </div>
              ) : (
                <p className="mt-3 text-zinc-400">{INSTALL_HINT[status.install] || ''}</p>
              )}

              {latest.notes && (
                <div className="mt-4">
                  <div className="text-zinc-400 mb-1">Что изменилось:</div>
                  <div className="md-body md-tint-zinc text-sm rounded-lg border border-zinc-800 px-4 py-3">
                    <ReactMarkdown remarkPlugins={[remarkGfm]} components={mdComponents}>
                      {latest.notes}
                    </ReactMarkdown>
                  </div>
                </div>
              )}
            </div>
          )}

          {status?.error && (
            <div className="mt-4 text-xs">
              <div className="text-amber-400/80">Последняя проверка не удалась: {status.error}</div>
              {/* Адрес обязателен рядом с ошибкой: без него «404» неотличимо
                  от поломки инструмента, а адрес настраиваемый */}
              {status.url && (
                <div className="text-zinc-500 mt-1 break-all">Адрес проверки: {status.url}</div>
              )}
              <div className="text-zinc-500 mt-1">
                Не беспокойтесь, с доской всё в порядке — проверка обновлений
                независимая функция.
              </div>
            </div>
          )}
        </div>

        <div className="flex items-center gap-3 px-5 py-3 border-t border-zinc-800">
          <button className={btn} onClick={check} disabled={busy || status?.mode === 'off'}>
            {busy ? 'Проверяю…' : 'Проверить сейчас'}
          </button>
          {checked && <div className="text-xs text-zinc-500">последняя проверка: {checked}</div>}
        </div>
      </div>
    </div>
  )
}
