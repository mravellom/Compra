import { Component, inject, signal, ViewChild } from '@angular/core';
import { RouterOutlet, RouterLink, RouterLinkActive } from '@angular/router';
import { NotificationToastComponent } from '../../../shared/components/notification-toast/notification-toast.component';
import { TutorialWalkthroughComponent } from '../../../shared/components/tutorial-walkthrough/tutorial-walkthrough.component';
import { OpportunityFacade } from '../../../application/facades/opportunity.facade';
import { HealthFacade } from '../../../application/facades/health.facade';

interface NavItem {
  path: string;
  label: string;
  icon: string;
}

@Component({
  selector: 'app-main-layout',
  standalone: true,
  imports: [RouterOutlet, RouterLink, RouterLinkActive, NotificationToastComponent, TutorialWalkthroughComponent],
  template: `
    <div class="min-h-screen bg-surface-900 flex">
      <!-- Sidebar Nav -->
      @if (sidebarOpen()) {
        <div class="fixed inset-0 z-20 bg-black/50 lg:hidden" (click)="sidebarOpen.set(false)"></div>
      }

      <nav class="fixed z-30 inset-y-0 left-0 w-60 bg-surface-800 border-r border-surface-600 flex flex-col transition-transform duration-200 lg:static"
           [class]="sidebarOpen() ? 'translate-x-0' : '-translate-x-full lg:translate-x-0'">
        <!-- Brand -->
        <div class="px-5 py-4 border-b border-surface-600">
          <button (click)="runScan()"
                  class="flex items-center gap-3 hover:opacity-80 active:scale-95 transition-all cursor-pointer w-full"
                  [disabled]="oppFacade.loading()"
                  title="Ejecutar scraping">
            <div class="w-8 h-8 rounded-lg flex items-center justify-center text-sm font-bold"
                 [class]="oppFacade.loading() ? 'bg-accent-blue/50 animate-pulse' : 'bg-accent-blue'">
              @if (oppFacade.loading()) {
                <svg class="w-4 h-4 animate-spin text-white" fill="none" viewBox="0 0 24 24">
                  <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle>
                  <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z"></path>
                </svg>
              } @else {
                R
              }
            </div>
            <span class="text-base font-semibold text-white">Radar</span>
          </button>
        </div>

        <!-- Nav Items -->
        <div class="flex-1 overflow-y-auto py-3 px-3 space-y-1">
          @for (item of navItems; track item.path) {
            <a [routerLink]="item.path"
               routerLinkActive="bg-accent-blue/10 text-accent-blue border-accent-blue/30"
               [routerLinkActiveOptions]="{ exact: item.path === '/' }"
               class="flex items-center gap-3 px-3 py-2.5 rounded-lg text-sm text-gray-400 hover:text-gray-200 hover:bg-surface-700 transition-colors border border-transparent"
               (click)="sidebarOpen.set(false)">
              <span class="text-base" [innerHTML]="item.icon"></span>
              <span>{{ item.label }}</span>
            </a>
          }
        </div>

        <!-- Status Footer -->
        <div class="px-4 py-3 border-t border-surface-600 space-y-2">
          <button (click)="openTutorial()"
                  class="flex items-center gap-2.5 w-full px-3 py-2 rounded-lg text-sm text-gray-400 hover:text-accent-blue hover:bg-accent-blue/10 transition-colors">
            <span class="w-5 h-5 rounded-full border border-current flex items-center justify-center text-xs font-bold">?</span>
            <span>Guia operativa</span>
          </button>
          <div class="flex items-center gap-2 text-xs text-gray-500">
            <span class="w-2 h-2 rounded-full animate-pulse"
                  [class]="healthStatus() === 'ok' ? 'bg-accent-green' : 'bg-accent-red'"></span>
            <span>{{ healthStatus() === 'ok' ? 'Sistema operativo' : 'Problema detectado' }}</span>
          </div>
        </div>
      </nav>

      <!-- Main Content -->
      <div class="flex-1 flex flex-col min-w-0">
        <!-- Top Bar (mobile) -->
        <header class="bg-surface-800 border-b border-surface-600 px-4 py-3 flex items-center justify-between lg:hidden">
          <button (click)="sidebarOpen.set(true)"
                  class="p-2 rounded-lg bg-surface-700 text-gray-400 hover:text-white">
            <svg class="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
              <path stroke-linecap="round" stroke-linejoin="round" d="M4 6h16M4 12h16M4 18h16" />
            </svg>
          </button>
          <span class="text-sm font-semibold text-white">Radar de Oportunidades</span>
          <span class="w-2 h-2 bg-accent-green rounded-full animate-pulse"></span>
        </header>

        <!-- Page Content -->
        <main class="flex-1 overflow-y-auto">
          <router-outlet />
        </main>
      </div>
    </div>

    <app-notification-toast />
    <app-tutorial-walkthrough />
  `,
})
export class MainLayoutComponent {
  readonly oppFacade = inject(OpportunityFacade);
  private readonly healthFacade = inject(HealthFacade);

  @ViewChild(TutorialWalkthroughComponent) tutorial!: TutorialWalkthroughComponent;

  readonly sidebarOpen = signal(false);

  readonly navItems: NavItem[] = [
    { path: '/', label: 'Dashboard', icon: '&#9632;' },
    { path: '/discovery', label: 'Discovery', icon: '&#9733;' },
    { path: '/trends', label: 'Tendencias', icon: '&#8593;' },
    { path: '/predictions', label: 'Predicciones', icon: '&#9684;' },
    { path: '/categories', label: 'Categorias', icon: '&#9638;' },
    { path: '/execution', label: 'Ejecucion', icon: '&#9654;' },
    { path: '/portfolio', label: 'Portfolio', icon: '&#9673;' },
    { path: '/orchestrator', label: 'AI Decisions', icon: '&#9881;' },
    { path: '/alerts', label: 'Alertas', icon: '&#9888;' },
    { path: '/health', label: 'Sistema', icon: '&#9829;' },
  ];

  healthStatus(): string {
    return this.healthFacade.health()?.status ?? 'ok';
  }

  runScan(): void {
    this.oppFacade.triggerScan();
  }

  openTutorial(): void {
    this.sidebarOpen.set(false);
    this.tutorial.open();
  }
}
