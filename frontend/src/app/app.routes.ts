import { Routes } from '@angular/router';
import { MainLayoutComponent } from './presentation/layouts/main-layout/main-layout.component';

export const routes: Routes = [
  {
    path: '',
    component: MainLayoutComponent,
    children: [
      {
        path: '',
        loadComponent: () =>
          import('./presentation/pages/dashboard/dashboard-page.component').then(
            (m) => m.DashboardPageComponent,
          ),
      },
      {
        path: 'discovery',
        loadComponent: () =>
          import('./presentation/pages/discovery/discovery-page.component').then(
            (m) => m.DiscoveryPageComponent,
          ),
      },
      {
        path: 'trends',
        loadComponent: () =>
          import('./presentation/pages/trends/trends-page.component').then(
            (m) => m.TrendsPageComponent,
          ),
      },
      {
        path: 'predictions',
        loadComponent: () =>
          import('./presentation/pages/predictions/predictions-page.component').then(
            (m) => m.PredictionsPageComponent,
          ),
      },
      {
        path: 'categories',
        loadComponent: () =>
          import('./presentation/pages/categories/categories-page.component').then(
            (m) => m.CategoriesPageComponent,
          ),
      },
      {
        path: 'execution',
        loadComponent: () =>
          import('./presentation/pages/execution/execution-page.component').then(
            (m) => m.ExecutionPageComponent,
          ),
      },
      {
        path: 'portfolio',
        loadComponent: () =>
          import('./presentation/pages/portfolio/portfolio-page.component').then(
            (m) => m.PortfolioPageComponent,
          ),
      },
      {
        path: 'orchestrator',
        loadComponent: () =>
          import('./presentation/pages/orchestrator/orchestrator-page.component').then(
            (m) => m.OrchestratorPageComponent,
          ),
      },
      {
        path: 'alerts',
        loadComponent: () =>
          import('./presentation/pages/alerts/alerts-page.component').then(
            (m) => m.AlertsPageComponent,
          ),
      },
      {
        path: 'health',
        loadComponent: () =>
          import('./presentation/pages/health/health-page.component').then(
            (m) => m.HealthPageComponent,
          ),
      },
    ],
  },
  { path: '**', redirectTo: '' },
];
