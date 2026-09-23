// История переходов окна помощи — стек мест {section, scroll}, как у браузера.
// Место хранит высоту прокрутки: «назад» возвращает туда, откуда ушли, а не в
// начало раздела. Функции чистые и массив не правят: React сравнивает состояние
// по ссылке, и правка на месте не перерисовала бы стрелку

// Шаг из места `from` в раздел `to`. Тот же раздел — не шаг: стрелка, которая
// никуда не уводит, только сбивает
export function visit(trail, from, to) {
  if (!from?.section || from.section === to) return trail
  return [...trail, from]
}

// Шаг назад: {place, trail} — место, куда вернуться, и стек без него
export function back(trail) {
  if (!trail.length) return { place: null, trail }
  return { place: trail[trail.length - 1], trail: trail.slice(0, -1) }
}
