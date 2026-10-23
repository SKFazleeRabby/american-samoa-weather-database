import React from 'react';
import ReactDOM from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import App from './App';
import 'leaflet/dist/leaflet.css';
import './styles.css';
import './app.css';

const client = new QueryClient({ defaultOptions: { queries: { staleTime: 30_000, gcTime: 300_000, retry: 1, refetchOnWindowFocus: false } } });
ReactDOM.createRoot(document.getElementById('root')!).render(<React.StrictMode><QueryClientProvider client={client}><App /></QueryClientProvider></React.StrictMode>);
