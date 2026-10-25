// Заведомо падающий тест компонента: test_frontend_components запускает его
// при VITEST_FIXTURES=1 и проверяет, что падение доходит до общего прогона.
// В обычный прогон `npm test` не входит
import { expect, test } from 'vitest'

test('заведомо падает', () => {
  expect(1).toBe(2)
})
