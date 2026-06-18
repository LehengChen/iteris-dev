import { Routes, Route, NavLink, Navigate } from 'react-router-dom';
import { LogViewer } from './views/LogViewer';
import { Overview } from './views/Overview';
import { FactGraph } from './views/FactGraph';
import { Evolve } from './views/Evolve';
import { Reports } from './views/Reports';
import { AnswerStatus } from './components/AnswerStatus';
import { useEvolve } from './hooks/useApi';

export default function App() {
  // The Evolve tab only exists for family roots (projects with EVOLVE.json).
  const evolveReady = useEvolve().data?.initialized === true;
  return (
    <div className="app">
      <header className="app-header">
        <span className="brand">Iteris</span>
        <nav>
          <NavLink to="/overview" className={({ isActive }) => (isActive ? 'active' : '')}>
            Overview
          </NavLink>
          <NavLink to="/facts" className={({ isActive }) => (isActive ? 'active' : '')}>
            Facts
          </NavLink>
          {evolveReady && (
            <NavLink to="/evolve" className={({ isActive }) => (isActive ? 'active' : '')}>
              Evolve
            </NavLink>
          )}
          <NavLink to="/reports" className={({ isActive }) => (isActive ? 'active' : '')}>
            Reports
          </NavLink>
          <NavLink to="/logs" className={({ isActive }) => (isActive ? 'active' : '')}>
            Logs
          </NavLink>
        </nav>
        <div className="header-spacer" />
        <AnswerStatus />
      </header>
      <main className="app-main">
        <Routes>
          <Route path="/overview" element={<Overview />} />
          <Route path="/facts" element={<FactGraph />} />
          <Route path="/evolve" element={<Evolve />} />
          <Route path="/reports" element={<Reports />} />
          <Route path="/logs" element={<LogViewer />} />
          <Route path="*" element={<Navigate to="/overview" replace />} />
        </Routes>
      </main>
    </div>
  );
}
