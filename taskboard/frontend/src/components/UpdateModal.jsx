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
// Само обновление отсюда не запускается: показываем готовую команду, которую
// пользователь выполняет сам. Кнопка «обновить и перезапустить» — следующий шаг.

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

// Что делать пользователю, у которого обновиться одной командой нельзя
const INSTALL_HINT = {
  nogit: 'Копия развёрнута из git, но сам git в PATH не найден — установите его ' +
    'или обновитесь вручную, скачав новую версию.',
  plain: 'Копия развёрнута из архива, а не из git — обновление одной командой ' +
    'недоступно: скачайте новую версию и распакуйте её поверх, сохранив папку ' +
    'taskboard/.venv (она пересоздастся сама, если её потерять).',
}

export default function UpdateModal({ onClose }) {
  const [status, setStatus] = useState(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)

  useEffect(() => {
    // Только чтение кэша: открытие окна само по себе запросов наружу не шлёт
    api.updateStatus().then(setStatus).catch((e) => setError(e.message))
  }, [])

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

  const latest = status?.latest
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
            <div className="text-xs text-zinc-600">установлена версия {status.version}</div>
          )}
          <button
            onClick={onClose}
            className="ml-auto text-zinc-500 hover:text-zinc-200 text-xl leading-none px-2"
            title="Закрыть (Esc)"
          >
            ×
          </button>
        </div>

        <div className="flex-1 overflow-y-auto px-5 py-4 text-sm text-zinc-300">
          {error && <div className="text-rose-400 mb-3">{error}</div>}
          {!status && !error && <div className="text-zinc-500">Загрузка…</div>}

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
              <p className="text-xs text-zinc-600 mt-2">
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
                {latest.date && <span className="text-zinc-500 text-sm ml-2">от {latest.date}</span>}
              </div>

              {status.command ? (
                <div className="mt-3">
                  <div className="text-zinc-400 mb-1">Обновиться одной командой:</div>
                  <div className="flex items-start gap-2 rounded-lg bg-zinc-950 border border-zinc-800 px-3 py-2">
                    <code className="flex-1 text-xs text-zinc-300 break-all">{status.command}</code>
                    <CopyButton text={status.command} title="Скопировать команду" />
                  </div>
                  <p className="text-xs text-zinc-500 mt-2">
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
                <div className="text-zinc-600 mt-1 break-all">Адрес проверки: {status.url}</div>
              )}
              <div className="text-zinc-600 mt-1">
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
          {checked && <div className="text-xs text-zinc-600">последняя проверка: {checked}</div>}
        </div>
      </div>
    </div>
  )
}
