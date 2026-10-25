// Якорь подраздела справки: `05-agentic.md#сценарии-до-кода`, `#обновление-и-правки`
// или адрес для кнопки «подробнее» — `agentic#сценарии-до-кода`.
//
// Якорь строится из текста заголовка так же, как его строит GitHub, — тогда одна
// ссылка ведёт к одному месту и в окне помощи, и в файле на GitHub

// GitHub: нижний регистр, всё кроме букв, цифр, пробела, дефиса и подчёркивания
// выбрасывается, каждый пробел — дефис (подряд идущие не схлопываются)
export function slugify(text) {
  return String(text || '')
    .trim()
    .toLowerCase()
    .replace(/[^\p{L}\p{M}\p{N}\p{Pc} -]/gu, '')
    .replace(/ /g, '-')
}

// Адрес места в справке: раздел и якорь. Пустой раздел — тот же, что открыт
export function parseHelpRef(ref) {
  const raw = String(ref || '')
  const at = raw.indexOf('#')
  const section = at < 0 ? raw : raw.slice(0, at)
  let anchor = at < 0 ? '' : raw.slice(at + 1)
  try {
    anchor = decodeURIComponent(anchor)
  } catch {
    // Битая %-последовательность — якорь как написан: не найдётся, раздел откроется сверху
  }
  return { section, anchor: anchor.toLowerCase() }
}
