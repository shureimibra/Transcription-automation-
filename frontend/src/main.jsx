import React from 'react'
import ReactDOM from 'react-dom/client'
import './index.css'
import { TASKER_BASE } from './tasker/brand.js'

// TWO APPLICATIONS, ONE PAGE.
//
// Outside Eagle taskers get tasker/DeskApp.jsx; everyone else gets App.jsx.
// They are loaded as separate bundles, chosen by address, so a tasker's
// browser never downloads the worker, admin or studio screens. One
// index.html serves both, which is why the server needs no new rule.
const path = window.location.pathname
const isDesk = path === TASKER_BASE || path.startsWith(`${TASKER_BASE}/`)

// Each app's own tab icon and browser colour, set before anything draws.
document.getElementById('app-icon')?.setAttribute('href', isDesk ? '/desk-icon.svg' : '/icon.svg')
if (isDesk) document.querySelector('meta[name="theme-color"]')?.setAttribute('content', '#17151A')

const load = isDesk ? import('./tasker/DeskApp.jsx') : import('./App.jsx')

load.then(({ default: Root }) => {
  ReactDOM.createRoot(document.getElementById('root')).render(
    <React.StrictMode>
      <Root />
    </React.StrictMode>,
  )
})
