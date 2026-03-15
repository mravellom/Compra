import { Component, inject, OnInit, OnDestroy, ViewChild } from '@angular/core';
import { OpportunityService } from '../../services/opportunity.service';
import { StatsBarComponent } from '../stats-bar/stats-bar.component';
import { SidebarComponent } from '../sidebar/sidebar.component';
import { OpportunityCardComponent } from '../opportunity-card/opportunity-card.component';
import { OpportunityDetailComponent } from '../opportunity-detail/opportunity-detail.component';

@Component({
  selector: 'app-dashboard',
  standalone: true,
  imports: [StatsBarComponent, SidebarComponent, OpportunityCardComponent, OpportunityDetailComponent],
  template: `
    <div class="flex h-[calc(100vh-53px)]">
      <!-- Sidebar -->
      <app-sidebar />

      <!-- Main content -->
      <main class="flex-1 overflow-y-auto p-6 space-y-6">
        <!-- Stats -->
        <app-stats-bar />

        <!-- Results header -->
        <div class="flex items-center justify-between">
          <div class="flex items-center gap-3">
            <!-- Hamburger button -->
            <button (click)="toggleSidebar()"
                    class="p-2 rounded-lg bg-surface-700 hover:bg-surface-600 text-gray-400 hover:text-white transition-colors"
                    title="Filtros">
              <svg class="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
                <path stroke-linecap="round" stroke-linejoin="round"
                      d="M3 4a1 1 0 0 1 1-1h16a1 1 0 0 1 1 1v2.586a1 1 0 0 1-.293.707l-6.414 6.414a1 1 0 0 0-.293.707V17l-4 4v-6.586a1 1 0 0 0-.293-.707L3.293 7.293A1 1 0 0 1 3 6.586V4z" />
              </svg>
            </button>
            <h2 class="text-lg font-semibold text-white">
              Oportunidades
              <span class="text-sm font-normal text-gray-500 ml-2">
                {{ svc.filteredOpportunities().length }} resultados
              </span>
            </h2>
          </div>
          <div class="flex gap-2 text-sm">
            <button (click)="sortBy = 'score'" class="px-3 py-1 rounded-lg transition-colors"
                    [class]="sortBy === 'score' ? 'bg-accent-blue/20 text-accent-blue' : 'text-gray-500 hover:text-gray-300'">
              Score
            </button>
            <button (click)="sortBy = 'roi'" class="px-3 py-1 rounded-lg transition-colors"
                    [class]="sortBy === 'roi' ? 'bg-accent-blue/20 text-accent-blue' : 'text-gray-500 hover:text-gray-300'">
              ROI
            </button>
            <button (click)="sortBy = 'profit'" class="px-3 py-1 rounded-lg transition-colors"
                    [class]="sortBy === 'profit' ? 'bg-accent-blue/20 text-accent-blue' : 'text-gray-500 hover:text-gray-300'">
              Profit
            </button>
            <button (click)="sortBy = 'recent'" class="px-3 py-1 rounded-lg transition-colors"
                    [class]="sortBy === 'recent' ? 'bg-accent-blue/20 text-accent-blue' : 'text-gray-500 hover:text-gray-300'">
              Recientes
            </button>
          </div>
        </div>

        <!-- Loading -->
        @if (svc.loading()) {
          <div class="flex items-center justify-center py-20">
            <div class="w-8 h-8 border-2 border-accent-blue/30 border-t-accent-blue rounded-full animate-spin"></div>
          </div>
        }

        <!-- Grid de oportunidades -->
        @if (!svc.loading()) {
          @if (sorted().length > 0) {
            <div class="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4">
              @for (opp of sorted(); track opp.id) {
                <app-opportunity-card [opp]="opp" (selected)="onSelect($event)" />
              }
            </div>
          } @else {
            <div class="text-center py-20">
              <p class="text-gray-600 text-lg">No se encontraron oportunidades</p>
              <p class="text-gray-700 text-sm mt-2">Ajusta los filtros o ejecuta un nuevo escaneo</p>
            </div>
          }
        }
      </main>
    </div>

    <!-- Detail panel -->
    @if (svc.selectedOpportunity()) {
      <app-opportunity-detail />
    }
  `,
})
export class DashboardComponent implements OnInit, OnDestroy {
  @ViewChild(SidebarComponent) sidebar!: SidebarComponent;

  svc = inject(OpportunityService);
  sortBy: 'score' | 'roi' | 'profit' | 'recent' = 'score';

  ngOnInit(): void {
    this.svc.loadOpportunities();
    this.svc.loadStats();
    this.svc.startPolling();
  }

  ngOnDestroy(): void {
    this.svc.stopPolling();
  }

  toggleSidebar(): void {
    this.sidebar.toggle();
  }

  onSelect(id: number): void {
    this.svc.selectOpportunity(id);
  }

  sorted() {
    const opps = [...this.svc.filteredOpportunities()];
    switch (this.sortBy) {
      case 'score':
        return opps.sort((a, b) => b.opportunity_score - a.opportunity_score);
      case 'roi':
        return opps.sort((a, b) => b.roi - a.roi);
      case 'profit':
        return opps.sort((a, b) => b.net_profit - a.net_profit);
      case 'recent':
        return opps.sort((a, b) => new Date(b.created_at).getTime() - new Date(a.created_at).getTime());
    }
  }
}
