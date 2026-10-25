/** @type {import('tailwindcss').Config} */
export default {
  // Тесты компонентов не сканируются: слова из них (`container`) стали бы
  // классами в собранном CSS
  content: ['./index.html', './src/**/*.{js,jsx}', '!./src/**/*.test.{js,jsx}'],
  theme: {
    extend: {},
  },
  plugins: [],
}
