// Редактор маршрута: какие рекомендации каталога предлагаются «Сделать обязательным».
// Закрытой считается рекомендация, у которой на этапе есть объявленное требование
// того же смысла (проверка и её параметр) или с тем же служебным именем
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, expect, test } from 'vitest'
import PipelineEditor from './PipelineEditor'

afterEach(cleanup)

const VERIFIED = { id: 'verified', check: 'confirm', ask: 'проверку подтвердил человек' }
const RELEASE_TEXT = { id: 'release_text', check: 'section_present',
                       name: 'Изменение для пользователя', ask: 'тексты релиза написаны' }
const RELEASE_OK = { id: 'release_ok', check: 'field', name: 'release_ok',
                     ask: 'тексты релиза утверждены человеком' }

const stage = (key, label, recommends) => ({ key, label, section: label, color: 'orange', recommends })

function openStage(recommends, declared) {
  const pipeline = [stage('testing', 'Testing', recommends)]
  render(<PipelineEditor pipeline={pipeline} actions={{}} catalog={[]} sources={[]}
                         requires={{ testing: declared }} predicates={[]}
                         onChange={() => {}} />)
  fireEvent.click(screen.getByTitle('Что должно быть выполнено, чтобы уйти с этапа'))
}

// Строка рекомендации — та, где рядом с текстом стоит кнопка «Сделать обязательным»
const offered = () => screen.queryAllByRole('button', { name: 'Сделать обязательным' })
  .map((button) => button.parentElement.textContent)

test('своё подтверждение с другим именем закрывает рекомендацию', () => {
  openStage([VERIFIED], [{ id: 'qa_ok', check: 'confirm', ask: 'QA проверил' }])
  expect(offered()).toEqual([])
})

test('секция с другим регистром имени закрывает рекомендацию, остальные предлагаются', () => {
  openStage([RELEASE_TEXT, RELEASE_OK],
            [{ id: 'notes', check: 'section_present', name: 'изменение для пользователя', ask: 'есть' }])
  const rows = offered()
  expect(rows).toHaveLength(1)
  expect(rows[0]).toContain('тексты релиза утверждены человеком')
})

test('требование с тем же именем закрывает рекомендацию', () => {
  openStage([VERIFIED], [{ id: 'verified', check: 'field', name: 'qa', ask: 'другое' }])
  expect(offered()).toEqual([])
})

test('требование другого смысла рекомендацию не закрывает', () => {
  openStage([VERIFIED],
            [{ id: 'notes', check: 'section_filled', name: 'Комментарии', ask: 'заметки есть' }])
  const rows = offered()
  expect(rows).toHaveLength(1)
  expect(rows[0]).toContain('проверку подтвердил человек')
})
