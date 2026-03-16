import { Component, inject, OnInit, OnDestroy, signal, computed } from '@angular/core';
import { DecimalPipe, DatePipe } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { OpportunityFacade } from '../../../application/facades/opportunity.facade';
import { LoadingSpinnerComponent } from '../../../shared/components/loading-spinner/loading-spinner.component';
import { EmptyStateComponent } from '../../../shared/components/empty-state/empty-state.component';
import { StatCardComponent } from '../../../shared/components/stat-card/stat-card.component';
import { ScoreBadgeComponent } from '../../../shared/components/score-badge/score-badge.component';
import { ProgressBarComponent } from '../../../shared/components/progress-bar/progress-bar.component';
import { Opportunity, OpportunityDetail } from '../../../domain/models';

type SortKey = 'score' | 'roi' | 'profit' | 'recent';

const ROUTE_COLORS: Record<string, string> = {
  'MX→MX': '#3b82f6',
  'US→MX': '#10b981',
  'US→AR': '#8b5cf6',
  'US→CL': '#06b6d4',
  'US→CO': '#14b8a6',
  'CN→MX': '#f59e0b',
  'CN→AR': '#f97316',
  'CN→CL': '#eab308',
  'CN→CO': '#d97706',
  'MX→CL': '#ec4899',
  'MX→AR': '#e11d48',
  'MX→CO': '#db2777',
  'AR→MX': '#a855f7',
  'CL→MX': '#6366f1',
  'CO→MX': '#7c3aed',
};

@Component({
  selector: 'app-dashboard-page',
  standalone: true,
  imports: [DecimalPipe, DatePipe, FormsModule, LoadingSpinnerComponent, EmptyStateComponent, StatCardComponent, ScoreBadgeComponent, ProgressBarComponent],
  template: `
    <div class="flex h-full">
      <!-- Sidebar Filters -->
      @if (filtersOpen()) {
        <div class="fixed inset-0 z-20 bg-black/50 lg:hidden" (click)="filtersOpen.set(false)"></div>
      }
      <aside class="bg-surface-800 border-r border-surface-600 p-5 flex flex-col gap-6 overflow-y-auto
                     fixed z-30 inset-y-0 left-0 w-72 transition-transform duration-200 lg:static lg:z-auto"
             [class]="filtersOpen() ? 'translate-x-0' : '-translate-x-full lg:hidden'">
        <div class="flex items-center justify-between">
          <h2 class="text-sm font-semibold text-gray-400 uppercase tracking-wider">Filtros</h2>
          <button (click)="filtersOpen.set(false)"
                  class="text-gray-500 hover:text-white transition-colors text-xl leading-none lg:hidden">&times;</button>
        </div>
        <div>
          <label class="flex justify-between text-sm mb-2">
            <span class="text-gray-400">ROI Minimo</span>
            <span class="text-accent-green font-medium">{{ minRoi * 100 | number:'1.0-0' }}%</span>
          </label>
          <input type="range" [min]="0" [max]="1" [step]="0.05" [(ngModel)]="minRoi" (ngModelChange)="applyFilters()" />
        </div>
        <div>
          <label class="flex justify-between text-sm mb-2">
            <span class="text-gray-400">Precio max. compra</span>
            <span class="text-white font-medium">\${{ maxPrice | number:'1.0-0' }}</span>
          </label>
          <input type="range" [min]="0" [max]="2000" [step]="50" [(ngModel)]="maxPrice" (ngModelChange)="applyFilters()" />
        </div>
        <div>
          <p class="text-sm text-gray-400 mb-3">Marketplaces</p>
          <div class="flex flex-col gap-2">
            @for (mp of facade.availableMarketplaces(); track mp) {
              <label class="flex items-center gap-2.5 cursor-pointer group">
                <input type="checkbox" [checked]="selectedMps.has(mp)" (change)="toggleMp(mp)"
                       class="w-4 h-4 rounded bg-surface-700 border-surface-600 text-accent-blue focus:ring-accent-blue/30 focus:ring-offset-0" />
                <span class="text-sm text-gray-400 group-hover:text-gray-200 transition-colors capitalize">{{ mp }}</span>
              </label>
            }
          </div>
        </div>
        <div class="mt-auto pt-4 border-t border-surface-600">
          <button (click)="facade.triggerScan()" [disabled]="facade.loading()"
                  class="btn-primary w-full flex items-center justify-center gap-2 disabled:opacity-50">
            @if (facade.loading()) {
              <span class="w-4 h-4 border-2 border-white/30 border-t-white rounded-full animate-spin"></span>
              Escaneando...
            } @else {
              Escanear ahora
            }
          </button>
        </div>
      </aside>

      <!-- Main -->
      <div class="flex-1 overflow-y-auto p-6 space-y-6">
        <!-- Stats -->
        <div class="grid grid-cols-2 lg:grid-cols-4 gap-4">
          <app-stat-card label="Oportunidades" [displayValue]="facade.stats().total_opportunities.toString()" />
          <app-stat-card label="ROI Promedio"
                         [displayValue]="(facade.stats().avg_roi * 100 | number:'1.1-1') + '%'"
                         [valueColor]="roiColor(facade.stats().avg_roi)" />
          <app-stat-card label="Profit Promedio"
                         [displayValue]="'$' + (facade.stats().avg_profit | number:'1.2-2')"
                         valueColor="text-accent-green" />
          <app-stat-card label="Top Marketplace"
                         [displayValue]="facade.stats().top_marketplace || '—'"
                         valueColor="text-accent-blue" />
        </div>

        <!-- Results header -->
        <div class="flex items-center justify-between">
          <div class="flex items-center gap-3">
            <button (click)="filtersOpen.set(true)"
                    class="p-2 rounded-lg bg-surface-700 hover:bg-surface-600 text-gray-400 hover:text-white transition-colors">
              <svg class="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
                <path stroke-linecap="round" stroke-linejoin="round"
                      d="M3 4a1 1 0 0 1 1-1h16a1 1 0 0 1 1 1v2.586a1 1 0 0 1-.293.707l-6.414 6.414a1 1 0 0 0-.293.707V17l-4 4v-6.586a1 1 0 0 0-.293-.707L3.293 7.293A1 1 0 0 1 3 6.586V4z" />
              </svg>
            </button>
            <h2 class="text-lg font-semibold text-white">
              Oportunidades
              <span class="text-sm font-normal text-gray-500 ml-2">{{ sorted().length }} resultados</span>
            </h2>
          </div>
          <div class="flex gap-2 text-sm">
            @for (s of sortOptions; track s.key) {
              <button (click)="sortBy = s.key" class="px-3 py-1 rounded-lg transition-colors"
                      [class]="sortBy === s.key ? 'bg-accent-blue/20 text-accent-blue' : 'text-gray-500 hover:text-gray-300'">
                {{ s.label }}
              </button>
            }
          </div>
        </div>

        @if (facade.loading()) {
          <app-loading-spinner />
        }

        @if (!facade.loading()) {
          @if (sorted().length > 0) {
            <div class="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4">
              @for (opp of sorted(); track opp.id) {
                <div class="card flex flex-col gap-4 cursor-pointer border-l-4"
                     [style.border-left-color]="routeColor(opp)"
                     (click)="facade.selectOpportunity(opp.id)">
                  <div class="flex gap-4">
                    <div class="w-20 h-20 rounded-lg bg-surface-700 flex-shrink-0 overflow-hidden">
                      @if (opp.image_url) {
                        <img [src]="opp.image_url" [alt]="opp.product_name" class="w-full h-full object-cover" />
                      } @else {
                        <div class="w-full h-full flex items-center justify-center text-gray-600 text-2xl">?</div>
                      }
                    </div>
                    <div class="flex-1 min-w-0">
                      <h3 class="text-sm font-semibold text-white truncate">{{ opp.product_name }}</h3>
                      <p class="text-xs text-gray-500 mt-1">{{ opp.created_at | date:'short' }}</p>
                      <div class="flex items-center gap-2 mt-2">
                        <span class="inline-block w-2 h-2 rounded-full flex-shrink-0" [style.background-color]="routeColor(opp)"></span>
                        <span class="badge bg-surface-600 text-gray-300">{{ opp.buy_marketplace }}</span>
                        <span class="text-gray-600">&rarr;</span>
                        <span class="badge bg-surface-600 text-gray-300">{{ opp.sell_marketplace }}</span>
                        @if (opp.route) {
                          <span class="text-[10px] font-medium px-1.5 py-0.5 rounded-full border"
                                [style.border-color]="routeColor(opp)" [style.color]="routeColor(opp)">{{ opp.route }}</span>
                        }
                      </div>
                    </div>
                  </div>
                  <div class="grid grid-cols-2 gap-3">
                    <div class="bg-surface-700/50 rounded-lg p-3 text-center">
                      <p class="text-[10px] text-gray-500 uppercase">Compra</p>
                      <p class="text-lg font-bold text-white">\${{ opp.buy_price | number:'1.2-2' }}</p>
                    </div>
                    <div class="bg-surface-700/50 rounded-lg p-3 text-center">
                      <p class="text-[10px] text-gray-500 uppercase">Venta</p>
                      <p class="text-lg font-bold text-white">\${{ opp.sell_price | number:'1.2-2' }}</p>
                    </div>
                  </div>
                  <div class="flex items-center justify-between">
                    <div>
                      <p class="text-[10px] text-gray-500 uppercase">Profit neto</p>
                      <p class="text-base font-bold text-accent-green">\${{ opp.net_profit | number:'1.2-2' }}</p>
                    </div>
                    <div class="text-right">
                      <p class="text-[10px] text-gray-500 uppercase">ROI</p>
                      <p class="text-xl font-bold" [class]="roiColor(opp.roi)">{{ opp.roi * 100 | number:'1.1-1' }}%</p>
                    </div>
                  </div>
                  <div class="flex items-center justify-between">
                    <div class="flex items-center gap-2">
                      <app-score-badge [value]="opp.opportunity_score" />
                      <div>
                        <p class="text-[10px] text-gray-500 uppercase">Score</p>
                        <span class="text-xs font-medium px-1.5 py-0.5 rounded" [class]="confidenceClass(opp)">{{ opp.confidence_level }}</span>
                      </div>
                    </div>
                    <div class="text-right space-y-1">
                      <app-progress-bar label="Vel." [value]="opp.sales_velocity_score" color="blue" />
                      <app-progress-bar label="Comp." [value]="opp.competition_score" color="auto" />
                    </div>
                  </div>
                  <div class="flex gap-4 text-[11px] text-gray-500">
                    <span>Comisiones: \${{ opp.fees | number:'1.2-2' }}</span>
                    <span>Envio: \${{ opp.shipping_cost | number:'1.2-2' }}</span>
                    @if (opp.import_tax > 0) { <span>Tax: \${{ opp.import_tax | number:'1.2-2' }}</span> }
                  </div>
                  <div class="flex gap-2">
                    <a [href]="opp.buy_url" target="_blank" rel="noopener" (click)="$event.stopPropagation()"
                       class="btn-primary flex-1 text-center text-sm py-1.5">Comprar</a>
                    <a [href]="opp.sell_url" target="_blank" rel="noopener" (click)="$event.stopPropagation()"
                       class="flex-1 text-center text-sm py-1.5 bg-surface-600 hover:bg-surface-700 text-gray-300 rounded-lg transition-colors">Ver venta</a>
                  </div>
                </div>
              }
            </div>
          } @else {
            <app-empty-state message="No se encontraron oportunidades" subtitle="Ajusta los filtros o ejecuta un nuevo escaneo" />
          }
        }
      </div>
    </div>

    <!-- Detail panel -->
    @if (facade.selectedOpportunity(); as d) {
      <div class="fixed inset-0 z-40 bg-black/60 backdrop-blur-sm" (click)="facade.clearSelection()"></div>
      <div class="fixed inset-y-0 right-0 z-50 w-full max-w-2xl bg-surface-900 border-l border-surface-600 shadow-2xl overflow-y-auto">
        @if (facade.loadingDetail()) {
          <app-loading-spinner />
        }
        <div class="sticky top-0 bg-surface-900/95 backdrop-blur border-b border-surface-600 p-5 flex items-start gap-4">
          <div class="w-24 h-24 rounded-xl bg-surface-700 flex-shrink-0 overflow-hidden">
            @if (d.image_url) {
              <img [src]="d.image_url" [alt]="d.product_name" class="w-full h-full object-cover" />
            } @else {
              <div class="w-full h-full flex items-center justify-center text-gray-600 text-3xl">?</div>
            }
          </div>
          <div class="flex-1 min-w-0">
            <h2 class="text-lg font-bold text-white leading-tight">{{ d.product_name }}</h2>
            <div class="flex flex-wrap gap-2 mt-2">
              @if (d.brand) { <span class="badge bg-accent-blue/20 text-accent-blue">{{ d.brand }}</span> }
              @if (d.category) { <span class="badge bg-surface-600 text-gray-300">{{ d.category }}</span> }
              <span class="badge bg-surface-600 text-gray-400">{{ d.created_at | date:'medium' }}</span>
            </div>
          </div>
          <button (click)="facade.clearSelection()" class="text-gray-500 hover:text-white transition-colors text-2xl leading-none p-1">&times;</button>
        </div>
        <div class="p-5 space-y-6">
          <div class="grid grid-cols-3 gap-3">
            <div class="bg-surface-800 rounded-xl p-4 text-center border border-surface-600">
              <p class="text-[10px] text-gray-500 uppercase">Compra</p>
              <p class="text-xl font-bold text-white mt-1">\${{ d.buy_price | number:'1.2-2' }}</p>
              <p class="text-xs text-gray-500 mt-1">{{ d.buy_marketplace }}</p>
            </div>
            <div class="bg-surface-800 rounded-xl p-4 text-center border border-surface-600">
              <p class="text-[10px] text-gray-500 uppercase">Venta</p>
              <p class="text-xl font-bold text-white mt-1">\${{ d.sell_price | number:'1.2-2' }}</p>
              <p class="text-xs text-gray-500 mt-1">{{ d.sell_marketplace }}</p>
            </div>
            <div class="bg-surface-800 rounded-xl p-4 text-center border border-accent-green/30">
              <p class="text-[10px] text-gray-500 uppercase">Profit</p>
              <p class="text-xl font-bold text-accent-green mt-1">\${{ d.net_profit | number:'1.2-2' }}</p>
              <p class="text-xs font-semibold mt-1" [class]="roiColor(d.roi)">ROI {{ d.roi * 100 | number:'1.1-1' }}%</p>
            </div>
          </div>
          <div class="bg-surface-800 rounded-xl border border-surface-600 p-4">
            <h3 class="text-sm font-semibold text-white mb-3">Desglose de costos</h3>
            <div class="space-y-2 text-sm">
              <div class="flex justify-between"><span class="text-gray-400">Precio de compra</span><span class="text-white">\${{ d.buy_price | number:'1.2-2' }}</span></div>
              <div class="flex justify-between"><span class="text-gray-400">Comisiones marketplace</span><span class="text-accent-red">-\${{ d.fees | number:'1.2-2' }}</span></div>
              <div class="flex justify-between"><span class="text-gray-400">Costo de envio</span><span class="text-accent-red">-\${{ d.shipping_cost | number:'1.2-2' }}</span></div>
              <div class="border-t border-surface-600 pt-2 flex justify-between font-semibold">
                <span class="text-gray-300">Ganancia neta</span><span class="text-accent-green">\${{ d.net_profit | number:'1.2-2' }}</span>
              </div>
            </div>
          </div>
          <div class="flex gap-3">
            <a [href]="d.buy_url" target="_blank" rel="noopener" class="btn-primary flex-1 text-center">Comprar en {{ d.buy_marketplace }}</a>
            <a [href]="d.sell_url" target="_blank" rel="noopener"
               class="flex-1 text-center py-2 px-4 bg-surface-600 hover:bg-surface-700 text-gray-300 rounded-lg transition-colors font-medium">Ver en {{ d.sell_marketplace }}</a>
          </div>
          @if (d.related_listings.length > 0) {
            <div>
              <h3 class="text-sm font-semibold text-white mb-3">Listings del mismo producto <span class="text-gray-500 font-normal ml-1">({{ d.related_listings.length }})</span></h3>
              <div class="space-y-2">
                @for (listing of d.related_listings; track listing.id) {
                  <a [href]="listing.url" target="_blank" rel="noopener"
                     class="flex items-center gap-3 bg-surface-800 rounded-lg border border-surface-600 p-3 hover:border-accent-blue/40 transition-colors">
                    <div class="w-10 h-10 rounded bg-surface-700 flex-shrink-0 overflow-hidden">
                      @if (listing.image_url) { <img [src]="listing.image_url" class="w-full h-full object-cover" /> }
                      @else { <div class="w-full h-full flex items-center justify-center text-gray-600 text-xs">?</div> }
                    </div>
                    <div class="flex-1 min-w-0">
                      <p class="text-xs text-gray-300 truncate">{{ listing.title }}</p>
                      <div class="flex items-center gap-2 mt-0.5">
                        <span class="text-[10px] text-gray-500">{{ listing.marketplace_id }}</span>
                        @if (listing.similarity_score) { <span class="text-[10px] text-gray-600">sim: {{ listing.similarity_score | number:'1.2-2' }}</span> }
                      </div>
                    </div>
                    <div class="text-right flex-shrink-0">
                      <p class="text-sm font-bold text-white">\${{ listing.price | number:'1.2-2' }}</p>
                      <p class="text-[10px] text-gray-500">{{ listing.currency }}</p>
                    </div>
                  </a>
                }
              </div>
            </div>
          }
        </div>
      </div>
    }
  `,
})
export class DashboardPageComponent implements OnInit, OnDestroy {
  readonly facade = inject(OpportunityFacade);

  readonly filtersOpen = signal(false);
  sortBy: SortKey = 'score';
  minRoi = 0;
  maxPrice = 1000;
  selectedMps = new Set<string>();

  readonly sortOptions: { key: SortKey; label: string }[] = [
    { key: 'score', label: 'Score' },
    { key: 'roi', label: 'ROI' },
    { key: 'profit', label: 'Profit' },
    { key: 'recent', label: 'Recientes' },
  ];

  ngOnInit(): void {
    this.facade.loadOpportunities();
    this.facade.loadStats();
    this.facade.startPolling();
  }

  ngOnDestroy(): void {
    this.facade.stopPolling();
  }

  sorted(): Opportunity[] {
    const opps = [...this.facade.filteredOpportunities()];
    switch (this.sortBy) {
      case 'score': return opps.sort((a, b) => b.opportunity_score - a.opportunity_score);
      case 'roi': return opps.sort((a, b) => b.roi - a.roi);
      case 'profit': return opps.sort((a, b) => b.net_profit - a.net_profit);
      case 'recent': return opps.sort((a, b) => new Date(b.created_at).getTime() - new Date(a.created_at).getTime());
    }
  }

  routeColor(opp: Opportunity): string {
    return ROUTE_COLORS[opp.route || ''] || '#6b7280';
  }

  roiColor(roi: number): string {
    if (roi >= 0.5) return 'text-accent-green';
    if (roi >= 0.25) return 'text-accent-yellow';
    return 'text-accent-red';
  }

  confidenceClass(opp: Opportunity): string {
    switch (opp.confidence_level) {
      case 'high': return 'bg-accent-green/20 text-accent-green';
      case 'medium': return 'bg-accent-yellow/20 text-accent-yellow';
      default: return 'bg-gray-700 text-gray-400';
    }
  }

  toggleMp(mp: string): void {
    if (this.selectedMps.has(mp)) this.selectedMps.delete(mp);
    else this.selectedMps.add(mp);
    this.applyFilters();
  }

  applyFilters(): void {
    this.facade.updateFilters({
      min_roi: this.minRoi,
      max_buy_price: this.maxPrice,
      marketplaces: Array.from(this.selectedMps),
    });
  }
}
