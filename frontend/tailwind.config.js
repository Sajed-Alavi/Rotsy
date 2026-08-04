/** @type {import('tailwindcss').Config} */
export default {
  // Class-based dark mode: the `dark` class on <html> toggles it, controlled
  // by ThemeContext and persisted to localStorage.
  darkMode: 'class',
  content: ['./index.html', './src/**/*.{js,jsx}'],
  theme: {
    extend: {
      colors: {
        // The logo's palette (see components/Logo.jsx): a violet arrow over cyan
        // cubes on a deep navy shield. Reserved for brand chrome — the working UI
        // accent stays `sky`, which index.css explains as the deliberate
        // "tooling, not marketing" choice.
        brand: {
          50: '#f5f3ff',
          100: '#ede9fe',
          400: '#a855f7',
          500: '#8b5cf6',
          600: '#7c3aed',
          700: '#6d28d9',
          900: '#1e3a8a',
          950: '#0a1230',
        },
      },
    },
  },
  plugins: [],
};
