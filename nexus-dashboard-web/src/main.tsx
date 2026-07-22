import React from 'react'
import ReactDOM from 'react-dom/client'
import { RouterProvider } from 'react-router-dom'
import { ThemeProvider } from 'next-themes'
import { ErrorBoundary } from './components/ErrorBoundary'
import { router } from './router'
import { installChunkErrorReload } from './lib/chunk-reload'
import './index.css'

// Recover automatically when a deploy invalidates the chunk an open tab needs.
installChunkErrorReload()

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <ErrorBoundary>
      <ThemeProvider attribute="class" defaultTheme="dark">
        <RouterProvider router={router} />
      </ThemeProvider>
    </ErrorBoundary>
  </React.StrictMode>,
)
