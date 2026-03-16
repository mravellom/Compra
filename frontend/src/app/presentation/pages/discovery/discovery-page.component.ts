import { Component, inject, OnInit, signal } from '@angular/core';
import { DecimalPipe, DatePipe } from '@angular/common';
import { DiscoveryFacade } from '../../../application/facades/discovery.facade';
import { LoadingSpinnerComponent } from '../../../shared/components/loading-spinner/loading-spinner.component';
import { StatCardComponent } from '../../../shared/components/stat-card/stat-card.component';
import { EmptyStateComponent } from '../../../shared/components/empty-state/empty-state.component';
import { TrendingProduct, NewProduct, ProductHistory } from '../../../domain/models';

@Component({
  selector: 'app-discovery-page',
  standalone: true,
  imports: [DecimalPipe, DatePipe, LoadingSpinnerComponent, StatCardComponent, EmptyStateComponent],
  template: `
    <div class="p-6 space-y-6">
      <div class="flex items-center justify-between">
        <h1 class="text-xl font-bold text-white">Discovery</h1>
        <div class="flex gap-2">
          <button (click)="activeTab.set('trending')" class="px-4 py-2 rounded-lg text-sm transition-colors"
                  [class]="activeTab() === 'trending' ? 'bg-accent-blue/20 text-accent-blue' : 'text-gray-500 hover:text-gray-300'">
            Trending
          </button>
          <button (click)="activeTab.set('new')" class="px-4 py-2 rounded-lg text-sm transition-colors"
                  [class]="activeTab() === 'new' ? 'bg-accent-blue/20 text-accent-blue' : 'text-gray-500 hover:text-gray-300'">
            Nuevos
          </button>
        </div>
      </div>

      <!-- Stats -->
      @if (facade.stats(); as s) {
        <div class="grid grid-cols-2 lg:grid-cols-5 gap-4">
          <app-stat-card label="Total Productos" [displayValue]="s.total_products.toString()" />
          <app-stat-card label="Trending" [displayValue]="s.trending_count.toString()" valueColor="text-accent-green" />
          <app-stat-card label="Nuevos (7d)" [displayValue]="s.new_last_7d.toString()" valueColor="text-accent-blue" />
          <app-stat-card label="Avg Listings" [displayValue]="(s.avg_listings_per_product | number:'1.1-1') || '0'" />
          <app-stat-card label="Avg Marketplaces" [displayValue]="(s.avg_marketplaces_per_product | number:'1.1-1') || '0'" />
        </div>
      }

      <!-- Top Categories -->
      @if (facade.stats()?.top_categories?.length) {
        <div class="flex gap-2 flex-wrap">
          @for (cat of facade.stats()!.top_categories; track cat.category) {
            <button (click)="filterByCategory(cat.category)"
                    class="badge bg-surface-700 text-gray-300 hover:bg-surface-600 cursor-pointer transition-colors">
              {{ cat.category }} ({{ cat.product_count }})
            </button>
          }
        </div>
      }

      @if (facade.loading()) { <app-loading-spinner /> }

      <!-- Trending Tab -->
      @if (activeTab() === 'trending' && !facade.loading()) {
        @if (facade.trending().length === 0) {
          <app-empty-state message="Sin productos trending" subtitle="Ejecuta un escaneo para detectar tendencias" />
        } @else {
          <div class="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4">
            @for (p of facade.trending(); track p.product_id) {
              <div class="card cursor-pointer" (click)="selectProduct(p.product_id)">
                <div class="flex items-start justify-between mb-3">
                  <h3 class="text-sm font-semibold text-white truncate flex-1">{{ p.product_name }}</h3>
                  <span class="badge ml-2" [class]="trendBadgeClass(p.trend_label)">{{ p.trend_label }}</span>
                </div>
                <div class="grid grid-cols-3 gap-2 text-center mb-3">
                  <div>
                    <p class="text-[10px] text-gray-500 uppercase">Precio</p>
                    <p class="text-sm font-bold text-white">\${{ p.avg_price | number:'1.2-2' }}</p>
                  </div>
                  <div>
                    <p class="text-[10px] text-gray-500 uppercase">Listings</p>
                    <p class="text-sm font-bold text-white">{{ p.listing_count }}</p>
                  </div>
                  <div>
                    <p class="text-[10px] text-gray-500 uppercase">Sellers</p>
                    <p class="text-sm font-bold text-white">{{ p.seller_count }}</p>
                  </div>
                </div>
                <div class="flex items-center justify-between text-xs">
                  <span class="text-gray-500">Trend Score</span>
                  <div class="flex items-center gap-2">
                    <div class="w-20 h-1.5 bg-surface-600 rounded-full overflow-hidden">
                      <div class="h-full bg-accent-blue rounded-full" [style.width.%]="p.trend_score"></div>
                    </div>
                    <span class="text-accent-blue font-medium">{{ p.trend_score | number:'1.0-0' }}</span>
                  </div>
                </div>
                @if (p.brand || p.category) {
                  <div class="flex gap-2 mt-2">
                    @if (p.brand) { <span class="text-[10px] text-gray-600">{{ p.brand }}</span> }
                    @if (p.category) { <span class="text-[10px] text-gray-600">{{ p.category }}</span> }
                  </div>
                }
                <div class="flex justify-between text-[10px] text-gray-600 mt-2">
                  <span>Vel 7d: {{ p.velocity_7d | number:'1.1-1' }}</span>
                  <span>Vel 30d: {{ p.velocity_30d | number:'1.1-1' }}</span>
                  <span>{{ p.marketplace_count }} markets</span>
                </div>
              </div>
            }
          </div>
        }
      }

      <!-- New Products Tab -->
      @if (activeTab() === 'new' && !facade.loading()) {
        @if (facade.newProducts().length === 0) {
          <app-empty-state message="Sin productos nuevos" />
        } @else {
          <div class="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4">
            @for (p of facade.newProducts(); track p.product_id) {
              <div class="card">
                <h3 class="text-sm font-semibold text-white truncate mb-2">{{ p.product_name }}</h3>
                <div class="grid grid-cols-3 gap-2 text-center mb-3">
                  <div>
                    <p class="text-[10px] text-gray-500 uppercase">Precio</p>
                    <p class="text-sm font-bold text-white">\${{ p.avg_price | number:'1.2-2' }}</p>
                  </div>
                  <div>
                    <p class="text-[10px] text-gray-500 uppercase">Listings</p>
                    <p class="text-sm font-bold text-white">{{ p.listing_count }}</p>
                  </div>
                  <div>
                    <p class="text-[10px] text-gray-500 uppercase">Markets</p>
                    <p class="text-sm font-bold text-white">{{ p.marketplace_count }}</p>
                  </div>
                </div>
                @if (p.first_seen_at) {
                  <p class="text-[10px] text-gray-600">Visto: {{ p.first_seen_at | date:'short' }}</p>
                }
              </div>
            }
          </div>
        }
      }

      <!-- Product History Panel -->
      @if (selectedProductId() && facade.productHistory().length > 0) {
        <div class="bg-surface-800 rounded-xl border border-surface-600 p-5">
          <div class="flex items-center justify-between mb-4">
            <h3 class="text-sm font-semibold text-white">Historial de Precios</h3>
            <button (click)="selectedProductId.set(null)" class="text-gray-500 hover:text-white text-lg">&times;</button>
          </div>
          @if (facade.loadingHistory()) { <app-loading-spinner size="sm" containerClass="py-8" /> }
          @else {
            <div class="overflow-x-auto">
              <table class="w-full text-sm">
                <thead>
                  <tr class="text-gray-500 text-xs uppercase">
                    <th class="text-left py-2">Fecha</th>
                    <th class="text-right py-2">Avg</th>
                    <th class="text-right py-2">Min</th>
                    <th class="text-right py-2">Max</th>
                    <th class="text-right py-2">Listings</th>
                    <th class="text-right py-2">Sellers</th>
                  </tr>
                </thead>
                <tbody>
                  @for (h of facade.productHistory(); track h.date) {
                    <tr class="border-t border-surface-600">
                      <td class="py-2 text-gray-400">{{ h.date }}</td>
                      <td class="py-2 text-right text-white">\${{ h.avg_price | number:'1.2-2' }}</td>
                      <td class="py-2 text-right text-accent-green">\${{ h.min_price | number:'1.2-2' }}</td>
                      <td class="py-2 text-right text-accent-red">\${{ h.max_price | number:'1.2-2' }}</td>
                      <td class="py-2 text-right text-gray-400">{{ h.listing_count }}</td>
                      <td class="py-2 text-right text-gray-400">{{ h.seller_count }}</td>
                    </tr>
                  }
                </tbody>
              </table>
            </div>
          }
        </div>
      }
    </div>
  `,
})
export class DiscoveryPageComponent implements OnInit {
  readonly facade = inject(DiscoveryFacade);
  readonly activeTab = signal<'trending' | 'new'>('trending');
  readonly selectedProductId = signal<number | null>(null);

  ngOnInit(): void {
    this.facade.loadTrending();
    this.facade.loadNew();
    this.facade.loadStats();
  }

  trendBadgeClass(label: string): string {
    switch (label) {
      case 'rising': return 'bg-accent-green/20 text-accent-green';
      case 'falling': return 'bg-accent-red/20 text-accent-red';
      default: return 'bg-surface-600 text-gray-400';
    }
  }

  filterByCategory(category: string): void {
    this.facade.loadTrending(20, category);
  }

  selectProduct(productId: number): void {
    this.selectedProductId.set(productId);
    this.facade.loadProductHistory(productId);
  }
}
