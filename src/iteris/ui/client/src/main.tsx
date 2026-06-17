import React from 'react';
import ReactDOM from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { BrowserRouter } from 'react-router-dom';
import App from './App';
import './styles/index.css';

const queryClient = new QueryClient({
  // Polling hooks own their refetch cadence (see hooks/useApi.ts); a short
  // staleTime keeps mount/focus refetches honest without fighting the poll,
  // and retry stays off because the next poll is the retry.
  defaultOptions: { queries: { staleTime: 2000, retry: 0 } },
});

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <App />
      </BrowserRouter>
    </QueryClientProvider>
  </React.StrictMode>,
);
