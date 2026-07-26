/** @type {import('tailwindcss').Config} */
export default {
  // Class-based dark mode: the `dark` class on <html> toggles it, controlled
  // by ThemeContext and persisted to localStorage.
  darkMode: 'class',
  content: ['./index.html', './src/**/*.{js,jsx}'],
  theme: {
    extend: {
      colors: {
        brand: {
          50: '#eef6ff',
          100: '#d9eaff',
          500: '#2f7df5',
          600: '#1f64d8',
          700: '#1a4faf',
        },
      },
    },
  },
  plugins: [],
};
